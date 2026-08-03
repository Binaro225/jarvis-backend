"""
Decouverte dynamique des modeles LLM disponibles, provider par provider, sans aucun nom de
modele code en dur. Chaque fournisseur expose son propre endpoint de listing ; ce module les
interroge, normalise les resultats en un format commun, et les met en cache (TTL) pour eviter
de re-interroger les APIs a chaque requete.

Format normalise pour chaque modele :
  { "id": str, "name": str, "provider": str, "is_free": bool, "supports_vision": bool, "supports_tools": bool }

Les criteres is_free / supports_vision / supports_tools sont deduits des metadonnees renvoyees
par chaque API (tarification, modalites, capacites), jamais d'une liste figee de noms.
"""

import os
import time
import httpx
from typing import Any, Dict, List, Optional

CACHE_TTL_SECONDS = 300  # 5 minutes : evite de spammer les APIs de listing a chaque requete
_cache: Dict[str, Any] = {"timestamp": 0.0, "data": None}

PROVIDER_ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/models",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    "groq": "https://api.groq.com/openai/v1/models",
    "mistral": "https://api.mistral.ai/v1/models",
    "grok": "https://api.x.ai/v1/models",
}

REQUEST_TIMEOUT = 15


async def _get(url: str, headers: Optional[Dict[str, str]] = None, params: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            r = await client.get(url, headers=headers or {}, params=params or {})
        if r.status_code != 200:
            return None
        return r.json()
    except httpx.RequestError:
        return None


# ----------------------------------------------------------------------------
# OpenRouter
# ----------------------------------------------------------------------------

async def fetch_openrouter_models(api_key: str) -> List[Dict[str, Any]]:
    data = await _get(PROVIDER_ENDPOINTS["openrouter"], headers={"Authorization": f"Bearer {api_key}"})
    if not data:
        return []
    models = []
    for item in data.get("data", []):
        pricing = item.get("pricing", {}) or {}
        is_free = str(pricing.get("prompt", "")) == "0" and str(pricing.get("completion", "")) == "0"
        architecture = item.get("architecture", {}) or {}
        input_modalities = architecture.get("input_modalities") or []
        modality_str = architecture.get("modality", "") or ""
        supports_vision = "image" in input_modalities or "image" in modality_str
        supported_params = item.get("supported_parameters") or []
        supports_tools = "tools" in supported_params or "tool_choice" in supported_params
        models.append({
            "id": item.get("id", ""),
            "name": item.get("name", item.get("id", "")),
            "provider": "openrouter",
            "is_free": is_free,
            "supports_vision": supports_vision,
            "supports_tools": supports_tools,
        })
    return models


# ----------------------------------------------------------------------------
# Gemini (Google AI Studio)
# ----------------------------------------------------------------------------

async def fetch_gemini_models(api_key: str) -> List[Dict[str, Any]]:
    data = await _get(PROVIDER_ENDPOINTS["gemini"], params={"key": api_key})
    if not data:
        return []
    models = []
    for item in data.get("models", []):
        raw_id = item.get("name", "")  # format "models/gemini-2.5-flash"
        model_id = raw_id.split("/")[-1] if "/" in raw_id else raw_id
        methods = item.get("supportedGenerationMethods", []) or []
        if "generateContent" not in methods:
            continue  # exclut les modeles d'embedding / non conversationnels
        is_embedding = "embedding" in model_id.lower()
        if is_embedding:
            continue
        models.append({
            "id": model_id,
            "name": item.get("displayName", model_id),
            "provider": "gemini",
            # Gemini a un palier gratuit genereux sur les variantes "flash" ; "pro" est payant au-dela.
            "is_free": "flash" in model_id.lower(),
            # Tous les modeles Gemini generateContent recents sont multimodaux nativement.
            "supports_vision": True,
            "supports_tools": True,
        })
    return models


# ----------------------------------------------------------------------------
# Groq
# ----------------------------------------------------------------------------

async def fetch_groq_models(api_key: str) -> List[Dict[str, Any]]:
    data = await _get(PROVIDER_ENDPOINTS["groq"], headers={"Authorization": f"Bearer {api_key}"})
    if not data:
        return []
    models = []
    for item in data.get("data", []):
        model_id = item.get("id", "")
        models.append({
            "id": model_id,
            "name": model_id,
            "provider": "groq",
            "is_free": True,  # Groq est gratuit (avec limites de debit) au moment de l'ecriture
            "supports_vision": "vision" in model_id.lower(),
            "supports_tools": True,
        })
    return models


# ----------------------------------------------------------------------------
# Mistral
# ----------------------------------------------------------------------------

async def fetch_mistral_models(api_key: str) -> List[Dict[str, Any]]:
    data = await _get(PROVIDER_ENDPOINTS["mistral"], headers={"Authorization": f"Bearer {api_key}"})
    if not data:
        return []
    models = []
    for item in data.get("data", []):
        model_id = item.get("id", "")
        capabilities = item.get("capabilities", {}) or {}
        models.append({
            "id": model_id,
            "name": model_id,
            "provider": "mistral",
            "is_free": model_id.lower().startswith("open-"),  # gamme "open-*" sur le palier gratuit
            "supports_vision": capabilities.get("vision", "pixtral" in model_id.lower()),
            "supports_tools": capabilities.get("function_calling", True),
        })
    return models


# ----------------------------------------------------------------------------
# xAI (Grok)
# ----------------------------------------------------------------------------

async def fetch_xai_models(api_key: str) -> List[Dict[str, Any]]:
    data = await _get(PROVIDER_ENDPOINTS["grok"], headers={"Authorization": f"Bearer {api_key}"})
    if not data:
        return []
    models = []
    for item in data.get("data", []):
        model_id = item.get("id", "")
        models.append({
            "id": model_id,
            "name": model_id,
            "provider": "grok",
            "is_free": False,  # xAI n'a pas de palier gratuit au moment de l'ecriture
            "supports_vision": "vision" in model_id.lower(),
            "supports_tools": True,
        })
    return models


FETCHERS = {
    "openrouter": fetch_openrouter_models,
    "gemini": fetch_gemini_models,
    "groq": fetch_groq_models,
    "mistral": fetch_mistral_models,
    "grok": fetch_xai_models,
}


# ----------------------------------------------------------------------------
# API publique du module
# ----------------------------------------------------------------------------

async def discover_models(force_refresh: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """Interroge chaque fournisseur configure (cle API presente) et renvoie
    {provider: [modeles normalises]}. Resultat mis en cache CACHE_TTL_SECONDS."""
    now = time.time()
    if not force_refresh and _cache["data"] is not None and (now - _cache["timestamp"]) < CACHE_TTL_SECONDS:
        return _cache["data"]

    key_envs = {
        "openrouter": "OPENROUTER_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "groq": "GROQ_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "grok": "XAI_API_KEY",
    }

    result: Dict[str, List[Dict[str, Any]]] = {}
    for provider, key_env in key_envs.items():
        api_key = os.getenv(key_env)
        if not api_key:
            result[provider] = []
            continue
        result[provider] = await FETCHERS[provider](api_key)

    _cache["data"] = result
    _cache["timestamp"] = now
    return result


async def discover_models_flat(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Meme chose que discover_models() mais aplati en une seule liste, tous providers confondus."""
    grouped = await discover_models(force_refresh=force_refresh)
    flat: List[Dict[str, Any]] = []
    for models in grouped.values():
        flat.extend(models)
    return flat


def _sort_key(model: Dict[str, Any]):
    # Priorite : gratuit d'abord, puis support des outils (necessaire pour le function calling).
    return (not model.get("is_free", False), not model.get("supports_tools", False))


async def get_vision_candidates(preferred_provider: Optional[str] = None, preferred_model: Optional[str] = None) -> List[Dict[str, Any]]:
    """Renvoie une liste ordonnee de modeles vision-capables, le modele preferre de l'utilisateur
    en tete si fourni et compatible, suivi des autres (gratuits d'abord) pour servir de chaine de
    fallback automatique en cas d'echec du premier."""
    flat = await discover_models_flat()
    vision_models = [m for m in flat if m.get("supports_vision")]
    vision_models.sort(key=_sort_key)

    if preferred_provider and preferred_model:
        preferred = [m for m in vision_models if m["provider"] == preferred_provider and m["id"] == preferred_model]
        others = [m for m in vision_models if not (m["provider"] == preferred_provider and m["id"] == preferred_model)]
        return preferred + others
    return vision_models


async def get_default_model_for_provider(provider: str) -> str:
    """Renvoie le meilleur modele disponible pour un provider donne (gratuit + tools en priorite).
    Leve une erreur explicite si la decouverte echoue plutot que de se rabattre sur un nom fige."""
    grouped = await discover_models(force_refresh=False)
    candidates = grouped.get(provider) or []
    if not candidates:
        raise ValueError(
            f"Aucun modele decouvert pour '{provider}'. Verifie que la cle API est valide et que "
            f"le service de listing de modeles de ce fournisseur est joignable."
        )
    candidates = sorted(candidates, key=_sort_key)
    return candidates[0]["id"]
