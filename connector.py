"""
Connector - Fonctions Python (tools) qui répondent aux appels vers le Web App Google Apps Script
(Drive / Docs / Sheets / Gmail / Calendar) déployé séparément.
"""

import os
import json
import httpx
from typing import Any, Dict, List, Optional

GAS_WEBAPP_URL = os.getenv("GAS_WEBAPP_URL")
GAS_API_SECRET = os.getenv("GAS_API_SECRET")  # Optionnel, doit correspondre à API_SECRET côté Apps Script
GAS_TIMEOUT = 30.0  # Secondes


class ConnectorError(Exception):
    """Erreur levée quand l'appel au connecteur Apps Script échoue."""


# Instance globale réutilisable pour préserver les connexions TCP/TLS (Connection Pooling)
_client: Optional[httpx.AsyncClient] = None


def get_httpx_client() -> httpx.AsyncClient:
    """Retourne ou initialise l'instance unique d'httpx.AsyncClient."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=GAS_TIMEOUT,
            follow_redirects=True,
            # Force la conservation de la méthode POST sur les redirections 301/302 si nécessaire
            trust_env=True
        )
    return _client


async def close_httpx_client():
    """Ferme proprement le client HTTP lors de l'arrêt de l'application backend."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def call_gas(action: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """Envoie une requête POST au Web App Apps Script et renvoie le champ 'data' de la réponse."""
    if not GAS_WEBAPP_URL:
        raise ConnectorError(
            "GAS_WEBAPP_URL n'est pas configurée. Définis cette variable d'environnement "
            "avec l'URL /exec de ton déploiement Apps Script."
        )
    
    payload = {"action": action, "params": params or {}}
    if GAS_API_SECRET:
        payload["token"] = GAS_API_SECRET

    client = get_httpx_client()

    try:
        r = await client.post(GAS_WEBAPP_URL, json=payload)
    except httpx.RequestError as e:
        raise ConnectorError(f"Impossible de contacter le connecteur Apps Script: {e}")

    if r.status_code != 200:
        raise ConnectorError(f"Le connecteur Apps Script a renvoyé une erreur HTTP {r.status_code}: {r.text[:300]}")

    try:
        body = r.json()
    except json.JSONDecodeError:
        raise ConnectorError(f"Réponse non-JSON du connecteur Apps Script: {r.text[:300]}")

    if not body.get("success"):
        raise ConnectorError(body.get("error", "Erreur inconnue du connecteur Apps Script."))

    return body.get("data")


# ----------------------------------------------------------------------------
# Drive
# ----------------------------------------------------------------------------

async def search_google_drive(query: str) -> Dict[str, Any]:
    """Recherche des fichiers/dossiers dans Google Drive et renvoie noms, ID et extraits."""
    results = await call_gas("search_drive", {"query": query})
    return {"results": results}


async def list_drive_files(folder_name: Optional[str] = None, max_results: int = 20) -> Dict[str, Any]:
    """Liste les fichiers d'un dossier Drive (racine si non précisé)."""
    results = await call_gas("list_drive", {"folderName": folder_name, "maxResults": max_results})
    return {"results": results}


async def get_drive_graph(max_results: int = 300) -> Dict[str, Any]:
    """Récupère une vue plate des fichiers Drive de l'utilisateur (id, nom, type, dossier parent),
    utilisée pour construire le graphe nodes/links de la Galaxie 3D côté frontend."""
    result = await call_gas("get_drive_graph", {"maxResults": max_results})
    return result if isinstance(result, dict) else {"files": result}


async def read_drive_file(file_id_or_name: str) -> Dict[str, Any]:
    """Lit le contenu textuel d'un fichier Drive (Doc, Sheet, ou fichier texte)."""
    result = await call_gas("read_drive_file", {"fileIdOrName": file_id_or_name})
    return result if isinstance(result, dict) else {"content": result}


async def get_file_details(file_id_or_name: str) -> Dict[str, Any]:
    """Récupère les métadonnées d'un fichier Drive (type, taille, dernière modification, URL)."""
    result = await call_gas("get_file_details", {"fileIdOrName": file_id_or_name})
    return result if isinstance(result, dict) else {"details": result}


async def organize_drive_file(file_id_or_name: str, target_folder_name: str) -> Dict[str, Any]:
    """Déplace un fichier Drive vers un autre dossier."""
    return await call_gas(
        "move_drive_file", {"fileIdOrName": file_id_or_name, "targetFolderName": target_folder_name}
    )


async def save_note_to_drive(title: str, content: str) -> Dict[str, Any]:
    """Crée une note (Google Doc) dans le dossier Drive dédié."""
    return await call_gas("create_note", {"type": "doc", "title": title, "content": content})


async def remember_note(content: str) -> Dict[str, Any]:
    """Ajoute une information à la mémoire persistante (Memoire_Jarvis.txt) sur Drive."""
    return await call_gas("remember", {"content": content})


# ----------------------------------------------------------------------------
# Docs
# ----------------------------------------------------------------------------

async def create_google_doc(title: str, content: str) -> Dict[str, Any]:
    """Crée un nouveau Google Doc structuré avec le titre et le contenu donnés."""
    return await call_gas("create_doc", {"title": title, "content": content})


async def read_google_doc(doc_id_or_name: str) -> Dict[str, Any]:
    """Lit le contenu intégral d'un Google Doc."""
    result = await call_gas("read_doc", {"docIdOrName": doc_id_or_name})
    return result if isinstance(result, dict) else {"content": result}


async def write_google_doc(doc_id_or_name: str, content: str, mode: str = "append") -> Dict[str, Any]:
    """Ajoute (mode='append') ou remplace (mode='replace') le contenu d'un Google Doc."""
    return await call_gas("write_doc", {"docIdOrName": doc_id_or_name, "content": content, "mode": mode})


# ----------------------------------------------------------------------------
# Sheets
# ----------------------------------------------------------------------------

async def create_google_sheet(
    title: str, headers: Optional[List[str]] = None, rows: Optional[List[List[str]]] = None
) -> Dict[str, Any]:
    """Crée une nouvelle Google Sheet, avec en-têtes et lignes de données initiales optionnelles."""
    return await call_gas("create_sheet", {"title": title, "headers": headers or [], "rows": rows or []})


async def read_google_sheet(
    sheet_id_or_name: str, range_: Optional[str] = None, sheet_name: Optional[str] = None
) -> Dict[str, Any]:
    """Lit une plage de cellules d'une Google Sheet, sur un onglet précis si fourni."""
    result = await call_gas(
        "read_sheet", {"sheetIdOrName": sheet_id_or_name, "range": range_, "sheetName": sheet_name}
    )
    return result if isinstance(result, dict) else {"values": result}


async def write_google_sheet(
    sheet_id_or_name: str, range_: str, values: List[List[str]], sheet_name: Optional[str] = None
) -> Dict[str, Any]:
    """Écrit/met à jour des valeurs dans une plage précise d'une Google Sheet."""
    return await call_gas(
        "write_sheet",
        {"sheetIdOrName": sheet_id_or_name, "range": range_, "values": values, "sheetName": sheet_name},
    )


async def update_sheet_cell(
    sheet_id_or_name: str, cell: str, value: str, sheet_name: Optional[str] = None
) -> Dict[str, Any]:
    """Modifie la valeur d'une seule cellule dans une Google Sheet."""
    return await call_gas(
        "update_cell", {"sheetIdOrName": sheet_id_or_name, "cell": cell, "value": value, "sheetName": sheet_name}
    )


async def append_google_sheet_row(
    sheet_id_or_name: str, row_values: List[str], sheet_name: Optional[str] = None
) -> Dict[str, Any]:
    """Ajoute une nouvelle ligne à la fin d'une Google Sheet."""
    return await call_gas(
        "append_sheet_row", {"sheetIdOrName": sheet_id_or_name, "rowValues": row_values, "sheetName": sheet_name}
    )


# ----------------------------------------------------------------------------
# Gmail
# ----------------------------------------------------------------------------

async def get_unread_emails() -> Dict[str, Any]:
    """Récupère un résumé des derniers e-mails non lus sur Gmail."""
    results = await call_gas("read_email", {"maxResults": 5})
    return {"emails": results}


async def send_gmail(to: str, subject: str, body: str) -> Dict[str, Any]:
    """Envoie un e-mail via Gmail."""
    return await call_gas("send_email", {"to": to, "subject": subject, "body": body})


async def create_gmail_draft(to: str, subject: str, body: str) -> Dict[str, Any]:
    """Crée un brouillon d'e-mail dans Gmail sans l'envoyer."""
    return await call_gas("create_draft", {"to": to, "subject": subject, "body": body})


# ----------------------------------------------------------------------------
# Calendar
# ----------------------------------------------------------------------------

async def list_calendar_events(max_results: int = 10, days_ahead: int = 7) -> Dict[str, Any]:
    """Liste les prochains événements du calendrier de l'utilisateur."""
    results = await call_gas("list_calendar_events", {"maxResults": max_results, "daysAhead": days_ahead})
    return {"events": results}


async def create_calendar_event(
    title: str, start_datetime: str, end_datetime: str, description: str = ""
) -> Dict[str, Any]:
    """Crée un événement dans le calendrier Google de l'utilisateur."""
    return await call_gas(
        "create_calendar_event",
        {"title": title, "startDatetime": start_datetime, "endDatetime": end_datetime, "description": description},
    )
