"""
Backend Python leger - FastAPI multi-fournisseurs LLM (gratuit) + Function Calling
Fournisseurs supportes : Gemini, Groq, Mistral, OpenRouter
Outils (tools) : connecteur Google Apps Script (Drive / Docs / Sheets / Gmail / Calendar)
Recherche web : Tavily (temps reel)
Deploiement : Render (voir Procfile) - sert aussi le frontend (index.html) directement.
"""

import os
import json
import httpx
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

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
# FastAPI app
# ----------------------------------------------------------------------------

app = FastAPI(
    title="JARVIS Backend - Multi-LLM avec Function Calling",
    description=(
        "Backend leger FastAPI - Multi-fournisseurs LLM gratuits (Gemini, Groq, Mistral, "
        "OpenRouter) avec function calling vers l'ecosysteme Google (Drive/Docs/Sheets/"
        "Gmail/Calendar) et recherche web temps reel (Tavily)."
    ),
    version="3.0.0",
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
    prompt: str
    provider: Optional[str] = None   # override ponctuel, sans changer l'etat global
    model: Optional[str] = None      # override ponctuel du modele
    system: Optional[str] = None     # instructions ponctuelles additionnelles
    history: Optional[List[HistoryMessage]] = None  # historique de conversation (frontend -> backend)
    image_base64: Optional[str] = None  # capture d'ecran ou image jointe (vision), sans prefixe data:
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 1024
    use_tools: Optional[bool] = True


class ToolCallLog(BaseModel):
    name: str
    arguments: Dict[str, Any]
    result: Any


class ChatResponse(BaseModel):
    provider: str
    model: str
    response: str
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
    mime_type: str = ""
    url: str = ""


class GalaxyLink(BaseModel):
    source: str
    target: str


class GalaxyResponse(BaseModel):
    nodes: List[GalaxyNode]
    links: List[GalaxyLink]


# ----------------------------------------------------------------------------
# Fournisseurs "compatibles OpenAI" (Groq, Mistral, OpenRouter)
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
    pour tout fournisseur compatible OpenAI (Groq, Mistral, OpenRouter), avec historique et
    support vision optionnel (image jointe encodee en base64)."""

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

            result = await tools.execute_tool(fn_name, fn_args)
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

        result = await tools.execute_tool(fn_name, fn_args)
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

@app.get("/")
async def root():
    """Sert directement le HUD JARVIS : l'URL racine du deploiement Render affiche
    immediatement le frontend, sans etape intermediaire."""
    index_path = BASE_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "JARVIS Backend actif", "current_brain": current_brain}


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


# Planetes "systeme" sans fichier Drive correspondant : toujours presentes dans la Galaxie pour
# que la camera ait une cible meme quand JARVIS utilise Gmail, la prediction football, ou le
# web_search (mime_type prefixe "system/" pour un code couleur dedie cote frontend).
SYSTEM_GALAXY_NODES = [
    GalaxyNode(id="system:gmail", name="Gmail", mime_type="system/gmail", url=""),
    GalaxyNode(id="system:prediction", name="Predicteur Football", mime_type="system/prediction", url=""),
    GalaxyNode(id="system:search", name="Recherche Web", mime_type="system/search", url=""),
]


@app.get("/api/galaxy", response_model=GalaxyResponse)
async def api_galaxy():
    """Construit le graphe (nodes/links) des fichiers Google Drive de l'utilisateur, augmente de
    planetes 'systeme' fixes (Gmail, prediction football, recherche web), pour alimenter la
    Galaxie 3D du frontend. Chaque fichier est un noeud ; chaque lien dossier -> fichier est une arete."""
    nodes: List[GalaxyNode] = list(SYSTEM_GALAXY_NODES)
    links: List[GalaxyLink] = []

    try:
        graph = await connector.get_drive_graph()
        for item in graph.get("files", []):
            nodes.append(GalaxyNode(
                id=item["id"], name=item.get("name", ""), mime_type=item.get("mimeType", ""), url=item.get("url", "")
            ))
            if item.get("parentId"):
                links.append(GalaxyLink(source=item["parentId"], target=item["id"]))
    except connector.ConnectorError:
        # Le connecteur Drive peut etre indisponible : on renvoie quand meme les planetes
        # systeme plutot que de faire echouer toute la Galaxie.
        pass

    return GalaxyResponse(nodes=nodes, links=links)


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
    provider = provider.lower().strip()
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


def extract_source_node_ids(tools_used: List[ToolCallLog]) -> List[str]:
    """Parcourt les resultats d'outils pour en extraire les IDs de fichiers Drive touches,
    afin que le frontend puisse illuminer les noeuds correspondants dans la Galaxie 3D."""
    ids: List[str] = []

    def collect(obj: Any):
        if isinstance(obj, dict):
            file_id = obj.get("id")
            if isinstance(file_id, str) and file_id and file_id not in ids:
                ids.append(file_id)
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
    courante et du cerveau actif. L'historique de conversation transmis par le frontend est
    reinjecte pour que JARVIS ne perde jamais le fil.

    Si une image (capture d'ecran) est jointe (image_base64), la requete est automatiquement
    routee vers le meilleur modele vision disponible, avec bascule (fallback) sur le modele
    vision suivant en cas d'echec du premier - liste decouverte dynamiquement, aucun modele fige.

    Deux court-circuits rapides, sans passer par le LLM :
      - "Rappelle-toi que...", "Note que...", "Souviens-toi que..." -> memorisation immediate.
      - "Passe sur Groq", "Quel cerveau utilises-tu ?" -> changement/etat du cerveau immediat.

    Toute la logique d'appel LLM est protegee par un try/except global : le frontend ne
    recoit jamais une erreur HTTP 500/404 brute, mais toujours une reponse JSON exploitable.
    """
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Le champ 'prompt' ne peut pas etre vide.")

    provider = (req.provider or current_brain["provider"]).lower().strip()
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
        return ChatResponse(provider=provider, model="", response=f"Petit contretemps technique : {e}", tools_used=[])

    use_tools_flag = req.use_tools if req.use_tools is not None else True

    # ------------------------------------------------------------------------
    # Court-circuit 1 : Total Recall (memorisation instantanee)
    # ------------------------------------------------------------------------
    if use_tools_flag and not req.image_base64:
        memory_content = total_recall.extract_memory_content(req.prompt)
        if memory_content:
            result = await tools.execute_tool("remember_note", {"content": memory_content})
            if isinstance(result, dict) and result.get("error"):
                response_text = persona.get_memory_failure_line(result["error"])
            else:
                response_text = persona.get_memory_confirmation()
            return ChatResponse(
                provider=provider,
                model=model,
                response=persona.clean_text_for_voice(response_text),
                tools_used=[ToolCallLog(name="remember_note", arguments={"content": memory_content}, result=result)],
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
                return ChatResponse(provider=provider, model=model, response=f"Petit contretemps technique : {e}", tools_used=[])
            return ChatResponse(
                provider=switch_target,
                model=current_brain["model"],
                response=persona.clean_text_for_voice(persona.get_brain_switch_confirmation(switch_target)),
                tools_used=[],
            )
        if persona.detect_brain_query(req.prompt):
            return ChatResponse(
                provider=provider,
                model=model,
                response=persona.clean_text_for_voice(persona.get_brain_query_answer(current_brain["provider"])),
                tools_used=[],
            )

    # ------------------------------------------------------------------------
    # Appel LLM normal (ou vision avec fallback automatique), protege globalement
    # ------------------------------------------------------------------------
    effective_system = persona.build_effective_system_prompt(
        current_datetime=current_datetime_str(),
        current_provider=current_brain["provider"],
        extra_instructions=req.system,
    )

    try:
        if req.image_base64:
            answer, tools_used, used_provider, used_model = await vision_chat_with_fallback(
                prompt=req.prompt,
                image_base64=req.image_base64,
                system=effective_system,
                history=req.history,
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
                history=req.history,
                temperature=req.temperature or 0.7,
                max_tokens=req.max_tokens or 1024,
                use_tools=use_tools_flag,
            )
    except HTTPException as e:
        return ChatResponse(
            provider=provider,
            model=model,
            response=f"Petit contretemps technique : {e.detail}",
            tools_used=[],
        )
    except Exception as e:  # filet de securite ultime : jamais de 500 brut cote frontend
        return ChatResponse(
            provider=provider,
            model=model,
            response=f"Une erreur inattendue est survenue : {e}",
            tools_used=[],
        )

    return ChatResponse(
        provider=provider,
        model=model,
        response=persona.clean_text_for_voice(answer),
        tools_used=tools_used,
        source_node_ids=extract_source_node_ids(tools_used),
        active_categories=extract_active_categories(tools_used),
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
# --- CODE À AJOUTER À LA FIN DE MAIN.PY ---
from fastapi.responses import FileResponse, Response
import os

@app.get("/{file_name:path}")
async def serve_static_files(file_name: str):
    if file_name == "" or file_name == "/":
        return FileResponse("index.html")
    
    if os.path.exists(file_name):
        if file_name.endswith((".jsx", ".js")):
            return FileResponse(file_name, media_type="text/javascript")
        return FileResponse(file_name)
    
    return Response(status_code=404)
