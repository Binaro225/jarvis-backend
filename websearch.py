"""
Outil de recherche web gratuit, basé sur la bibliothèque `duckduckgo_search`
(aucune clé API requise — contrairement à Tavily ou Google Custom Search, qui
nécessiteraient une variable d'environnement supplémentaire).

Le paquet PyPI `duckduckgo_search` a par le passé été renommé `ddgs` suite à une
demande de DuckDuckGo ; on tente donc les deux noms d'import pour rester robuste
si l'un des deux venait à disparaître de l'index au moment du déploiement.
"""

import asyncio
from typing import Any, Dict, List

try:
    from duckduckgo_search import DDGS
except ImportError:  # pragma: no cover - fallback si le paquet a été renommé
    from ddgs import DDGS  # type: ignore

DEFAULT_MAX_RESULTS = 5
MIN_RESULTS = 3


def _search_sync(query: str, max_results: int) -> List[Dict[str, str]]:
    """Appel bloquant (la bibliothèque n'est pas async) — à exécuter via asyncio.to_thread."""
    results: List[Dict[str, str]] = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, region="fr-fr", safesearch="moderate", max_results=max_results):
            results.append(
                {
                    "title": r.get("title", "") or "",
                    "url": r.get("href") or r.get("url") or "",
                    "snippet": r.get("body", "") or "",
                }
            )
    return results


async def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> Dict[str, Any]:
    """Recherche sur le web et renvoie les meilleurs résultats (titre, URL, extrait)."""
    query = (query or "").strip()
    if not query:
        return {"error": "Le paramètre 'query' est requis pour web_search."}

    max_results = max(MIN_RESULTS, min(max_results or DEFAULT_MAX_RESULTS, DEFAULT_MAX_RESULTS))

    try:
        results = await asyncio.to_thread(_search_sync, query, max_results)
    except Exception as e:  # la lib peut lever divers types d'erreurs réseau/rate-limit
        return {"error": f"La recherche web a échoué : {e}"}

    if not results:
        return {"query": query, "results": [], "message": "Aucun résultat trouvé."}

    return {"query": query, "results": results}
