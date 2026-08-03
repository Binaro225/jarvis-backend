"""
Definitions des outils (function calling) exposes au LLM, independamment du fournisseur.
Fournit :
  - TOOLS : definition canonique (nom, description, parametres)
  - get_openai_tools() : format utilise par Groq / Mistral / OpenRouter (API compatible OpenAI)
  - get_gemini_tools() : format utilise par l'API Gemini
  - execute_tool(name, args) : execute la fonction Python correspondante

Couverture "acces total et avance a l'ecosysteme Google" :
  Drive    : recherche, listing, lecture, deplacement/organisation, suppression
  Docs     : lecture, ecriture/ajout, creation structuree/stylee, modification
  Sheets   : lecture, ecriture, ajout, suppression de lignes, formatage/thèmes, formules, maj par cle
  Gmail    : lecture des non-lus, envoi, brouillon
  Calendar : liste des evenements, creation d'evenement
"""

from typing import Any, Dict, List

import connector
import websearch

# ----------------------------------------------------------------------------
# Definition canonique des outils
# ----------------------------------------------------------------------------

TOOLS: List[Dict[str, Any]] = [
    # ---------------- Drive ----------------
    {
        "name": "search_google_drive",
        "description": "Recherche des fichiers ou dossiers dans le Google Drive de l'utilisateur par mots-cles.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Termes de recherche."}},
            "required": ["query"],
        },
    },
    {
        "name": "list_drive_files",
        "description": "Liste les fichiers presents dans un dossier Google Drive (racine si aucun dossier precise).",
        "parameters": {
            "type": "object",
            "properties": {
                "folder_name": {"type": "string", "description": "Nom du dossier a lister (optionnel)."},
                "max_results": {"type": "integer", "description": "Nombre maximum de fichiers a renvoyer."},
            },
            "required": [],
        },
    },
    {
        "name": "read_drive_file",
        "description": "Lit le contenu textuel d'un fichier Drive (Doc, Sheet ou fichier texte) a partir de son nom ou de son ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id_or_name": {"type": "string", "description": "ID ou nom du fichier a lire."},
            },
            "required": ["file_id_or_name"],
        },
    },
    {
        "name": "get_file_details",
        "description": (
            "Recupere les metadonnees d'un fichier Drive (type, taille, derniere modification, URL) "
            "sans en lire tout le contenu. Utile pour verifier qu'un fichier existe ou identifier son type."
        ),
        "parameters": {
            "type": "object",
            "properties": {"file_id_or_name": {"type": "string", "description": "ID ou nom du fichier."}},
            "required": ["file_id_or_name"],
        },
    },
    {
        "name": "organize_drive_file",
        "description": "Deplace un fichier Drive vers un autre dossier (organisation/rangement).",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id_or_name": {"type": "string", "description": "ID ou nom du fichier a deplacer."},
                "target_folder_name": {"type": "string", "description": "Nom du dossier de destination."},
            },
            "required": ["file_id_or_name", "target_folder_name"],
        },
    },
    {
        "name": "delete_drive_file",
        "description": "Supprime définitivement ou place dans la corbeille un fichier du Google Drive.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id_or_name": {"type": "string", "description": "ID ou nom du fichier a supprimer."},
            },
            "required": ["file_id_or_name"],
        },
    },
    {
        "name": "save_note_to_drive",
        "description": "Enregistre une note sous forme de Google Doc dans un dossier dedie du Drive de l'utilisateur.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titre de la note / du document."},
                "content": {"type": "string", "description": "Contenu textuel de la note."},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "remember_note",
        "description": (
            "Enregistre une information courte dans la memoire permanente de l'utilisateur "
            "(fichier cumulatif Memoire_Jarvis.txt sur Drive)."
        ),
        "parameters": {
            "type": "object",
            "properties": {"content": {"type": "string", "description": "Information a memoriser."}},
            "required": ["content"],
        },
    },
    # ---------------- Docs ----------------
    {
        "name": "create_google_doc",
        "description": "Cree un nouveau Google Doc basique avec le titre et le contenu donnes.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titre du nouveau document."},
                "content": {"type": "string", "description": "Contenu textuel initial du document."},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "create_styled_doc",
        "description": (
            "Cree un Google Doc professionnel avance avec des sections de titres (H1, H2), "
            "des paragraphes formatés et des tableaux de donnees avec en-têtes stylisés."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titre principal du document."},
                "sections": {
                    "type": "array",
                    "description": "Liste des sections du document avec leurs contenus et tableaux.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string", "description": "Titre de la section (ex: 'Rapport ERP')."},
                            "level": {"type": "integer", "description": "Niveau du titre (1 pour H1, 2 pour H2)."},
                            "body": {"type": "string", "description": "Texte explicatif de la section."},
                            "table": {
                                "type": "array",
                                "description": "Tableau 2D optionnel à insérer dans cette section.",
                                "items": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                },
            },
            "required": ["title", "sections"],
        },
    },
    {
        "name": "read_google_doc",
        "description": "Lit le contenu integral d'un Google Doc a partir de son nom ou de son ID.",
        "parameters": {
            "type": "object",
            "properties": {"doc_id_or_name": {"type": "string", "description": "ID ou nom du document."}},
            "required": ["doc_id_or_name"],
        },
    },
    {
        "name": "write_google_doc",
        "description": "Ajoute ou remplace du texte dans un Google Doc existant.",
        "parameters": {
            "type": "object",
            "properties": {
                "doc_id_or_name": {"type": "string", "description": "ID ou nom du document."},
                "content": {"type": "string", "description": "Texte a inserer."},
                "mode": {"type": "string", "description": "'append' pour ajouter a la suite, 'replace' pour remplacer tout le contenu."},
            },
            "required": ["doc_id_or_name", "content"],
        },
    },
    {
        "name": "replace_text_in_doc",
        "description": "Recherche et remplace une chaîne de texte spécifique dans un document Google Doc.",
        "parameters": {
            "type": "object",
            "properties": {
                "doc_id_or_name": {"type": "string", "description": "ID ou nom du document."},
                "target_text": {"type": "string", "description": "Texte exact à chercher et remplacer."},
                "replacement_text": {"type": "string", "description": "Nouveau texte de remplacement."},
            },
            "required": ["doc_id_or_name", "target_text", "replacement_text"],
        },
    },
    # ---------------- Sheets ----------------
    {
        "name": "create_google_sheet",
        "description": "Cree une nouvelle Google Sheet, avec optionnellement des en-têtes et des lignes de données.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titre de la nouvelle feuille de calcul."},
                "headers": {"type": "array", "items": {"type": "string"}, "description": "Ligne d'en-tetes (colonnes)."},
                "rows": {
                    "type": "array",
                    "description": "Lignes de donnees initiales.",
                    "items": {"type": "array", "items": {"type": "string"}},
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "read_google_sheet",
        "description": "Lit une plage de cellules d'une Google Sheet existante (ex: 'A1:C10').",
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id_or_name": {"type": "string", "description": "ID ou nom de la feuille de calcul."},
                "sheet_name": {"type": "string", "description": "Nom de l'onglet cible (optionnel)."},
                "range": {"type": "string", "description": "Plage a lire, ex 'A1:C10'."},
            },
            "required": ["sheet_id_or_name"],
        },
    },
    {
        "name": "write_google_sheet",
        "description": "Ecrit ou met a jour des valeurs dans une plage precise d'une Google Sheet existante.",
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id_or_name": {"type": "string", "description": "ID ou nom de la feuille de calcul."},
                "sheet_name": {"type": "string", "description": "Nom de l'onglet cible (optionnel)."},
                "range": {"type": "string", "description": "Plage a ecrire, ex 'A1:B2'."},
                "values": {
                    "type": "array",
                    "description": "Tableau 2D de valeurs à écrire.",
                    "items": {"type": "array", "items": {"type": "string"}},
                },
            },
            "required": ["sheet_id_or_name", "range", "values"],
        },
    },
    {
        "name": "update_sheet_cell",
        "description": "Modifie la valeur d'une seule cellule dans une Google Sheet (ex: 'B4').",
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id_or_name": {"type": "string", "description": "ID ou nom de la feuille de calcul."},
                "sheet_name": {"type": "string", "description": "Nom de l'onglet cible (optionnel)."},
                "cell": {"type": "string", "description": "Reference de la cellule, ex 'B4'."},
                "value": {"type": "string", "description": "Nouvelle valeur ou formule à insérer."},
            },
            "required": ["sheet_id_or_name", "cell", "value"],
        },
    },
    {
        "name": "append_google_sheet_row",
        "description": "Ajoute une nouvelle ligne a la fin d'une Google Sheet existante.",
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id_or_name": {"type": "string", "description": "ID ou nom de la feuille de calcul."},
                "sheet_name": {"type": "string", "description": "Nom de l'onglet cible (optionnel)."},
                "row_values": {"type": "array", "items": {"type": "string"}, "description": "Valeurs de la nouvelle ligne."},
            },
            "required": ["sheet_id_or_name", "row_values"],
        },
    },
    {
        "name": "format_google_sheet",
        "description": (
            "Applique un thème visuel professionnel à une feuille de calcul : en-têtes sombres, "
            "texte blanc en gras, auto-dimensionnement des colonnes et figement de la 1ère ligne."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id_or_name": {"type": "string", "description": "ID ou nom du fichier Sheet."},
                "sheet_name": {"type": "string", "description": "Nom de l'onglet (optionnel)."},
                "theme": {"type": "string", "description": "Thème de couleur : 'DARK_BLUE', 'EMERALD', 'SLATE', 'CHARCOAL'."},
            },
            "required": ["sheet_id_or_name"],
        },
    },
    {
        "name": "apply_sheet_formula",
        "description": "Injecte une formule de calcul dynamique dans une cellule (ex: '=SUM(C2:C20)' ou '=AVERAGE(B1:B10)').",
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id_or_name": {"type": "string", "description": "ID ou nom du fichier Sheet."},
                "sheet_name": {"type": "string", "description": "Nom de l'onglet (optionnel)."},
                "cell": {"type": "string", "description": "Cellule cible (ex: 'C21')."},
                "formula": {"type": "string", "description": "Formule débutant par '=' (ex: '=SUM(C2:C20)')."},
            },
            "required": ["sheet_id_or_name", "cell", "formula"],
        },
    },
    {
        "name": "update_sheet_row_by_key",
        "description": "Recherche une ligne spécifique basée sur la valeur d'une colonne (ex: une référence ou date) et la met à jour.",
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id_or_name": {"type": "string", "description": "ID ou nom du fichier Sheet."},
                "sheet_name": {"type": "string", "description": "Nom de l'onglet (optionnel)."},
                "key_column_index": {"type": "integer", "description": "Indice de la colonne de recherche (1 pour A, 2 pour B...)."},
                "key_value": {"type": "string", "description": "Valeur exacte à rechercher dans la colonne."},
                "new_row_values": {"type": "array", "items": {"type": "string"}, "description": "Nouvelles valeurs pour mettre à jour la ligne."},
            },
            "required": ["sheet_id_or_name", "key_column_index", "key_value", "new_row_values"],
        },
    },
    {
        "name": "delete_sheet_row",
        "description": "Supprime une ligne spécifique d'un tableau Google Sheet d'après son numéro.",
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id_or_name": {"type": "string", "description": "ID ou nom du fichier Sheet."},
                "sheet_name": {"type": "string", "description": "Nom de l'onglet (optionnel)."},
                "row_index": {"type": "integer", "description": "Numéro de la ligne à supprimer (base 1)."},
            },
            "required": ["sheet_id_or_name", "row_index"],
        },
    },
    {
        "name": "format_sheet_number_range",
        "description": "Applique un formatage de nombre/devise (ex: FCFA, USD, date, pourcentage) à une plage de cellules.",
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id_or_name": {"type": "string", "description": "ID ou nom du fichier Sheet."},
                "sheet_name": {"type": "string", "description": "Nom de l'onglet (optionnel)."},
                "range": {"type": "string", "description": "Plage de cellules (ex: 'C2:C100')."},
                "number_format": {"type": "string", "description": "Format (ex: '#,##0 \"FCFA\"', '0.00%', 'YYYY-MM-DD')."},
            },
            "required": ["sheet_id_or_name", "range", "number_format"],
        },
    },
    # ---------------- Gmail ----------------
    {
        "name": "get_unread_emails",
        "description": "Recupere un resume des derniers e-mails non lus dans Gmail.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "send_gmail",
        "description": "Redige et envoie un e-mail via Gmail au nom de l'utilisateur.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Adresse e-mail du destinataire."},
                "subject": {"type": "string", "description": "Objet de l'e-mail."},
                "body": {"type": "string", "description": "Corps du message."},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "create_gmail_draft",
        "description": "Cree un brouillon d'e-mail dans Gmail sans l'envoyer.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Adresse e-mail du destinataire."},
                "subject": {"type": "string", "description": "Objet de l'e-mail."},
                "body": {"type": "string", "description": "Corps du message."},
            },
            "required": ["to", "subject", "body"],
        },
    },
    # ---------------- Calendar ----------------
    {
        "name": "list_calendar_events",
        "description": "Liste les prochains evenements du calendrier de l'utilisateur.",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Nombre maximum d'evenements a renvoyer."},
                "days_ahead": {"type": "integer", "description": "Fenetre en nombre de jours a venir."},
            },
            "required": [],
        },
    },
    {
        "name": "create_calendar_event",
        "description": "Cree un evenement dans le calendrier Google de l'utilisateur.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titre de l'evenement."},
                "start_datetime": {"type": "string", "description": "Date/heure de debut au format ISO 8601."},
                "end_datetime": {"type": "string", "description": "Date/heure de fin au format ISO 8601."},
                "description": {"type": "string", "description": "Description optionnelle de l'evenement."},
            },
            "required": ["title", "start_datetime", "end_datetime"],
        },
    },
    # ---------------- Web ----------------
    {
        "name": "web_search",
        "description": (
            "Effectue une recherche sur le web en temps reel et renvoie les meilleurs resultats. "
            "A utiliser pour toute information recente ou externe."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Requete de recherche."}},
            "required": ["query"],
        },
    },
]

# ----------------------------------------------------------------------------
# Dispatcher d'execution
# ----------------------------------------------------------------------------

_TOOL_FUNCTIONS = {
    # Drive
    "search_google_drive": lambda args: connector.search_google_drive(args.get("query", "")),
    "list_drive_files": lambda args: connector.list_drive_files(args.get("folder_name"), args.get("max_results", 20)),
    "read_drive_file": lambda args: connector.read_drive_file(args.get("file_id_or_name", "")),
    "get_file_details": lambda args: connector.get_file_details(args.get("file_id_or_name", "")),
    "organize_drive_file": lambda args: connector.organize_drive_file(
        args.get("file_id_or_name", ""), args.get("target_folder_name", "")
    ),
    "delete_drive_file": lambda args: connector.delete_drive_file(args.get("file_id_or_name", "")),
    "save_note_to_drive": lambda args: connector.save_note_to_drive(args.get("title", ""), args.get("content", "")),
    "remember_note": lambda args: connector.remember_note(args.get("content", "")),
    
    # Docs
    "create_google_doc": lambda args: connector.create_google_doc(args.get("title", ""), args.get("content", "")),
    "create_styled_doc": lambda args: connector.create_styled_doc(args.get("title", ""), args.get("sections", [])),
    "read_google_doc": lambda args: connector.read_google_doc(args.get("doc_id_or_name", "")),
    "write_google_doc": lambda args: connector.write_google_doc(
        args.get("doc_id_or_name", ""), args.get("content", ""), args.get("mode", "append")
    ),
    "replace_text_in_doc": lambda args: connector.replace_text_in_doc(
        args.get("doc_id_or_name", ""), args.get("target_text", ""), args.get("replacement_text", "")
    ),
    
    # Sheets
    "create_google_sheet": lambda args: connector.create_google_sheet(
        args.get("title", ""), args.get("headers", []), args.get("rows", [])
    ),
    "read_google_sheet": lambda args: connector.read_google_sheet(
        args.get("sheet_id_or_name", ""), args.get("range"), args.get("sheet_name")
    ),
    "write_google_sheet": lambda args: connector.write_google_sheet(
        args.get("sheet_id_or_name", ""), args.get("range", ""), args.get("values", []), args.get("sheet_name")
    ),
    "update_sheet_cell": lambda args: connector.update_sheet_cell(
        args.get("sheet_id_or_name", ""), args.get("cell", ""), args.get("value", ""), args.get("sheet_name")
    ),
    "append_google_sheet_row": lambda args: connector.append_google_sheet_row(
        args.get("sheet_id_or_name", ""), args.get("row_values", []), args.get("sheet_name")
    ),
    "format_google_sheet": lambda args: connector.format_google_sheet(
        args.get("sheet_id_or_name", ""), args.get("sheet_name"), args.get("theme", "DARK_BLUE")
    ),
    "apply_sheet_formula": lambda args: connector.apply_sheet_formula(
        args.get("sheet_id_or_name", ""), args.get("cell", ""), args.get("formula", ""), args.get("sheet_name")
    ),
    "update_sheet_row_by_key": lambda args: connector.update_sheet_row_by_key(
        args.get("sheet_id_or_name", ""), args.get("key_column_index", 1), args.get("key_value", ""), args.get("new_row_values", []), args.get("sheet_name")
    ),
    "delete_sheet_row": lambda args: connector.delete_sheet_row(
        args.get("sheet_id_or_name", ""), args.get("row_index", 1), args.get("sheet_name")
    ),
    "format_sheet_number_range": lambda args: connector.format_sheet_number_range(
        args.get("sheet_id_or_name", ""), args.get("range", ""), args.get("number_format", ""), args.get("sheet_name")
    ),

    # Gmail
    "get_unread_emails": lambda args: connector.get_unread_emails(),
    "send_gmail": lambda args: connector.send_gmail(args.get("to", ""), args.get("subject", ""), args.get("body", "")),
    "create_gmail_draft": lambda args: connector.create_gmail_draft(
        args.get("to", ""), args.get("subject", ""), args.get("body", "")
    ),

    # Calendar
    "list_calendar_events": lambda args: connector.list_calendar_events(
        args.get("max_results", 10), args.get("days_ahead", 7)
    ),
    "create_calendar_event": lambda args: connector.create_calendar_event(
        args.get("title", ""), args.get("start_datetime", ""), args.get("end_datetime", ""), args.get("description", "")
    ),

    # Web
    "web_search": lambda args: websearch.web_search(args.get("query", "")),
}


async def execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute l'outil demande par le LLM et renvoie un resultat JSON-serialisable."""
    fn = _TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"Outil inconnu: '{name}'"}
    try:
        return await fn(args)
    except connector.ConnectorError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Erreur lors de l'execution de l'outil '{name}': {e}"}


# ----------------------------------------------------------------------------
# Conversion vers le format OpenAI (Groq / Mistral / OpenRouter)
# ----------------------------------------------------------------------------

def get_openai_tools() -> List[Dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
        for t in TOOLS
    ]


# ----------------------------------------------------------------------------
# Conversion vers le format Gemini
# ----------------------------------------------------------------------------

def _json_schema_to_gemini_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Convertit un schema JSON simple vers le format attendu par Gemini."""
    type_map = {
        "object": "OBJECT",
        "string": "STRING",
        "number": "NUMBER",
        "integer": "INTEGER",
        "boolean": "BOOLEAN",
        "array": "ARRAY",
    }
    result: Dict[str, Any] = {}
    schema_type = schema.get("type")
    if schema_type:
        result["type"] = type_map.get(schema_type, schema_type.upper())
    if "description" in schema:
        result["description"] = schema["description"]
    if "properties" in schema:
        result["properties"] = {k: _json_schema_to_gemini_schema(v) for k, v in schema["properties"].items()}
    if "required" in schema and schema["required"]:
        result["required"] = schema["required"]
    if "items" in schema:
        result["items"] = _json_schema_to_gemini_schema(schema["items"])
    return result


def get_gemini_tools() -> List[Dict[str, Any]]:
    return [
        {
            "functionDeclarations": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": _json_schema_to_gemini_schema(t["parameters"]),
                }
                for t in TOOLS
            ]
        }
    ]
