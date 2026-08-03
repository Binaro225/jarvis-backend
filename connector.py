"""
Connector - Fonctions Python (tools) qui appellent des Web Apps Google Apps Script
(Drive / Docs / Sheets / Gmail / Calendar + scripts specialises, ex: prediction football)
deployees separement.

Correctif important : les Web Apps Apps Script repondent souvent par une redirection
HTTP 302 vers la reponse reelle. httpx ne suit PAS les redirections par defaut,
d'ou l'erreur "HTTP 302 Moved Temporarily" observee precedemment. Le client est
maintenant cree avec follow_redirects=True pour corriger definitivement ce point.

ROUTING MULTI-GAS
  Plusieurs Web Apps Apps Script peuvent etre deployees independamment (ex: un connecteur
  Google generique, et un script dedie aux predictions football). Chaque "endpoint" est
  identifie par un nom logique et resolu vers une URL via une variable d'environnement :

    default    -> GAS_WEBAPP_URL       (Drive/Docs/Sheets/Gmail/Calendar)
    prediction -> GAS_PREDICTION_URL   (script de prediction football)

  Pour ajouter un nouveau script GAS specialise plus tard, il suffit d'ajouter une entree
  a GAS_ENDPOINTS ci-dessous et de definir la variable d'environnement correspondante.
"""

import os
import json
import httpx
from typing import Any, Dict, List, Optional

GAS_ENDPOINTS: Dict[str, Optional[str]] = {
    "default": os.getenv("GAS_WEBAPP_URL"),
    "prediction": os.getenv("GAS_PREDICTION_URL"),
}
GAS_API_SECRET = os.getenv("GAS_API_SECRET")  # optionnel, doit correspondre a API_SECRET cote Apps Script
GAS_TIMEOUT = 30  # secondes


class ConnectorError(Exception):
    """Erreur levee quand l'appel au connecteur Apps Script echoue."""


async def call_gas(action: str, params: Optional[Dict[str, Any]] = None, endpoint: str = "default") -> Dict[str, Any]:
    """Envoie une requete POST au Web App Apps Script identifie par 'endpoint' et renvoie le
    champ 'data' de la reponse. follow_redirects=True corrige le 302 typique des Web Apps
    Apps Script."""
    webapp_url = GAS_ENDPOINTS.get(endpoint)
    if not webapp_url:
        env_hint = "GAS_WEBAPP_URL" if endpoint == "default" else f"GAS_{endpoint.upper()}_URL"
        raise ConnectorError(
            f"Aucune URL configuree pour le connecteur Apps Script '{endpoint}'. Definis la "
            f"variable d'environnement {env_hint} avec l'URL /exec du deploiement correspondant."
        )
    payload = {"action": action, "params": params or {}}
    if GAS_API_SECRET:
        payload["token"] = GAS_API_SECRET

    try:
        async with httpx.AsyncClient(timeout=GAS_TIMEOUT, follow_redirects=True) as client:
            r = await client.post(webapp_url, json=payload)
    except httpx.RequestError as e:
        raise ConnectorError(f"Impossible de contacter le connecteur Apps Script '{endpoint}': {e}")

    if r.status_code != 200:
        raise ConnectorError(f"Le connecteur Apps Script '{endpoint}' a renvoye une erreur HTTP {r.status_code}: {r.text[:300]}")

    try:
        body = r.json()
    except json.JSONDecodeError:
        raise ConnectorError(f"Reponse non-JSON du connecteur Apps Script '{endpoint}': {r.text[:300]}")

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
    """Liste les fichiers d'un dossier Drive (racine si non precise)."""
    results = await call_gas("list_drive", {"folderName": folder_name, "maxResults": max_results})
    return {"results": results}


async def get_drive_graph(max_results: int = 300) -> Dict[str, Any]:
    """Recupere une vue plate des fichiers Drive de l'utilisateur (id, nom, type, dossier parent),
    utilisee pour construire le graphe nodes/links de la Galaxie 3D cote frontend."""
    result = await call_gas("get_drive_graph", {"maxResults": max_results})
    return result if isinstance(result, dict) else {"files": result}


async def read_drive_file(file_id_or_name: str) -> Dict[str, Any]:
    """Lit le contenu textuel d'un fichier Drive (Doc, Sheet, ou fichier texte)."""
    result = await call_gas("read_drive_file", {"fileIdOrName": file_id_or_name})
    return result if isinstance(result, dict) else {"content": result}


async def get_file_details(file_id_or_name: str) -> Dict[str, Any]:
    """Recupere les metadonnees d'un fichier Drive (type, taille, derniere modification, URL),
    sans en lire le contenu integral. Utile pour verifier l'existence/le type avant d'agir."""
    result = await call_gas("get_file_details", {"fileIdOrName": file_id_or_name})
    return result


async def organize_drive_file(file_id_or_name: str, target_folder_name: str) -> Dict[str, Any]:
    """Deplace un fichier Drive vers un autre dossier."""
    result = await call_gas(
        "move_drive_file", {"fileIdOrName": file_id_or_name, "targetFolderName": target_folder_name}
    )
    return result


async def save_note_to_drive(title: str, content: str) -> Dict[str, Any]:
    """Cree une note (Google Doc) dans le dossier Drive dedie."""
    result = await call_gas("create_note", {"type": "doc", "title": title, "content": content})
    return result


async def remember_note(content: str) -> Dict[str, Any]:
    """Ajoute une information a la memoire persistante (Memoire_Jarvis.txt) sur Drive.
    Contrairement a save_note_to_drive, n'ouvre pas un nouveau document a chaque appel :
    la ligne est ajoutee, horodatee, a la suite d'un unique fichier cumulatif."""
    result = await call_gas("remember", {"content": content})
    return result


# ----------------------------------------------------------------------------
# Docs
# ----------------------------------------------------------------------------

async def create_google_doc(title: str, content: str) -> Dict[str, Any]:
    """Cree un nouveau Google Doc structure avec le titre et le contenu donnes."""
    result = await call_gas("create_doc", {"title": title, "content": content})
    return result


async def read_google_doc(doc_id_or_name: str) -> Dict[str, Any]:
    """Lit le contenu integral d'un Google Doc."""
    result = await call_gas("read_doc", {"docIdOrName": doc_id_or_name})
    return result if isinstance(result, dict) else {"content": result}


async def write_google_doc(doc_id_or_name: str, content: str, mode: str = "append") -> Dict[str, Any]:
    """Ajoute (mode='append') ou remplace (mode='replace') le contenu d'un Google Doc."""
    result = await call_gas("write_doc", {"docIdOrName": doc_id_or_name, "content": content, "mode": mode})
    return result


# ----------------------------------------------------------------------------
# Sheets
# ----------------------------------------------------------------------------

async def create_google_sheet(title: str, headers: Optional[List[str]] = None, rows: Optional[List[List[str]]] = None) -> Dict[str, Any]:
    """Cree une nouvelle Google Sheet, avec en-tetes et lignes de donnees initiales optionnelles."""
    result = await call_gas("create_sheet", {"title": title, "headers": headers or [], "rows": rows or []})
    return result


async def read_google_sheet(sheet_id_or_name: str, range_: Optional[str] = None, sheet_name: Optional[str] = None) -> Dict[str, Any]:
    """Lit une plage de cellules d'une Google Sheet, sur un onglet precis si fourni."""
    result = await call_gas(
        "read_sheet", {"sheetIdOrName": sheet_id_or_name, "range": range_, "sheetName": sheet_name}
    )
    return result if isinstance(result, dict) else {"values": result}


async def write_google_sheet(
    sheet_id_or_name: str, range_: str, values: List[List[str]], sheet_name: Optional[str] = None
) -> Dict[str, Any]:
    """Ecrit/met a jour des valeurs dans une plage precise d'une Google Sheet."""
    result = await call_gas(
        "write_sheet",
        {"sheetIdOrName": sheet_id_or_name, "range": range_, "values": values, "sheetName": sheet_name},
    )
    return result


async def update_sheet_cell(
    sheet_id_or_name: str, cell: str, value: str, sheet_name: Optional[str] = None
) -> Dict[str, Any]:
    """Modifie la valeur d'une seule cellule dans une Google Sheet."""
    result = await call_gas(
        "update_cell", {"sheetIdOrName": sheet_id_or_name, "cell": cell, "value": value, "sheetName": sheet_name}
    )
    return result


async def append_google_sheet_row(
    sheet_id_or_name: str, row_values: List[str], sheet_name: Optional[str] = None
) -> Dict[str, Any]:
    """Ajoute une nouvelle ligne a la fin d'une Google Sheet (sur l'onglet precise si fourni)."""
    result = await call_gas(
        "append_sheet_row", {"sheetIdOrName": sheet_id_or_name, "rowValues": row_values, "sheetName": sheet_name}
    )
    return result


# ----------------------------------------------------------------------------
# Gmail
# ----------------------------------------------------------------------------

async def get_unread_emails() -> Dict[str, Any]:
    """Recupere un resume des derniers e-mails non lus sur Gmail."""
    results = await call_gas("read_email", {"maxResults": 5})
    return {"emails": results}


async def send_gmail(to: str, subject: str, body: str) -> Dict[str, Any]:
    """Envoie un e-mail via Gmail."""
    result = await call_gas("send_email", {"to": to, "subject": subject, "body": body})
    return result


async def create_gmail_draft(to: str, subject: str, body: str) -> Dict[str, Any]:
    """Cree un brouillon d'e-mail dans Gmail sans l'envoyer."""
    result = await call_gas("create_draft", {"to": to, "subject": subject, "body": body})
    return result


# ----------------------------------------------------------------------------
# Calendar
# ----------------------------------------------------------------------------

async def list_calendar_events(max_results: int = 10, days_ahead: int = 7) -> Dict[str, Any]:
    """Liste les prochains evenements du calendrier de l'utilisateur."""
    results = await call_gas("list_calendar_events", {"maxResults": max_results, "daysAhead": days_ahead})
    return {"events": results}


async def create_calendar_event(title: str, start_datetime: str, end_datetime: str, description: str = "") -> Dict[str, Any]:
    """Cree un evenement dans le calendrier Google de l'utilisateur."""
    result = await call_gas(
        "create_calendar_event",
        {"title": title, "startDatetime": start_datetime, "endDatetime": end_datetime, "description": description},
    )
    return result


# ----------------------------------------------------------------------------
# Prediction Football (Web App Apps Script dediee, GAS_PREDICTION_URL)
# ----------------------------------------------------------------------------

async def predict_football_match(
    team_home: str,
    team_away: str,
    competition: str = "",
    match_date: str = "",
    form_home: str = "",
    form_away: str = "",
    h2h: str = "",
    xg_home: Optional[float] = None,
    xg_away: Optional[float] = None,
    notes: str = "",
) -> Dict[str, Any]:
    """Envoie un payload structure de statistiques de match au script GAS de prediction
    football (endpoint 'prediction', GAS_PREDICTION_URL). Les statistiques (forme, H2H, xG)
    doivent avoir ete recueillies au prealable, typiquement via l'outil web_search."""
    payload = {
        "teamHome": team_home,
        "teamAway": team_away,
        "competition": competition,
        "matchDate": match_date,
        "formHome": form_home,
        "formAway": form_away,
        "h2h": h2h,
        "xgHome": xg_home,
        "xgAway": xg_away,
        "notes": notes,
    }
    result = await call_gas("predict_match", payload, endpoint="prediction")
    return result
