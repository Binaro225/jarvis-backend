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

# ----------------------------------------------------------------------------
# Configuration des fournisseurs (cles API lues via variables d'environnement)
# ----------------------------------------------------------------------------

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "gemini": {
        "key_env": "GEMINI_API_KEY",
        "models": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro"],
        "default_model": "gemini-2.5-flash",
        "family": "gemini",
    },
    "groq": {
        "key_env": "GROQ_API_KEY",
        "models": ["llama-3.3-70b-versatile", "deepseek-r1-distill-llama-70b"],
        "default_model": "llama-3.3-70b-versatile",
        "family": "openai_compatible",
        "base_url": "https://api.groq.com/openai/v1",
    },
    "mistral": {
        "key_env": "MISTRAL_API_KEY",
        "models": ["mistral-small-latest", "open-mistral-7b"],
        "default_model": "mistral-small-latest",
        "family": "openai_compatible",
        "base_url": "https://api.mistral.ai/v1",
    },
    "openrouter": {
        "key_env": "OPENROUTER_API_KEY",
        "models": [
            "meta-llama/llama-3.3-70b-instruct:free",
            "google/gemini-2.0-flash-exp:free",
            "deepseek/deepseek-r1:free",
        ],
        "default_model": "meta-llama/llama-3.3-70b-instruct:free",
        "family": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "extra_headers": {
            "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://render.com"),
            "X-Title": os.getenv("OPENROUTER_SITE_NAME", "Multi-LLM Backend"),
        },
    },
}

MAX_TOOL_ITERATIONS = 5  # garde-fou contre les boucles d'appels d'outils infinies
BASE_DIR = Path(__file__).resolve().parent

# Etat global : le "cerveau" actuellement actif
current_brain: Dict[str, str] = {
    "provider": os.getenv("DEFAULT_PROVIDER", "gemini"),
    "model": os.getenv(
        "DEFAULT_MODEL",
        PROVIDERS.get(os.getenv("DEFAULT_PROVIDER", "gemini"), PROVIDERS["gemini"])["default_model"],
    ),
}

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


class SwitchBrainRequest(BaseModel):
    provider: str
    model: Optional[str] = None


class SwitchBrainResponse(BaseModel):
    message: str
    current_brain: Dict[str, str]


class BootResponse(BaseModel):
    message: str


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
) -> (str, List[ToolCallLog]):
    """Gere la boucle complete prompt -> (appels d'outils eventuels) -> reponse finale,
    pour tout fournisseur compatible OpenAI (Groq, Mistral, OpenRouter), avec historique."""

    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    for h in history or []:
        role = "assistant" if h.role == "assistant" else "user"
        messages.append({"role": role, "content": h.content})
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
) -> (str, List[ToolCallLog]):
    contents: List[Dict[str, Any]] = []
    for h in history or []:
        role = "model" if h.role == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": h.content}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

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
        return await run_gemini_chat(model, api_key, prompt, system, history, temperature, max_tokens, use_tools)

    if cfg["family"] == "openai_compatible":
        return await run_openai_style_chat(
            cfg["base_url"], api_key, model, prompt, system, history, temperature, max_tokens, use_tools,
            extra_headers=cfg.get("extra_headers"),
        )

    raise HTTPException(status_code=400, detail=f"Fournisseur non implemente: {provider}")


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
    """Liste les fournisseurs disponibles, leurs modeles, et si leur cle API est configuree."""
    result = {}
    for name, cfg in PROVIDERS.items():
        result[name] = {
            "models": cfg["models"],
            "default_model": cfg["default_model"],
            "configured": bool(os.getenv(cfg["key_env"])),
        }
    return {
        "providers": result,
        "current_brain": current_brain,
        "tools_available": [t["name"] for t in tools.TOOLS],
        "connector_configured": bool(os.getenv("GAS_WEBAPP_URL")),
        "web_search_configured": bool(os.getenv("TAVILY_API_KEY")),
    }


@app.get("/boot", response_model=BootResponse)
async def boot():
    """Message d'accueil fixe affiche/lu par le frontend a la connexion (pas de LLM,
    pour un demarrage instantane), protege par un try/except global."""
    try:
        return BootResponse(message=persona.BOOT_MESSAGE)
    except Exception:
        return BootResponse(message="Systeme en ligne.")


@app.post("/switch-brain", response_model=SwitchBrainResponse)
async def switch_brain(req: SwitchBrainRequest):
    """Change dynamiquement le fournisseur / modele LLM actif par defaut."""
    provider = req.provider.lower().strip()

    if provider not in PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Fournisseur inconnu '{provider}'. Choix possibles: {list(PROVIDERS.keys())}",
        )

    model = req.model or PROVIDERS[provider]["default_model"]
    current_brain["provider"] = provider
    current_brain["model"] = model

    return SwitchBrainResponse(
        message=f"Cerveau actif change pour '{provider}' ({model}).",
        current_brain=current_brain,
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    Envoie un prompt au LLM actif (ou a un fournisseur/modele precise ponctuellement), avec
    la personnalite JARVIS toujours appliquee, enrichie a chaque requete de la date/heure
    courante et du cerveau actif. L'historique de conversation transmis par le frontend est
    reinjecte pour que JARVIS ne perde jamais le fil.

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

    model = req.model or (
        current_brain["model"] if provider == current_brain["provider"] else PROVIDERS[provider]["default_model"]
    )

    use_tools_flag = req.use_tools if req.use_tools is not None else True

    # ------------------------------------------------------------------------
    # Court-circuit 1 : Total Recall (memorisation instantanee)
    # ------------------------------------------------------------------------
    if use_tools_flag:
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
    switch_target = persona.detect_brain_switch(req.prompt)
    if switch_target and switch_target in PROVIDERS:
        current_brain["provider"] = switch_target
        current_brain["model"] = PROVIDERS[switch_target]["default_model"]
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
    # Appel LLM normal, protege globalement contre toute exception imprevue
    # ------------------------------------------------------------------------
    effective_system = persona.build_effective_system_prompt(
        current_datetime=current_datetime_str(),
        current_provider=current_brain["provider"],
        extra_instructions=req.system,
    )

    try:
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
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
