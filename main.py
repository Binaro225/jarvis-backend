"""
Backend Python leger - FastAPI multi-fournisseurs LLM (gratuit) + Function Calling
Fournisseurs supportes : Gemini, Groq, Mistral, OpenRouter, Grok
Outils (tools) : connecteur Google Apps Script (Drive / Docs / Sheets / Gmail / Calendar)
Recherche web : Tavily (temps reel)
Deploiement : Render (voir Procfile) - sert aussi le frontend (index.html) directement.

MEMOIRE :
- Court terme : historique de conversation conserve cote serveur, par session_id, dans
  SESSION_HISTORIES (in-memory). Conserve les SESSION_HISTORY_MAX_MESSAGES derniers messages
  (par defaut 14, soit ~7 echanges user/assistant). Fusionne avec l'historique eventuellement
  envoye par le frontend, pour ne jamais perdre le fil meme si le frontend ne renvoie rien.
- Long terme : fichier local user_memory.json a la racine du projet (avec migration
  automatique depuis l'ancien memory.json s'il existe), injecte dans le system prompt a
  chaque requete /chat, et mis a jour automatiquement via deux court-circuits complementaires
  (voir detect_memory_shortcut() et total_recall.extract_memory_content()), en plus de
  l'ecriture existante vers le connecteur Drive (remember_note).

GALAXIE 3D :
- L'endpoint GET /api/galaxy alimente le rendu Three.js du frontend : chaque fichier Google
  Drive devient un noeud (planete si Google Sheets, etoile sinon). Depuis cette version, la
  liste des fichiers est obtenue via le connecteur Apps Script (connector.get_drive_graph),
  exactement comme tous les autres outils Drive/Docs/Sheets/Gmail/Calendar - donc plus besoin
  d'un compte de service Google separe ni de credentials supplementaires a gerer.
"""

import os
import re
import json
import httpx
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, model_validator

import tools
import persona
import total_recall
import model_discovery
import connector

# ----------------------------------------------------------------------------
# Configuration des fournisseurs : uniquement les infos de connexion (cle API, famille
# d'API, URL de base). AUCUN nom de modele n'est code en dur ici : les modeles disponibles
# sont decouverts dynamiquement au runtime via model_discovery.py (GET /api/models).
# ----------------------------------------------------------------------------

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "gemini": {
        "key_env": "GEMINI_API_KEY",
        "family": "gemini",
    },
    "groq": {
        "key_env": "GROQ_API_KEY",
        "family": "openai_compatible",
        "base_url": "https://api.groq.com/openai/v1",
    },
    "mistral": {
        "key_env": "MISTRAL_API_KEY",
        "family": "openai_compatible",
        "base_url": "https://api.mistral.ai/v1",
    },
    "openrouter": {
        "key_env": "OPENROUTER_API_KEY",
        "family": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "extra_headers": {
            "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://render.com"),
            "X-Title": os.getenv("OPENROUTER_SITE_NAME", "Multi-LLM Backend"),
        },
    },
    "grok": {
        "key_env": "XAI_API_KEY",
        "family": "openai_compatible",
        "base_url": "https://api.x.ai/v1",
    },
}

MAX_TOOL_ITERATIONS = 5  # garde-fou contre les boucles d'appels d'outils infinies
BASE_DIR = Path(__file__).resolve().parent

# Alias tolerants : certains frontends nomment les fournisseurs differemment de nos cles
# internes (ex: "google" au lieu de "gemini", "xai" au lieu de "grok"). On normalise avant
# toute recherche dans PROVIDERS pour eviter une erreur "Fournisseur inconnu" evitable.
PROVIDER_ALIASES: Dict[str, str] = {
    "google": "gemini",
    "google-gemini": "gemini",
    "xai": "grok",
    "x-ai": "grok",
}


def normalize_provider(name: Optional[str]) -> Optional[str]:
    if not name:
        return name
    key = name.lower().strip()
    return PROVIDER_ALIASES.get(key, key)

# Etat global : le "cerveau" actuellement actif. Le modele n'est plus fige au demarrage : il
# est resolu dynamiquement (meilleur modele disponible pour ce provider) des la premiere requete.
current_brain: Dict[str, Optional[str]] = {
    "provider": os.getenv("DEFAULT_PROVIDER", "gemini"),
    "model": os.getenv("DEFAULT_MODEL") or None,
}


async def ensure_current_brain_model() -> str:
    """Resout le modele actif s'il n'est pas encore connu, via decouverte dynamique."""
    if not current_brain.get("model"):
        current_brain["model"] = await model_discovery.get_default_model_for_provider(current_brain["provider"])
    return current_brain["model"]


# ----------------------------------------------------------------------------
# MEMOIRE COURT TERME : historique de conversation par session (cote serveur)
# ----------------------------------------------------------------------------
# Cle = session_id (fourni par le frontend, ou "default" si absent).
# Valeur = liste de dicts {"role": "user"|"assistant", "content": str}, la plus recente en fin.
# Un verrou simple protege les acces concurrents (FastAPI peut traiter plusieurs requetes
# en parallele sur des sessions differentes, voire la meme session en cas de double-clic).
SESSION_HISTORIES: Dict[str, List[Dict[str, str]]] = {}
SESSION_HISTORY_LOCK = threading.Lock()

# Nombre max de MESSAGES (pas d'echanges) conserves par session. Par defaut 14 messages
# = ~7 echanges user/assistant, conformement au besoin de "10 a 15 derniers messages".
# Configurable via la variable d'environnement SESSION_HISTORY_MAX_MESSAGES si besoin.
MAX_SESSION_MESSAGES = int(os.getenv("SESSION_HISTORY_MAX_MESSAGES", "14"))


def get_session_history(session_id: str) -> List[Dict[str, str]]:
    with SESSION_HISTORY_LOCK:
        return list(SESSION_HISTORIES.get(session_id, []))


def append_session_turn(session_id: str, user_content: str, assistant_content: str) -> None:
    """Ajoute l'echange (question utilisateur + reponse assistant) a l'historique de la
    session, puis tronque pour ne garder que les MAX_SESSION_MESSAGES derniers messages."""
    with SESSION_HISTORY_LOCK:
        history = SESSION_HISTORIES.setdefault(session_id, [])
        history.append({"role": "user", "content": user_content})
        history.append({"role": "assistant", "content": assistant_content})
        if len(history) > MAX_SESSION_MESSAGES:
            del history[: len(history) - MAX_SESSION_MESSAGES]


def clear_session_history(session_id: str) -> None:
    with SESSION_HISTORY_LOCK:
        SESSION_HISTORIES.pop(session_id, None)


def merge_history(
    session_id: str, client_history: Optional[List["HistoryMessage"]]
) -> List["HistoryMessage"]:
    """Determine l'historique effectif a envoyer au LLM pour cette requete.

    Priorite a l'historique conserve cote serveur (source de verite, jamais perdu). Si la
    session est vide cote serveur (premier appel apres redemarrage, par ex.) mais que le
    frontend a transmis un historique, on l'utilise pour amorcer la session - on ne perd
    ainsi jamais le fil, meme si le frontend "oublie" de renvoyer l'historique par la suite.
    """
    server_history = get_session_history(session_id)
    if server_history:
        return [HistoryMessage(role=m["role"], content=m["content"]) for m in server_history]
    if client_history:
        return client_history
    return []


# ----------------------------------------------------------------------------
# MEMOIRE LONG TERME : fichier local user_memory.json (faits persistants sur l'utilisateur)
# ----------------------------------------------------------------------------
# Renomme depuis memory.json -> user_memory.json pour refleter son role de "profil
# utilisateur". Migration automatique et transparente de l'ancien fichier s'il existe deja,
# pour ne perdre aucun fait deja memorise lors du passage a cette version.
LONG_TERM_MEMORY_FILE = BASE_DIR / "user_memory.json"
_LEGACY_MEMORY_FILE = BASE_DIR / "memory.json"
LONG_TERM_MEMORY_LOCK = threading.Lock()


def _read_memory_file() -> List[Dict[str, str]]:
    if LONG_TERM_MEMORY_FILE.exists():
        try:
            with open(LONG_TERM_MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    # Fichier introuvable : on tente une migration depuis l'ancien memory.json, une seule
    # fois, pour ne jamais perdre les faits deja memorises par l'utilisateur.
    if _LEGACY_MEMORY_FILE.exists():
        try:
            with open(_LEGACY_MEMORY_FILE, "r", encoding="utf-8") as f:
                legacy_data = json.load(f)
            if isinstance(legacy_data, list):
                _write_memory_file(legacy_data)
                return legacy_data
        except (json.JSONDecodeError, OSError):
            pass

    return []


def _write_memory_file(facts: List[Dict[str, str]]) -> None:
    with open(LONG_TERM_MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)


def load_long_term_memory_text() -> str:
    """Renvoie la memoire long terme formatee en texte, prete a etre injectee dans le system
    prompt. Chaine vide si aucun fait n'est encore memorise (fichier absent ou vide)."""
    with LONG_TERM_MEMORY_LOCK:
        facts = _read_memory_file()
    if not facts:
        return ""
    lines = [f"- {f.get('content', '')}" for f in facts if f.get("content")]
    if not lines:
        return ""
    return "Faits memorises sur l'utilisateur (a prendre en compte, sans les mentionner explicitement) :\n" + "\n".join(lines)


def append_long_term_memory(content: str) -> Dict[str, Any]:
    """Ajoute un fait a la memoire long terme locale (user_memory.json). Cree le fichier s'il
    n'existe pas encore. Renvoie le fait ajoute."""
    content = content.strip()
    if not content:
        return {"error": "Contenu vide, rien a memoriser."}
    with LONG_TERM_MEMORY_LOCK:
        facts = _read_memory_file()
        entry = {"content": content, "timestamp": datetime.now().isoformat()}
        facts.append(entry)
        _write_memory_file(facts)
    return entry


# ----------------------------------------------------------------------------
# COURT-CIRCUIT MEMOIRE : detection rapide des formules de memorisation explicites
# ----------------------------------------------------------------------------
# Complementaire a total_recall.extract_memory_content() (qui peut couvrir des formulations
# plus larges) : cette detection regex garantit que les formulations explicitement demandees
# ("retiens que", "souviens-toi que", "note que", "a partir de maintenant", ...) sont TOUJOURS
# interceptees AVANT tout appel LLM, quelle que soit la logique interne de total_recall.py.
MEMORY_SHORTCUT_PATTERNS: List[str] = [
    r"^retiens\s+(?:bien\s+)?que\s+(.+)",
    r"^retiens\s*[:\-]\s*(.+)",
    r"^souviens[\s-]toi\s+que\s+(.+)",
    r"^rappelle[\s-]toi\s+que\s+(.+)",
    r"^note\s+(?:bien\s+)?que\s+(.+)",
    r"^n['’]oublie\s+pas\s+que\s+(.+)",
    r"^a\s+partir\s+de\s+maintenant[,]?\s*(.+)",
    r"^à\s+partir\s+de\s+maintenant[,]?\s*(.+)",
    r"^d[ée]sormais[,]?\s*(.+)",
    r"^dor[ée]navant[,]?\s*(.+)",
    r"^memorise\s+(?:bien\s+)?que\s+(.+)",
    r"^m[ée]morise\s+(?:bien\s+)?que\s+(.+)",
]
_COMPILED_MEMORY_PATTERNS = [re.compile(p, flags=re.IGNORECASE) for p in MEMORY_SHORTCUT_PATTERNS]


def detect_memory_shortcut(prompt: str) -> Optional[str]:
    """Detecte si le prompt correspond a une formule explicite de memorisation ("retiens que
    ...", "souviens-toi que ...", "a partir de maintenant ...", etc.) et renvoie directement
    le contenu a memoriser (texte original, casse preservee), ou None si aucune formule ne
    correspond. Le matching est fait sur le texte normalise (strip), mais le contenu renvoye
    est extrait du texte ORIGINAL (pas de la version en minuscules) pour ne pas deformer les
    noms propres, chiffres, etc."""
    if not prompt:
        return None
    text = prompt.strip()
    if not text:
        return None
    for pattern in _COMPILED_MEMORY_PATTERNS:
        match = pattern.match(text)
        if match and match.groups():
            content = match.group(1).strip()
            if content:
                return content
    return None


# ----------------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------------

app = FastAPI(
    title="JARVIS Backend - Multi-LLM avec Function Calling",
    description=(
        "Backend leger FastAPI - Multi-fournisseurs LLM gratuits (Gemini, Groq, Mistral, "
        "OpenRouter, Grok) avec function calling vers l'ecosysteme Google (Drive/Docs/Sheets/"
        "Gmail/Calendar) et recherche web temps reel (Tavily)."
    ),
    version="3.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------------
# Schemas Pydantic
# ----------------------------------------------------------------------------

class HistoryMessage(BaseModel):
    role: str  # "user" ou "assistant"
    content: str


class ChatRequest(BaseModel):
    """
    Modele flexible : accepte soit {"prompt": "..."} (format natif/complet, avec provider,
    history, tools, etc.), soit {"message": "..."} (format simplifie envoye par certains
    frontends). Les deux sont acceptes pour eviter les 422 quand le frontend n'utilise pas
    exactement le meme nom de champ.
    """

    prompt: Optional[str] = None
    message: Optional[str] = None  # alias tolere, mappe automatiquement vers 'prompt'

    session_id: Optional[str] = "default"  # identifiant de session pour l'historique cote serveur

    provider: Optional[str] = None   # override ponctuel, sans changer l'etat global
    model: Optional[str] = None      # override ponctuel du modele
    system: Optional[str] = None     # instructions ponctuelles additionnelles
    history: Optional[List[HistoryMessage]] = []  # historique envoye par le frontend (optionnel desormais)
    image_base64: Optional[str] = None  # capture d'ecran ou image jointe (vision), sans prefixe data:
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 1024
    use_tools: Optional[bool] = True

    @model_validator(mode="after")
    def _resolve_prompt(self):
        # Si le frontend a envoye "message" au lieu de "prompt", on bascule dessus.
        if not self.prompt and self.message:
            self.prompt = self.message
        if not self.prompt or not self.prompt.strip():
            raise ValueError("Le champ 'prompt' (ou 'message') est requis et ne peut pas etre vide.")
        return self


class ToolCallLog(BaseModel):
    name: str
    arguments: Dict[str, Any]
    result: Any


class ChatResponse(BaseModel):
    provider: str
    model: str
    response: str
    session_id: str = "default"
    tools_used: List[ToolCallLog] = []
    source_node_ids: List[str] = []  # IDs de fichiers Drive touches par les outils, pour illuminer la Galaxie 3D
    active_categories: List[str] = []  # categories de planetes a mettre en focus (drive/docs/sheets/gmail/prediction/search)


# Categorie de "planete" associee a chaque outil, utilisee par le frontend (Galaxie 3D) pour
# savoir sur quelle constellation zoomer/faire pulser la camera pendant l'execution d'un outil,
# meme quand l'action ne correspond a aucun fichier Drive precis (ex: recherche web, prediction).
TOOL_CATEGORY: Dict[str, str] = {
    "search_google_drive": "drive",
    "list_drive_files": "drive",
    "read_drive_file": "drive",
    "get_file_details": "drive",
    "organize_drive_file": "drive",
    "save_note_to_drive": "docs",
    "remember_note": "docs",
    "create_google_doc": "docs",
    "read_google_doc": "docs",
    "write_google_doc": "docs",
    "create_google_sheet": "sheets",
    "read_google_sheet": "sheets",
    "write_google_sheet": "sheets",
    "update_sheet_cell": "sheets",
    "append_google_sheet_row": "sheets",
    "get_unread_emails": "gmail",
    "send_gmail": "gmail",
    "create_gmail_draft": "gmail",
    "list_calendar_events": "gmail",
    "create_calendar_event": "gmail",
    "predict_football_match": "prediction",
    "save_football_prediction": "prediction",
    "execute_apps_script_action": "prediction",
    "web_search": "search",
}


class SwitchBrainRequest(BaseModel):
    provider: str
    model: Optional[str] = None


class SwitchBrainResponse(BaseModel):
    message: str
    current_brain: Dict[str, Any]


class BootResponse(BaseModel):
    message: str


class ModelInfo(BaseModel):
    id: str
    name: str
    provider: str
    is_free: bool
    supports_vision: bool
    supports_tools: bool


class GalaxyNode(BaseModel):
    id: str
    name: str
    type: str = "file"  # "spreadsheet" (planete) ou "file" (etoile), voir /api/galaxy


class GalaxyResponse(BaseModel):
    nodes: List[GalaxyNode]


class MemoryFactRequest(BaseModel):
    content: str


# ----------------------------------------------------------------------------
# Fournisseurs "compatibles OpenAI" (Groq, Mistral, OpenRouter, Grok)
# ----------------------------------------------------------------------------

async def call_openai_compatible_raw(
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    llm_tools: Optional[List[Dict[str, Any]]],
    temperature: float,
    max_tokens: int,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Appelle l'endpoint /chat/completions et renvoie la reponse JSON brute."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if llm_tools:
        payload["tools"] = llm_tools
        payload["tool_choice"] = "auto"

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=f"Erreur API: {r.text}")
    return r.json()


async def run_openai_style_chat(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    system: Optional[str],
    history: Optional[List[HistoryMessage]],
    temperature: float,
    max_tokens: int,
    use_tools: bool,
    extra_headers: Optional[Dict[str, str]] = None,
    image_base64: Optional[str] = None,
) -> (str, List[ToolCallLog]):
    """Gere la boucle complete prompt -> (appels d'outils eventuels) -> reponse finale,
    pour tout fournisseur compatible OpenAI (Groq, Mistral, OpenRouter, Grok), avec historique
    et support vision optionnel (image jointe encodee en base64)."""

    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    for h in history or []:
        role = "assistant" if h.role == "assistant" else "user"
        messages.append({"role": role, "content": h.content})

    if image_base64:
        # Format multimodal standard (OpenAI-compatible) : contenu = liste de blocs texte + image.
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
            ],
        })
    else:
        messages.append({"role": "user", "content": prompt})

    llm_tools = tools.get_openai_tools() if use_tools else None
    tools_used: List[ToolCallLog] = []

    last_message: Dict[str, Any] = {}
    for _ in range(MAX_TOOL_ITERATIONS):
        data = await call_openai_compatible_raw(
            base_url, api_key, model, messages, llm_tools, temperature, max_tokens, extra_headers
        )
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError):
            raise HTTPException(status_code=502, detail=f"Reponse inattendue: {data}")

        last_message = message
        tool_calls = message.get("tool_calls")

        if not tool_calls:
            return message.get("content") or "", tools_used

        # Filet de securite : certains fournisseurs (Mistral en particulier) rejettent avec
        # "Invalid JSON payload" un message assistant renvoye avec un 'content' absent/None ou
        # des cles superflues. On ne renvoie que les champs strictement necessaires.
        safe_message: Dict[str, Any] = {
            "role": message.get("role", "assistant"),
            "content": message.get("content") or "",
            "tool_calls": tool_calls,
        }
        messages.append(safe_message)

        consecutive_errors = 0
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            raw_args = tc["function"].get("arguments") or "{}"
            try:
                fn_args = json.loads(raw_args)
            except json.JSONDecodeError:
                fn_args = {}

            result = await execute_tool_with_local_overrides(fn_name, fn_args)
            tools_used.append(ToolCallLog(name=fn_name, arguments=fn_args, result=result))
            consecutive_errors = consecutive_errors + 1 if isinstance(result, dict) and result.get("error") else 0

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": fn_name,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        if consecutive_errors >= 2:
            # Inutile d'epuiser les iterations si l'outil echoue systematiquement (ex: action
            # non implementee cote Apps Script) : on laisse le LLM formuler une reponse tout de
            # suite plutot que de boucler pour rien.
            data = await call_openai_compatible_raw(
                base_url, api_key, model, messages, None, temperature, max_tokens, extra_headers
            )
            try:
                return data["choices"][0]["message"].get("content") or "", tools_used
            except (KeyError, IndexError):
                break

    fallback_text = last_message.get("content") or "(Reponse tronquee apres plusieurs appels d'outils.)"
    return fallback_text, tools_used


# ----------------------------------------------------------------------------
# Fournisseur Gemini
# ----------------------------------------------------------------------------

async def call_gemini_raw(
    model: str,
    api_key: str,
    contents: List[Dict[str, Any]],
    system: Optional[str],
    llm_tools: Optional[List[Dict[str, Any]]],
    temperature: float,
    max_tokens: int,
) -> Dict[str, Any]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    if llm_tools:
        payload["tools"] = llm_tools

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.post(url, json=payload)
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=f"Erreur Gemini: {r.text}")
    return r.json()


async def run_gemini_chat(
    model: str,
    api_key: str,
    prompt: str,
    system: Optional[str],
    history: Optional[List[HistoryMessage]],
    temperature: float,
    max_tokens: int,
    use_tools: bool,
    image_base64: Optional[str] = None,
) -> (str, List[ToolCallLog]):
    contents: List[Dict[str, Any]] = []
    for h in history or []:
        role = "model" if h.role == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": h.content}]})

    user_parts: List[Dict[str, Any]] = [{"text": prompt}]
    if image_base64:
        user_parts.append({"inline_data": {"mime_type": "image/png", "data": image_base64}})
    contents.append({"role": "user", "parts": user_parts})

    llm_tools = tools.get_gemini_tools() if use_tools else None
    tools_used: List[ToolCallLog] = []

    last_parts: List[Dict[str, Any]] = []
    consecutive_errors = 0
    for _ in range(MAX_TOOL_ITERATIONS):
        data = await call_gemini_raw(model, api_key, contents, system, llm_tools, temperature, max_tokens)
        try:
            candidate = data["candidates"][0]
            parts = candidate["content"]["parts"]
        except (KeyError, IndexError):
            raise HTTPException(status_code=502, detail=f"Reponse Gemini inattendue: {data}")

        last_parts = parts
        function_call_part = next((p for p in parts if "functionCall" in p), None)

        if not function_call_part:
            text = "".join(p.get("text", "") for p in parts)
            return text, tools_used

        contents.append({"role": "model", "parts": parts})

        fc = function_call_part["functionCall"]
        fn_name = fc.get("name", "")
        fn_args = fc.get("args", {}) or {}

        result = await execute_tool_with_local_overrides(fn_name, fn_args)
        tools_used.append(ToolCallLog(name=fn_name, arguments=fn_args, result=result))
        consecutive_errors = consecutive_errors + 1 if isinstance(result, dict) and result.get("error") else 0

        contents.append(
            {"role": "function", "parts": [{"functionResponse": {"name": fn_name, "response": {"result": result}}}]}
        )

        if consecutive_errors >= 2:
            # L'outil echoue systematiquement (ex: action non implementee cote Apps Script) :
            # on demande une derniere reponse sans outils plutot que d'epuiser les iterations.
            data = await call_gemini_raw(model, api_key, contents, system, None, temperature, max_tokens)
            try:
                text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
                return text, tools_used
            except (KeyError, IndexError):
                break

    fallback_text = "".join(p.get("text", "") for p in last_parts) or "(Reponse tronquee apres plusieurs appels d'outils.)"
    return fallback_text, tools_used


# ----------------------------------------------------------------------------
# INTEGRATION GOOGLE APPS SCRIPT : fonction generique + wrapper pronostics
# ----------------------------------------------------------------------------
# URL du WebApp Google Apps Script deploye. GAS_ACTIONS_WEBAPP_URL est le nom generique a
# privilegier desormais ; on retombe sur GAS_PREDICTIONS_WEBAPP_URL par compatibilite
# ascendante si l'ancienne variable est deja definie sur ton deploiement Render.
GAS_PREDICTIONS_WEBAPP_URL = os.getenv("GAS_PREDICTIONS_WEBAPP_URL", "URL_DE_TON_APPS_SCRIPT")
GAS_ACTIONS_WEBAPP_URL = os.getenv("GAS_ACTIONS_WEBAPP_URL", GAS_PREDICTIONS_WEBAPP_URL)


async def send_to_apps_script(action: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Fonction GENERIQUE de transmission vers le WebApp Google Apps Script : poste n'importe
    quelle 'action' + payload JSON, sans avoir besoin d'un endpoint Python dedie par cas
    d'usage. Le WebApp Apps Script route deja sur le champ 'action' (comme il le fait pour
    'save_prediction' et pour remember_note via connector.py) ; il suffit d'ajouter un nouveau
    'case' dans le doPost() du script pour supporter une nouvelle action (ex: 'save_revenue',
    'update_client', etc.), sans toucher a ce backend."""
    url = GAS_ACTIONS_WEBAPP_URL
    if not url or url == "URL_DE_TON_APPS_SCRIPT":
        return {
            "error": (
                "URL du WebApp Apps Script non configuree. Definis la variable d'environnement "
                "GAS_ACTIONS_WEBAPP_URL (ou GAS_PREDICTIONS_WEBAPP_URL) avec l'URL /exec de ton "
                "deploiement."
            )
        }

    payload = {"action": action, "timestamp": datetime.now().isoformat(), **data}

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.post(url, json=payload)
        if r.status_code != 200:
            return {"error": f"Le WebApp Apps Script a repondu avec le statut {r.status_code}: {r.text}"}
        try:
            return r.json()
        except ValueError:
            # Certains deploiements Apps Script renvoient du texte brut plutot que du JSON.
            return {"success": True, "raw_response": r.text}
    except httpx.HTTPError as e:
        return {"error": f"Echec de la requete vers le WebApp Apps Script : {e}"}


async def save_football_prediction(match_data: Dict[str, Any]) -> Dict[str, Any]:
    """Enregistre un pronostic de match. Simple wrapper retrocompatible autour de
    send_to_apps_script("save_prediction", ...), conserve pour que le nom d'outil existant
    (deja declare dans tools.py) continue de fonctionner sans modification.

    match_data attendu, par exemple :
        {
            "equipe_domicile": "Sarmiento",
            "equipe_exterieur": "Rivadavia",
            "score_predit_domicile": 2,
            "score_predit_exterieur": 1,
            "issue": "1",              # "1" (domicile), "N" (nul), "2" (exterieur)
            "confiance": 0.72,          # 0-1, optionnel
            "date_match": "2026-08-10", # optionnel
            "commentaire": "..."        # optionnel
        }
    """
    return await send_to_apps_script("save_prediction", match_data)


async def execute_tool_with_local_overrides(fn_name: str, fn_args: Dict[str, Any]) -> Any:
    """Point d'entree unique pour l'execution des outils appeles par le LLM.

    On intercepte ici les outils geres localement dans main.py avant de retomber sur le
    dispatcher generique tools.execute_tool pour tous les autres outils
    (Drive/Docs/Sheets/Gmail/Calendar/recherche web/etc.).

    IMPORTANT : pour que le LLM sache que ces outils existent et decide de les appeler, il
    faut declarer leur schema de function calling dans tools.py (voir TOOLS / get_openai_tools
    / get_gemini_tools).
    """
    if fn_name == "save_football_prediction":
        return await save_football_prediction(fn_args)
    if fn_name == "execute_apps_script_action":
        action = fn_args.get("action", "")
        data = fn_args.get("data", {}) or {}
        if not action:
            return {"error": "Le champ 'action' est requis pour execute_apps_script_action."}
        return await send_to_apps_script(action, data)
    return await tools.execute_tool(fn_name, fn_args)


# ----------------------------------------------------------------------------
# Dispatch generique par fournisseur
# ----------------------------------------------------------------------------

async def dispatch_to_provider(
    provider: str,
    model: str,
    prompt: str,
    system: Optional[str],
    history: Optional[List[HistoryMessage]],
    temperature: float,
    max_tokens: int,
    use_tools: bool,
    image_base64: Optional[str] = None,
) -> (str, List[ToolCallLog]):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Fournisseur inconnu: {provider}")

    cfg = PROVIDERS[provider]
    api_key = os.getenv(cfg["key_env"])
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail=f"Cle API manquante pour '{provider}'. Definissez la variable d'environnement {cfg['key_env']}.",
        )

    if cfg["family"] == "gemini":
        return await run_gemini_chat(
            model, api_key, prompt, system, history, temperature, max_tokens, use_tools, image_base64=image_base64
        )

    if cfg["family"] == "openai_compatible":
        return await run_openai_style_chat(
            cfg["base_url"], api_key, model, prompt, system, history, temperature, max_tokens, use_tools,
            extra_headers=cfg.get("extra_headers"), image_base64=image_base64,
        )

    raise HTTPException(status_code=400, detail=f"Fournisseur non implemente: {provider}")


async def vision_chat_with_fallback(
    prompt: str,
    image_base64: str,
    system: Optional[str],
    history: Optional[List[HistoryMessage]],
    temperature: float,
    max_tokens: int,
    use_tools: bool,
    preferred_provider: Optional[str] = None,
    preferred_model: Optional[str] = None,
) -> (str, List[ToolCallLog], str, str):
    """Envoie une image au meilleur modele vision disponible ; si l'appel echoue (quota, erreur
    API, modele indisponible), bascule automatiquement sur le modele vision suivant de la liste,
    decouverte dynamiquement (aucun nom de modele fige). Renvoie (reponse, tools_used, provider, model)."""
    candidates = await model_discovery.get_vision_candidates(preferred_provider, preferred_model)
    if not candidates:
        raise HTTPException(
            status_code=503,
            detail="Aucun modele avec support vision n'a ete decouvert parmi les fournisseurs configures.",
        )

    last_error = None
    for candidate in candidates:
        try:
            answer, tools_used = await dispatch_to_provider(
                provider=candidate["provider"],
                model=candidate["id"],
                prompt=prompt,
                system=system,
                history=history,
                temperature=temperature,
                max_tokens=max_tokens,
                use_tools=use_tools,
                image_base64=image_base64,
            )
            return answer, tools_used, candidate["provider"], candidate["id"]
        except Exception as e:  # on essaie le candidat suivant plutot que d'echouer immediatement
            last_error = e
            continue

    raise HTTPException(status_code=502, detail=f"Tous les modeles vision disponibles ont echoue : {last_error}")


def current_datetime_str() -> str:
    """Date/heure courante formatee en francais, injectee dans le prompt systeme a chaque requete."""
    return datetime.now().strftime("%A %d %B %Y %H:%M")


# ----------------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Endpoint de sante utilise par Render pour verifier que le service tourne."""
    return {"status": "ok"}


@app.get("/providers")
async def list_providers():
    """Liste les fournisseurs configures (cle API presente ou non). Pour la liste detaillee des
    modeles disponibles par fournisseur (decouverte dynamique), voir GET /api/models."""
    result = {}
    for name, cfg in PROVIDERS.items():
        result[name] = {"configured": bool(os.getenv(cfg["key_env"]))}
    return {
        "providers": result,
        "current_brain": current_brain,
        "tools_available": [t["name"] for t in tools.TOOLS],
        "connector_configured": bool(os.getenv("GAS_WEBAPP_URL")),
        "apps_script_actions_configured": bool(
            GAS_ACTIONS_WEBAPP_URL and GAS_ACTIONS_WEBAPP_URL != "URL_DE_TON_APPS_SCRIPT"
        ),
        "web_search_configured": bool(os.getenv("TAVILY_API_KEY")),
    }


@app.get("/api/models", response_model=Dict[str, List[ModelInfo]])
async def api_models(refresh: bool = False):
    """Decouverte dynamique des modeles disponibles chez chaque fournisseur configure (aucun nom
    de modele code en dur cote backend). Resultat en cache 5 min ; passer ?refresh=true pour forcer
    une nouvelle interrogation des APIs de listing."""
    try:
        grouped = await model_discovery.discover_models(force_refresh=refresh)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Echec de la decouverte des modeles : {e}")
    return grouped


# ----------------------------------------------------------------------------
# GALAXIE 3D : GET /api/galaxy
# ----------------------------------------------------------------------------
# Interroge Google Drive VIA LE CONNECTEUR APPS SCRIPT (connector.get_drive_graph), au lieu
# d'un compte de service Google separe : ainsi l'authentification est unique et identique a
# celle deja utilisee pour tous les autres outils (Drive/Docs/Sheets/Gmail/Calendar). Chaque
# fichier devient un noeud pour le rendu Three.js cote frontend :
#   - type = "spreadsheet" (rendu en planete) des que le mimeType contient "spreadsheet" ;
#   - type = "file" (rendu en etoile) pour tout le reste (Docs, PDF, dossiers, images, etc.).
def _classify_galaxy_node_type(mime_type: str) -> str:
    """Determine le type de noeud pour le rendu 3D a partir du mimeType Google Drive."""
    mime_lower = (mime_type or "").lower()
    if "spreadsheet" in mime_lower:
        return "spreadsheet"
    return "file"


@app.get("/api/galaxy", response_model=GalaxyResponse)
async def api_galaxy():
    """
    Renvoie les fichiers Google Drive accessibles a l'utilisateur, sous forme de noeuds
    {id, name, type} destines a la Galaxie 3D (Three.js) du frontend :
      - "spreadsheet" (Google Sheets) -> rendu comme une planete
      - "file" (tout le reste)        -> rendu comme une etoile

    La liste est obtenue via connector.get_drive_graph(), qui appelle le meme WebApp Apps
    Script (action 'get_drive_graph') que les autres outils Drive - donc aucune credential
    supplementaire a configurer.

    Proteg par un try/except global : si le Drive n'est pas encore authentifie (GAS_WEBAPP_URL
    non configuree, jeton invalide, script non deploye, erreur reseau...), on renvoie une liste
    de noeuds VIDE plutot que de faire echouer la requete. La Galaxie doit pouvoir s'afficher
    (vide) meme sans connexion Drive active, pour ne jamais casser le frontend.
    """
    try:
        graph = await connector.get_drive_graph()
        raw_files = graph.get("files", []) if isinstance(graph, dict) else []
    except connector.ConnectorError:
        # Drive non authentifie / GAS_WEBAPP_URL absente / script non deploye : fallback propre.
        raw_files = []
    except Exception:
        # Filet de securite ultime : ne jamais faire remonter une erreur brute au frontend.
        raw_files = []

    nodes: List[GalaxyNode] = []
    for f in raw_files:
        if not isinstance(f, dict):
            continue
        file_id = f.get("id")
        if not file_id:
            continue
        name = f.get("name") or "Sans nom"
        node_type = _classify_galaxy_node_type(f.get("mimeType", ""))
        nodes.append(GalaxyNode(id=file_id, name=name, type=node_type))

    return GalaxyResponse(nodes=nodes)


@app.get("/boot", response_model=BootResponse)
async def boot():
    """Message d'accueil fixe affiche/lu par le frontend a la connexion (pas de LLM,
    pour un demarrage instantane), protege par un try/except global."""
    try:
        return BootResponse(message=persona.BOOT_MESSAGE)
    except Exception:
        return BootResponse(message="Systeme en ligne.")


async def _perform_brain_switch(provider: str, model: Optional[str] = None) -> SwitchBrainResponse:
    """Logique commune de bascule de cerveau, partagee par /switch-brain et /api/models/select."""
    provider = normalize_provider(provider)
    if provider not in PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Fournisseur inconnu '{provider}'. Choix possibles: {list(PROVIDERS.keys())}",
        )

    resolved_model = model
    if not resolved_model:
        try:
            resolved_model = await model_discovery.get_default_model_for_provider(provider)
        except ValueError as e:
            raise HTTPException(status_code=502, detail=str(e))

    current_brain["provider"] = provider
    current_brain["model"] = resolved_model

    return SwitchBrainResponse(
        message=f"Cerveau actif change pour '{provider}' ({resolved_model}).",
        current_brain=current_brain,
    )


@app.post("/switch-brain", response_model=SwitchBrainResponse)
async def switch_brain(req: SwitchBrainRequest):
    """Change dynamiquement le fournisseur / modele LLM actif par defaut."""
    return await _perform_brain_switch(req.provider, req.model)


@app.post("/api/models/select", response_model=SwitchBrainResponse)
async def api_models_select(req: SwitchBrainRequest):
    """Alias de /switch-brain, nomme selon la convention /api/models : bascule a chaud le
    fournisseur/modele actif depuis le selecteur de modeles du frontend, sans redemarrer le serveur."""
    return await _perform_brain_switch(req.provider, req.model)


# ----------------------------------------------------------------------------
# Endpoints de gestion de la memoire
# ----------------------------------------------------------------------------

@app.get("/api/memory")
async def api_get_memory():
    """Renvoie les faits actuellement memorises en long terme (contenu de user_memory.json)."""
    with LONG_TERM_MEMORY_LOCK:
        facts = _read_memory_file()
    return {"facts": facts}


@app.post("/api/memory")
async def api_add_memory(req: MemoryFactRequest):
    """Ajoute manuellement un fait a la memoire long terme locale."""
    entry = append_long_term_memory(req.content)
    return {"added": entry}


@app.delete("/api/session/{session_id}")
async def api_clear_session(session_id: str):
    """Efface l'historique court terme d'une session (pour repartir sur une conversation vierge)."""
    clear_session_history(session_id)
    return {"message": f"Historique de la session '{session_id}' efface."}


def extract_source_node_ids(tools_used: List[ToolCallLog]) -> List[str]:
    """Parcourt les resultats d'outils pour en extraire les IDs de fichiers Drive touches,
    afin que le frontend puisse illuminer les noeuds correspondants dans la Galaxie 3D.

    Recherche explicitement les cles 'id', 'spreadsheetId' et 'fileId' (les trois noms de
    cles couramment renvoyes par les differentes actions Apps Script / API Google), en plus
    d'un parcours recursif complet de la structure (listes imbriquees, sous-objets)."""
    ids: List[str] = []
    target_keys = ("id", "spreadsheetId", "fileId")

    def collect(obj: Any):
        if isinstance(obj, dict):
            for key in target_keys:
                value = obj.get(key)
                if isinstance(value, str) and value and value not in ids:
                    ids.append(value)
            for value in obj.values():
                collect(value)
        elif isinstance(obj, list):
            for item in obj:
                collect(item)

    for call in tools_used:
        collect(call.result)
    return ids


def extract_active_categories(tools_used: List[ToolCallLog]) -> List[str]:
    """Deduit les categories de planetes (drive/docs/sheets/gmail/prediction/search) touchees
    par les outils appeles pendant ce tour, pour piloter le focus camera de la Galaxie 3D."""
    categories: List[str] = []
    for call in tools_used:
        category = TOOL_CATEGORY.get(call.name)
        if category and category not in categories:
            categories.append(category)
    return categories


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    Envoie un prompt au LLM actif (ou a un fournisseur/modele precise ponctuellement), avec
    la personnalite JARVIS toujours appliquee, enrichie a chaque requete de la date/heure
    courante, du cerveau actif, ET de la memoire long terme locale (user_memory.json).

    L'historique de conversation est garanti cote SERVEUR : chaque session (identifiee par
    session_id) conserve ses MAX_SESSION_MESSAGES derniers messages dans SESSION_HISTORIES,
    independamment de ce que renvoie (ou non) le frontend. Le frontend peut continuer a
    envoyer 'history', il sert seulement a amorcer une session vide.

    Accepte indifferemment {"prompt": "..."} ou {"message": "..."} en entree (voir ChatRequest).

    Si une image (capture d'ecran) est jointe (image_base64), la requete est automatiquement
    routee vers le meilleur modele vision disponible, avec bascule (fallback) sur le modele
    vision suivant en cas d'echec du premier - liste decouverte dynamiquement, aucun modele fige.

    Trois court-circuits rapides, SANS passer par le LLM (donc sans consommer de tokens) :
      1. Formules de memorisation explicites ("Retiens que...", "Souviens-toi que...",
         "Note que...", "A partir de maintenant...") -> detect_memory_shortcut() (regex locale,
         garantie de fonctionner independamment de total_recall.py) ; en complement,
         total_recall.extract_memory_content() reste egalement verifie pour couvrir des
         formulations plus larges. Des qu'un contenu est extrait par l'une ou l'autre methode,
         memorisation IMMEDIATE dans le connecteur Drive (remember_note) ET dans
         user_memory.json (local), puis reponse retournee sans jamais appeler un LLM.
      2. "Passe sur Groq", "Quel cerveau utilises-tu ?" -> changement/etat du cerveau immediat.

    Pour toute autre demande (ex: "combien de mails Gmail aujourd'hui ?" ou "enregistre mon
    pronostic Sarmiento-Rivadavia"), le prompt est envoye au LLM actif AVEC les definitions
    d'outils (tools.get_*_tools()) : c'est le LLM qui decide d'appeler reellement l'outil (ex:
    get_unread_emails, save_football_prediction, execute_apps_script_action), et la reponse
    finale integre le resultat reel de l'outil, pas une reponse statique.

    Toute la logique d'appel LLM est protegee par un try/except global : le frontend ne
    recoit jamais une erreur HTTP 500/404 brute, mais toujours une reponse JSON exploitable.
    """
    session_id = req.session_id or "default"

    provider = normalize_provider(req.provider) or current_brain["provider"]
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Fournisseur inconnu: {provider}")

    try:
        if req.model:
            model = req.model
        elif provider == current_brain["provider"]:
            model = await ensure_current_brain_model()
        else:
            model = await model_discovery.get_default_model_for_provider(provider)
    except ValueError as e:
        return ChatResponse(provider=provider, model="", response=f"Petit contretemps technique : {e}", tools_used=[], session_id=session_id)

    use_tools_flag = req.use_tools if req.use_tools is not None else True

    # ------------------------------------------------------------------------
    # Court-circuit 1 : memorisation instantanee (Total Recall + detection locale explicite)
    # ------------------------------------------------------------------------
    if use_tools_flag and not req.image_base64:
        memory_content = detect_memory_shortcut(req.prompt) or total_recall.extract_memory_content(req.prompt)
        if memory_content:
            result = await tools.execute_tool("remember_note", {"content": memory_content})
            # En plus du connecteur Drive, on ecrit systematiquement dans la memoire locale
            # (user_memory.json), qui sert de source fiable et rapide pour l'injection au
            # system prompt.
            append_long_term_memory(memory_content)
            if isinstance(result, dict) and result.get("error"):
                response_text = persona.get_memory_failure_line(result["error"])
            else:
                response_text = persona.get_memory_confirmation()
            cleaned = persona.clean_text_for_voice(response_text)
            append_session_turn(session_id, req.prompt, cleaned)
            return ChatResponse(
                provider=provider,
                model=model,
                response=cleaned,
                session_id=session_id,
                tools_used=[ToolCallLog(name="remember_note", arguments={"content": memory_content}, result=result)],
                source_node_ids=extract_source_node_ids(
                    [ToolCallLog(name="remember_note", arguments={"content": memory_content}, result=result)]
                ),
                active_categories=["docs"],
            )

    # ------------------------------------------------------------------------
    # Court-circuit 2 : commandes vocales de changement / etat du cerveau
    # ------------------------------------------------------------------------
    if not req.image_base64:
        switch_target = persona.detect_brain_switch(req.prompt)
        if switch_target and switch_target in PROVIDERS:
            try:
                current_brain["provider"] = switch_target
                current_brain["model"] = await model_discovery.get_default_model_for_provider(switch_target)
            except ValueError as e:
                return ChatResponse(provider=provider, model=model, response=f"Petit contretemps technique : {e}", tools_used=[], session_id=session_id)
            cleaned = persona.clean_text_for_voice(persona.get_brain_switch_confirmation(switch_target))
            append_session_turn(session_id, req.prompt, cleaned)
            return ChatResponse(
                provider=switch_target,
                model=current_brain["model"],
                response=cleaned,
                session_id=session_id,
                tools_used=[],
            )
        if persona.detect_brain_query(req.prompt):
            cleaned = persona.clean_text_for_voice(persona.get_brain_query_answer(current_brain["provider"]))
            append_session_turn(session_id, req.prompt, cleaned)
            return ChatResponse(
                provider=provider,
                model=model,
                response=cleaned,
                session_id=session_id,
                tools_used=[],
            )

    # ------------------------------------------------------------------------
    # Construction du system prompt : personnalite + date/heure + cerveau actif +
    # memoire long terme locale (user_memory.json), injectee discretement.
    # ------------------------------------------------------------------------
    long_term_memory_text = load_long_term_memory_text()
    extra_instructions = req.system or ""
    if long_term_memory_text:
        extra_instructions = (extra_instructions + "\n\n" + long_term_memory_text).strip()

    effective_system = persona.build_effective_system_prompt(
        current_datetime=current_datetime_str(),
        current_provider=current_brain["provider"],
        extra_instructions=extra_instructions or None,
    )

    # Historique effectif : priorite a la memoire de session cote serveur, amorcee au besoin
    # par l'historique envoye par le frontend (voir merge_history).
    effective_history = merge_history(session_id, req.history)

    # ------------------------------------------------------------------------
    # Appel LLM normal (ou vision avec fallback automatique), avec function calling reel.
    # C'est ICI que Gmail / Drive / Sheets / recherche web / pronostics sont effectivement
    # executes, via dispatch_to_provider -> run_gemini_chat / run_openai_style_chat ->
    # execute_tool_with_local_overrides (qui route vers save_football_prediction,
    # execute_apps_script_action, ou tools.execute_tool selon l'outil demande).
    # Le tout est protege globalement pour ne jamais renvoyer une erreur brute au frontend.
    # ------------------------------------------------------------------------
    try:
        if req.image_base64:
            answer, tools_used, used_provider, used_model = await vision_chat_with_fallback(
                prompt=req.prompt,
                image_base64=req.image_base64,
                system=effective_system,
                history=effective_history,
                temperature=req.temperature or 0.7,
                max_tokens=req.max_tokens or 1024,
                use_tools=use_tools_flag,
                preferred_provider=provider,
                preferred_model=model,
            )
            provider, model = used_provider, used_model
        else:
            answer, tools_used = await dispatch_to_provider(
                provider=provider,
                model=model,
                prompt=req.prompt,
                system=effective_system,
                history=effective_history,
                temperature=req.temperature or 0.7,
                max_tokens=req.max_tokens or 1024,
                use_tools=use_tools_flag,
            )
    except HTTPException as e:
        error_text = f"Petit contretemps technique : {e.detail}"
        return ChatResponse(
            provider=provider,
            model=model,
            response=error_text,
            session_id=session_id,
            tools_used=[],
        )
    except Exception as e:  # filet de securite ultime : jamais de 500 brut cote frontend
        error_text = f"Une erreur inattendue est survenue : {e}"
        return ChatResponse(
            provider=provider,
            model=model,
            response=error_text,
            session_id=session_id,
            tools_used=[],
        )

    final_answer = persona.clean_text_for_voice(answer)

    # Memorisation de l'echange cote serveur, pour que le tour suivant garde le fil meme
    # sans historique envoye par le frontend.
    append_session_turn(session_id, req.prompt, final_answer)

    return ChatResponse(
        provider=provider,
        model=model,
        response=final_answer,
        session_id=session_id,
        tools_used=tools_used,
        source_node_ids=extract_source_node_ids(tools_used),
        active_categories=extract_active_categories(tools_used),
    )


# ----------------------------------------------------------------------------
# Fichiers statiques / frontend (route catch-all, doit rester en DERNIER pour ne
# jamais intercepter les routes API definies au-dessus).
# ----------------------------------------------------------------------------

@app.get("/")
async def root():
    """Sert directement le HUD JARVIS : l'URL racine du deploiement Render affiche
    immediatement le frontend, sans etape intermediaire."""
    index_path = BASE_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "JARVIS Backend actif", "current_brain": current_brain}


@app.get("/{file_name:path}")
async def serve_static_files(file_name: str):
    """Sert les fichiers statiques du frontend (JS/CSS/assets). Retourne 404 si le
    fichier n'existe pas, sans jamais intercepter les routes API (elles sont enregistrees
    avant celle-ci et sont donc prioritaires)."""
    if not file_name:
        index_path = BASE_DIR / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return Response(status_code=404)

    full_path = BASE_DIR / file_name
    if full_path.exists() and full_path.is_file():
        if file_name.endswith((".jsx", ".js")):
            return FileResponse(full_path, media_type="text/javascript")
        return FileResponse(full_path)

    return Response(status_code=404)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
