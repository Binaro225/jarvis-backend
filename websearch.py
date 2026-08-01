"""
Outil de recherche web temps reel base sur l'API Tavily (https://tavily.com), pensee
pour l'usage par des agents LLM (resultats courts, pertinents, avec un resume optionnel).

Necessite la variable d'environnement TAVILY_API_KEY. Si elle est absente, l'outil
renvoie une erreur explicite plutot que de planter silencieusement.
"""

import os
import httpx
from typing import Any, Dict, List

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
TAVILY_URL = "https://api.tavily.com/search"
DEFAULT_MAX_RESULTS = 5
MIN_RESULTS = 3
TAVILY_TIMEOUT = 20  # secondes


async def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> Dict[str, Any]:
    """Recherche sur le web via Tavily et renvoie les meilleurs resultats (titre, URL, extrait)."""
    query = (query or "").strip()
    if not query:
        return {"error": "Le parametre 'query' est requis pour web_search."}

    if not TAVILY_API_KEY:
        return {
            "error": (
                "TAVILY_API_KEY n'est pas configuree cote backend. "
                "Definis cette variable d'environnement pour activer la recherche web."
            )
        }

    max_results = max(MIN_RESULTS, min(max_results or DEFAULT_MAX_RESULTS, DEFAULT_MAX_RESULTS))

    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "include_answer": True,
        "max_results": max_results,
    }

    try:
        async with httpx.AsyncClient(timeout=TAVILY_TIMEOUT) as client:
            r = await client.post(TAVILY_URL, json=payload)
    except httpx.RequestError as e:
        return {"error": f"La recherche web a echoue (reseau) : {e}"}

    if r.status_code != 200:
        return {"error": f"La recherche web a echoue (HTTP {r.status_code}) : {r.text[:200]}"}

    try:
        data = r.json()
    except ValueError:
        return {"error": "Reponse Tavily illisible (non-JSON)."}

    raw_results: List[Dict[str, Any]] = data.get("results", []) or []
    results = [
        {
            "title": item.get("title", "") or "",
            "url": item.get("url", "") or "",
            "snippet": (item.get("content", "") or "")[:400],
        }
        for item in raw_results
    ]

    if not results:
        return {"query": query, "results": [], "message": "Aucun resultat trouve."}

    output: Dict[str, Any] = {"query": query, "results": results}
    if data.get("answer"):
        output["answer"] = data["answer"]
    return output
