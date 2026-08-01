"""
Backend Python léger - FastAPI multi-fournisseurs LLM (gratuit) + Function Calling
Fournisseurs supportés : Gemini, Groq, Mistral, OpenRouter
Outils (tools) : connecteur Google Apps Script (Drive / Gmail / Docs)
Déploiement : Render (voir Procfile)
"""

import os
import json
import httpx
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import tools
import persona
import total_recall

# ----------------------------------------------------------------------------
# Configuration des fournisseurs (clés API lues via variables d'environnement)
# ----------------------------------------------------------------------------

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "gemini": {
        "key_env": "GEMINI_API_KEY",
        "models": ["gemini-1.5-flash", "gemini-1.5-pro"],
        "default_model": "gemini-1.5-flash",
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

# État global : le "cerveau" actuellement actif
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
    title="Multi-LLM Backend avec Function Calling",
    description=(
        "Backend léger FastAPI - Multi-fournisseurs LLM gratuits (Gemini, Groq, Mistral, "
        "OpenRouter) avec function calling vers Google Drive / Gmail via Apps Script."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------------
# Schémas Pydantic
# ----------------------------------------------------------------------------

class ChatRequest(BaseModel):
    prompt: str
    provider: Optional[str] = None   # override ponctuel, sans changer l'état global
    model: Optional[str] = None      # override ponctuel du modèle
    system: Optional[str] = None     # instructions ponctuelles additionnelles (s'ajoutent à la personnalité JARVIS, ne la remplacent pas)
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 1024
    use_tools: Optional[bool] = True  # active/désactive le function calling pour cette requête


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
    """Appelle l'endpoint /chat/completions et renvoie la réponse JSON brute (pas seulement le texte)."""
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

    async with httpx.AsyncClient(timeout=60) as client:
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
    temperature: float,
    max_tokens: int,
    use_tools: bool,
    extra_headers: Optional[Dict[str, str]] = None,
) -> (str, List[ToolCallLog]):
    """Gère la boucle complète prompt -> (appels d'outils éventuels) -> réponse finale,
    pour tout fournisseur compatible OpenAI (Groq, Mistral, OpenRouter)."""

    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
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
            raise HTTPException(status_code=502, detail=f"Réponse inattendue: {data}")

        last_message = message
        tool_calls = message.get("tool_calls")

        if not tool_calls:
            return message.get("content") or "", tools_used

        # Ajoute le tour de l'assistant contenant les demandes d'appel d'outils
        messages.append(message)

        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            raw_args = tc["function"].get("arguments") or "{}"
            try:
                fn_args = json.loads(raw_args)
            except json.JSONDecodeError:
                fn_args = {}

            result = await tools.execute_tool(fn_name, fn_args)
            tools_used.append(ToolCallLog(name=fn_name, arguments=fn_args, result=result))

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": fn_name,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    # Nombre maximum d'itérations atteint : on renvoie le dernier contenu disponible
    fallback_text = last_message.get("content") or "(Réponse tronquée après plusieurs appels d'outils.)"
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

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=payload)
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=f"Erreur Gemini: {r.text}")
    return r.json()


async def run_gemini_chat(
    model: str,
    api_key: str,
    prompt: str,
    system: Optional[str],
    temperature: float,
    max_tokens: int,
    use_tools: bool,
) -> (str, List[ToolCallLog]):
    contents: List[Dict[str, Any]] = [{"role": "user", "parts": [{"text": prompt}]}]
    llm_tools = tools.get_gemini_tools() if use_tools else None
    tools_used: List[ToolCallLog] = []

    last_parts: List[Dict[str, Any]] = []
    for _ in range(MAX_TOOL_ITERATIONS):
        data = await call_gemini_raw(model, api_key, contents, system, llm_tools, temperature, max_tokens)
        try:
            candidate = data["candidates"][0]
            parts = candidate["content"]["parts"]
        except (KeyError, IndexError):
            raise HTTPException(status_code=502, detail=f"Réponse Gemini inattendue: {data}")

        last_parts = parts
        function_call_part = next((p for p in parts if "functionCall" in p), None)

        if not function_call_part:
            text = "".join(p.get("text", "") for p in parts)
            return text, tools_used

        # Ajoute le tour du modèle contenant la demande d'appel de fonction
        contents.append({"role": "model", "parts": parts})

        fc = function_call_part["functionCall"]
        fn_name = fc.get("name", "")
        fn_args = fc.get("args", {}) or {}

        result = await tools.execute_tool(fn_name, fn_args)
        tools_used.append(ToolCallLog(name=fn_name, arguments=fn_args, result=result))

        contents.append(
            {
                "role": "function",
                "parts": [{"functionResponse": {"name": fn_name, "response": {"result": result}}}],
            }
        )

    fallback_text = "".join(p.get("text", "") for p in last_parts) or "(Réponse tronquée après plusieurs appels d'outils.)"
    return fallback_text, tools_used


# ----------------------------------------------------------------------------
# Dispatch générique par fournisseur
# ----------------------------------------------------------------------------

async def dispatch_to_provider(
    provider: str,
    model: str,
    prompt: str,
    system: Optional[str],
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
            detail=f"Clé API manquante pour '{provider}'. Définissez la variable d'environnement {cfg['key_env']}.",
        )

    if cfg["family"] == "gemini":
        return await run_gemini_chat(model, api_key, prompt, system, temperature, max_tokens, use_tools)

    if cfg["family"] == "openai_compatible":
        return await run_openai_style_chat(
            cfg["base_url"],
            api_key,
            model,
            prompt,
            system,
            temperature,
            max_tokens,
            use_tools,
            extra_headers=cfg.get("extra_headers"),
        )

    raise HTTPException(status_code=400, detail=f"Fournisseur non implémenté: {provider}")


# ----------------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------------

@app.get("/")
async def root():
    return {"message": "Multi-LLM Backend actif", "current_brain": current_brain}


@app.get("/health")
async def health():
    """Endpoint de santé utilisé par Render pour vérifier que le service tourne."""
    return {"status": "ok"}


@app.get("/providers")
async def list_providers():
    """Liste les fournisseurs disponibles, leurs modèles, et si leur clé API est configurée."""
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
    }


@app.get("/boot", response_model=BootResponse)
async def boot():
    """
    Message d'accueil fixe affiché/lu par le frontend à la connexion.
    Ce message n'est PAS généré par un LLM : il est renvoyé tel quel pour garantir
    un boot instantané et toujours identique, sans dépendre d'un appel API externe.
    """
    return BootResponse(message=persona.BOOT_MESSAGE)


@app.post("/switch-brain", response_model=SwitchBrainResponse)
async def switch_brain(req: SwitchBrainRequest):
    """Change dynamiquement le fournisseur / modèle LLM actif par défaut."""
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
        message=f"Cerveau actif changé pour '{provider}' ({model}).",
        current_brain=current_brain,
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    Envoie un prompt au LLM actif (ou à un fournisseur/modèle précisé ponctuellement), avec
    la personnalité JARVIS (persona.py) toujours appliquée comme prompt système par défaut.
    Le champ `system` de la requête, s'il est fourni, s'ajoute à cette personnalité au lieu
    de la remplacer.
    Si le LLM décide d'utiliser un outil (Drive, Gmail...), celui-ci est exécuté via le
    connecteur Apps Script, le résultat est renvoyé au LLM, et sa réponse finale (enrichie
    du résultat de l'outil) est retournée dans `response`. Les outils appelés sont listés
    dans `tools_used`.
    """
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Le champ 'prompt' ne peut pas être vide.")

    provider = (req.provider or current_brain["provider"]).lower().strip()
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Fournisseur inconnu: {provider}")

    model = req.model or (
        current_brain["model"] if provider == current_brain["provider"] else PROVIDERS[provider]["default_model"]
    )

    # ------------------------------------------------------------------------
    # Total Recall : si le prompt commence par "Rappelle-toi que...", "Souviens-toi
    # que..." ou "Note que...", on court-circuite le LLM et on mémorise immédiatement,
    # sans attendre de round-trip d'inférence. Désactivable via use_tools=False.
    # ------------------------------------------------------------------------
    use_tools_flag = req.use_tools if req.use_tools is not None else True
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
                response=response_text,
                tools_used=[
                    ToolCallLog(name="remember_note", arguments={"content": memory_content}, result=result)
                ],
            )

    effective_system = persona.build_effective_system_prompt(req.system)

    answer, tools_used = await dispatch_to_provider(
        provider=provider,
        model=model,
        prompt=req.prompt,
        system=effective_system,
        temperature=req.temperature or 0.7,
        max_tokens=req.max_tokens or 1024,
        use_tools=use_tools_flag,
    )

    return ChatResponse(provider=provider, model=model, response=answer, tools_used=tools_used)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)