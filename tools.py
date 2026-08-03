"""
Definitions des outils (function calling) exposes au LLM, independamment du fournisseur.
Fournit :
  - TOOLS : definition canonique (nom, description, parametres)
  - get_openai_tools() : format utilise par Groq / Mistral / OpenRouter (API compatible OpenAI)
  - get_gemini_tools() : format utilise par l'API Gemini
  - execute_tool(name, args) : execute la fonction Python correspondante

Couverture "acces total a l'ecosysteme Google" :
  Drive    : recherche, listing, lecture, deplacement/organisation
  Docs     : lecture, ecriture/ajout, creation de note
  Sheets   : lecture d'une plage, ecriture d'une plage, ajout de ligne
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
            "sans en lire tout le contenu. Utile pour verifier qu'un fichier existe ou identifier son type "
            "avant d'agir dessus."
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
            "(fichier cumulatif Memoire_Jarvis.txt sur Drive). A utiliser quand l'utilisateur "
            "demande explicitement de se souvenir de quelque chose."
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
        "description": (
            "Cree un nouveau Google Doc structure avec le titre et le contenu donnes. A utiliser "
            "quand aucun document existant ne correspond a la demande (sinon, utiliser write_google_doc "
            "sur le fichier existant)."
        ),
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
    # ---------------- Sheets ----------------
    {
        "name": "create_google_sheet",
        "description": (
            "Cree une nouvelle Google Sheet, avec une ligne d'en-tetes optionnelle et des lignes "
            "de donnees initiales optionnelles. A utiliser quand aucun tableau existant ne correspond "
            "a la demande (sinon, utiliser append_google_sheet_row sur le fichier existant)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titre de la nouvelle feuille de calcul."},
                "headers": {"type": "array", "items": {"type": "string"}, "description": "Ligne d'en-tetes (colonnes), optionnelle."},
                "rows": {
                    "type": "array",
                    "description": "Lignes de donnees initiales, optionnelles.",
                    "items": {"type": "array", "items": {"type": "string"}},
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "read_google_sheet",
        "description": "Lit une plage de cellules d'une Google Sheet existante (ex: 'A1:C10'), sur un onglet precis si besoin.",
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id_or_name": {"type": "string", "description": "ID ou nom de la feuille de calcul."},
                "sheet_name": {"type": "string", "description": "Nom de l'onglet a lire (optionnel, onglet actif par defaut)."},
                "range": {"type": "string", "description": "Plage a lire, ex 'A1:C10'. Par defaut, tout l'onglet."},
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
                "sheet_name": {"type": "string", "description": "Nom de l'onglet cible (optionnel, onglet actif par defaut)."},
                "range": {"type": "string", "description": "Plage a ecrire, ex 'A1:B2'."},
                "values": {
                    "type": "array",
                    "description": "Tableau de lignes, chaque ligne etant un tableau de valeurs.",
                    "items": {"type": "array", "items": {"type": "string"}},
                },
            },
            "required": ["sheet_id_or_name", "range", "values"],
        },
    },
    {
        "name": "update_sheet_cell",
        "description": "Modifie la valeur d'une seule cellule dans une Google Sheet existante (ex: cellule 'B4').",
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id_or_name": {"type": "string", "description": "ID ou nom de la feuille de calcul."},
                "sheet_name": {"type": "string", "description": "Nom de l'onglet cible (optionnel, onglet actif par defaut)."},
                "cell": {"type": "string", "description": "Reference de la cellule, ex 'B4'."},
                "value": {"type": "string", "description": "Nouvelle valeur a inserer dans la cellule."},
            },
            "required": ["sheet_id_or_name", "cell", "value"],
        },
    },
    {
        "name": "append_google_sheet_row",
        "description": (
            "Ajoute une nouvelle ligne a la fin d'une Google Sheet existante. A utiliser sans hesiter "
            "des que l'utilisateur mentionne un revenu, une depense ou toute entree a consigner dans "
            "un tableau de suivi deja existant."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sheet_id_or_name": {"type": "string", "description": "ID ou nom de la feuille de calcul."},
                "sheet_name": {"type": "string", "description": "Nom de l'onglet cible (optionnel, onglet actif par defaut)."},
                "row_values": {"type": "array", "items": {"type": "string"}, "description": "Valeurs de la nouvelle ligne, dans l'ordre des colonnes."},
            },
            "required": ["sheet_id_or_name", "row_values"],
        },
    },
    # ---------------- Gmail ----------------
    {
        "name": "get_unread_emails",
        "description": "Recupere un resume des derniers e-mails non lus dans Gmail (expediteur, sujet, extrait).",
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
                "body": {"type": "string", "description": "Corps du message, en texte brut."},
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
                "days_ahead": {"type": "integer", "description": "Fenetre en nombre de jours a venir (par defaut 7)."},
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
    # ---------------- Prediction Football ----------------
    {
        "name": "predict_football_match",
        "description": (
            "Envoie un pronostic de match de football au script de prediction dedie. IMPORTANT : "
            "avant d'appeler cet outil, utilise d'abord web_search pour recueillir les statistiques "
            "reelles et actuelles du match (forme recente des deux equipes, historique des confrontations "
            "H2H, xG si disponible, blessures/absences notables). Ne renseigne jamais ces champs de "
            "memoire : ils doivent venir d'une recherche web recente. Une fois les statistiques reunies, "
            "appelle cet outil avec les champs remplis pour obtenir la prediction finale."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "team_home": {"type": "string", "description": "Equipe recevante."},
                "team_away": {"type": "string", "description": "Equipe visiteuse."},
                "competition": {"type": "string", "description": "Nom de la competition (optionnel)."},
                "match_date": {"type": "string", "description": "Date du match (optionnel)."},
                "form_home": {"type": "string", "description": "Forme recente de l'equipe a domicile (ex: 'VVNDV'), issue de la recherche web."},
                "form_away": {"type": "string", "description": "Forme recente de l'equipe a l'exterieur, issue de la recherche web."},
                "h2h": {"type": "string", "description": "Resume des confrontations directes recentes, issu de la recherche web."},
                "xg_home": {"type": "number", "description": "xG moyen de l'equipe a domicile, si trouve."},
                "xg_away": {"type": "number", "description": "xG moyen de l'equipe a l'exterieur, si trouve."},
                "notes": {"type": "string", "description": "Elements notables (blessures, enjeux, meteo...), optionnel."},
            },
            "required": ["team_home", "team_away"],
        },
    },
    # ---------------- Web ----------------
    {
        "name": "web_search",
        "description": (
            "Effectue une recherche sur le web en temps reel (Tavily) et renvoie les meilleurs "
            "resultats. A utiliser pour toute information recente, changeante ou externe aux "
            "connaissances du modele : actualites, prix, cours, resultats sportifs, meteo, etc. "
            "Ne pas utiliser pour des questions de culture generale stable."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Requete de recherche, concise et ciblee."}},
            "required": ["query"],
        },
    },
]

# ----------------------------------------------------------------------------
# Dispatcher d'execution
# ----------------------------------------------------------------------------

_TOOL_FUNCTIONS = {
    "search_google_drive": lambda args: connector.search_google_drive(args.get("query", "")),
    "list_drive_files": lambda args: connector.list_drive_files(args.get("folder_name"), args.get("max_results", 20)),
    "read_drive_file": lambda args: connector.read_drive_file(args.get("file_id_or_name", "")),
    "get_file_details": lambda args: connector.get_file_details(args.get("file_id_or_name", "")),
    "organize_drive_file": lambda args: connector.organize_drive_file(
        args.get("file_id_or_name", ""), args.get("target_folder_name", "")
    ),
    "save_note_to_drive": lambda args: connector.save_note_to_drive(args.get("title", ""), args.get("content", "")),
    "remember_note": lambda args: connector.remember_note(args.get("content", "")),
    "create_google_doc": lambda args: connector.create_google_doc(args.get("title", ""), args.get("content", "")),
    "read_google_doc": lambda args: connector.read_google_doc(args.get("doc_id_or_name", "")),
    "write_google_doc": lambda args: connector.write_google_doc(
        args.get("doc_id_or_name", ""), args.get("content", ""), args.get("mode", "append")
    ),
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
    "get_unread_emails": lambda args: connector.get_unread_emails(),
    "send_gmail": lambda args: connector.send_gmail(args.get("to", ""), args.get("subject", ""), args.get("body", "")),
    "create_gmail_draft": lambda args: connector.create_gmail_draft(
        args.get("to", ""), args.get("subject", ""), args.get("body", "")
    ),
    "list_calendar_events": lambda args: connector.list_calendar_events(
        args.get("max_results", 10), args.get("days_ahead", 7)
    ),
    "create_calendar_event": lambda args: connector.create_calendar_event(
        args.get("title", ""), args.get("start_datetime", ""), args.get("end_datetime", ""), args.get("description", "")
    ),
    "predict_football_match": lambda args: connector.predict_football_match(
        team_home=args.get("team_home", ""),
        team_away=args.get("team_away", ""),
        competition=args.get("competition", ""),
        match_date=args.get("match_date", ""),
        form_home=args.get("form_home", ""),
        form_away=args.get("form_away", ""),
        h2h=args.get("h2h", ""),
        xg_home=args.get("xg_home"),
        xg_away=args.get("xg_away"),
        notes=args.get("notes", ""),
    ),
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
    except Exception as e:  # securite : ne jamais laisser une exception remonter au LLM sans contexte
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
    """Convertit un schema JSON simple (type lowercase) vers le format attendu par Gemini
    (types en majuscules : OBJECT, STRING, ARRAY, ...)."""
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
