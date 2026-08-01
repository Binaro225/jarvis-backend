"""
Définitions des outils (function calling) exposés au LLM, indépendamment du fournisseur.
Fournit :
  - TOOLS : définition canonique (nom, description, paramètres)
  - get_openai_tools() : format utilisé par Groq / Mistral / OpenRouter (API compatible OpenAI)
  - get_gemini_tools() : format utilisé par l'API Gemini
  - execute_tool(name, args) : exécute la fonction Python correspondante
"""

from typing import Any, Dict, List

import connector
import websearch

# ----------------------------------------------------------------------------
# Définition canonique des outils
# ----------------------------------------------------------------------------

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "search_google_drive",
        "description": (
            "Recherche des fichiers ou dossiers dans le Google Drive de l'utilisateur. "
            "Renvoie les noms, ID et extraits de contenu correspondant à la requête."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Termes de recherche (nom de fichier, mots-clés du contenu, etc.).",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_unread_emails",
        "description": (
            "Récupère un résumé des derniers e-mails non lus dans Gmail "
            "(expéditeur, sujet, extrait). Ne prend aucun paramètre."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "send_gmail",
        "description": "Rédige et envoie un e-mail via Gmail au nom de l'utilisateur.",
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
        "name": "save_note_to_drive",
        "description": (
            "Enregistre une note sous forme de Google Doc dans un dossier dédié du Drive "
            "de l'utilisateur."
        ),
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
            "Enregistre une information courte dans la mémoire permanente de l'utilisateur "
            "(fichier cumulatif Mémoire_Jarvis.txt sur Drive), sans créer de nouveau document. "
            "À utiliser quand l'utilisateur demande explicitement de se souvenir de quelque chose "
            "(ex: 'Rappelle-toi que...', 'Note que...', 'Souviens-toi que...')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "L'information à mémoriser, formulée de façon claire et autonome.",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Effectue une recherche sur le web et renvoie les meilleurs résultats (titre, URL, "
            "extrait). À utiliser chaque fois que la question porte sur une information récente, "
            "changeante ou externe aux connaissances du modèle : actualités, prix, cours, résultats "
            "sportifs, disponibilité d'un produit, informations sur une entreprise ou un événement "
            "actuel, etc. Ne pas utiliser pour des questions de culture générale stable."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "La requête de recherche, formulée de façon concise et ciblée.",
                },
            },
            "required": ["query"],
        },
    },
]

# ----------------------------------------------------------------------------
# Dispatcher d'exécution
# ----------------------------------------------------------------------------

_TOOL_FUNCTIONS = {
    "search_google_drive": lambda args: connector.search_google_drive(args.get("query", "")),
    "get_unread_emails": lambda args: connector.get_unread_emails(),
    "send_gmail": lambda args: connector.send_gmail(
        args.get("to", ""), args.get("subject", ""), args.get("body", "")
    ),
    "save_note_to_drive": lambda args: connector.save_note_to_drive(
        args.get("title", ""), args.get("content", "")
    ),
    "remember_note": lambda args: connector.remember_note(args.get("content", "")),
    "web_search": lambda args: websearch.web_search(args.get("query", "")),
}


async def execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Exécute l'outil demandé par le LLM et renvoie un résultat JSON-sérialisable."""
    fn = _TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"Outil inconnu: '{name}'"}
    try:
        return await fn(args)
    except connector.ConnectorError as e:
        return {"error": str(e)}
    except Exception as e:  # sécurité: ne jamais laisser une exception remonter au LLM sans contexte
        return {"error": f"Erreur lors de l'exécution de l'outil '{name}': {e}"}


# ----------------------------------------------------------------------------
# Conversion vers le format OpenAI (Groq / Mistral / OpenRouter)
# ----------------------------------------------------------------------------

def get_openai_tools() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in TOOLS
    ]


# ----------------------------------------------------------------------------
# Conversion vers le format Gemini
# ----------------------------------------------------------------------------

def _json_schema_to_gemini_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Convertit un schéma JSON simple (type lowercase) vers le format attendu par Gemini
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
        result["properties"] = {
            key: _json_schema_to_gemini_schema(value) for key, value in schema["properties"].items()
        }

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
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": _json_schema_to_gemini_schema(tool["parameters"]),
                }
                for tool in TOOLS
            ]
        }
    ]
