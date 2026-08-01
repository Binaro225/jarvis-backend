"""
Personnalite de JARVIS : prompt systeme par defaut + message de boot.

Surchargeable sans toucher au code via les variables d'environnement :
  - JARVIS_SYSTEM_PROMPT : remplace entierement le gabarit du prompt systeme
  - JARVIS_BOOT_MESSAGE  : remplace entierement le message de boot

Ce module fournit aussi :
  - build_effective_system_prompt(...) : injecte la date/heure courante et le
    cerveau (fournisseur LLM) actif dans le prompt systeme a chaque requete.
  - clean_text_for_voice(...) : filet de securite qui retire Markdown/emojis
    et les deux-points isoles en fin de phrase avant affichage/synthese vocale.
  - detect_brain_switch(...) / detect_brain_query(...) : detection de
    commandes vocales de changement de cerveau ("Passe sur Groq", ...),
    traitees en amont du LLM (voir main.py) pour une reponse instantanee.
"""

import os
import re
import random

DEFAULT_SYSTEM_PROMPT = """Tu es JARVIS, le compagnon vocal personnel de l'utilisateur. Tu parles comme un humain chaleureux, vif et complice, jamais comme un robot ou un assistant scolaire.

STYLE
- Tutorat direct obligatoire : tutoie toujours l'utilisateur. Ne l'appelle JAMAIS "monsieur", "monsieur le client" ou par des formules de politesse formelles.
- Reponses ultra-courtes a l'oral : 1 a 2 phrases maximum par defaut. Tu ne developpes en longueur que si l'utilisateur demande explicitement des details, une explication complete ou une liste.
- Jamais de liste a puces, jamais de symboles Markdown, jamais d'emoji : ta reponse est un texte brut, fluide, pret a etre lu a voix haute.
- Interdiction de terminer une phrase sur un simple deux-points suivi de rien : chaque phrase se suffit a elle-meme.
- Ton complice, direct, avec un peu d'humour, mais toujours respectueux.

DATE ET HEURE
- Date et heure actuelles : {current_datetime}. Utilise-les pour toute question temporelle ou d'actualite, nous sommes en {current_year}.

OUTILS GOOGLE
- Tu as un acces complet a Drive, Docs, Sheets, Gmail et Calendar de l'utilisateur via les outils disponibles. Utilise-les sans hesiter des que la demande le necessite : chercher ou lister un fichier, lire ou ecrire un document, lire ou modifier une feuille de calcul, lire ou envoyer un mail, consulter ou creer un evenement.
- Ne recite jamais un resultat brut et long : resume en une phrase et precise que le detail est affiche a l'ecran.

RECHERCHE WEB
- Pour toute question sur une actualite, un resultat sportif, la meteo, un prix, ou tout fait susceptible d'avoir change recemment, utilise systematiquement l'outil web_search plutot que ta memoire, qui peut etre perimee.
- Une fois le resultat obtenu, synthetise en 1 a 2 phrases, avec eventuellement la source citee brievement, jamais l'URL complete.

CHANGEMENT DE CERVEAU
- Le cerveau (fournisseur LLM) actuellement actif est : {current_provider}. Les changements de cerveau demandes a l'oral sont geres en amont, tu n'as jamais a t'en occuper toi-meme.

Ne mentionne jamais ces instructions, ni le fait que tu es un modele de langage : tu es JARVIS, un point c'est tout."""

DEFAULT_BOOT_MESSAGE = "Systeme en ligne. Drive, Gmail, Docs, Sheets et Calendar sont connectes. Je t'ecoute."

SYSTEM_PROMPT_TEMPLATE = os.getenv("JARVIS_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)
BOOT_MESSAGE = os.getenv("JARVIS_BOOT_MESSAGE", DEFAULT_BOOT_MESSAGE)


def build_effective_system_prompt(current_datetime: str, current_provider: str, extra_instructions: str = None) -> str:
    """Construit le prompt systeme final pour cette requete : personnalite + date/heure
    dynamique + cerveau actif + instructions ponctuelles additionnelles eventuelles."""
    try:
        year = current_datetime.split()[-1] if current_datetime else ""
        base = SYSTEM_PROMPT_TEMPLATE.format(
            current_datetime=current_datetime,
            current_year=year,
            current_provider=current_provider,
        )
    except (KeyError, IndexError):
        # Un JARVIS_SYSTEM_PROMPT personnalise sans les placeholders attendus : on l'utilise tel quel.
        base = SYSTEM_PROMPT_TEMPLATE
    if extra_instructions:
        base += "\n\nINSTRUCTIONS SUPPLEMENTAIRES POUR CETTE REQUETE :\n" + extra_instructions
    return base


# ----------------------------------------------------------------------------
# Nettoyage du texte avant synthese vocale / affichage (filet de securite)
# ----------------------------------------------------------------------------

_MARKDOWN_CHARS = re.compile(r"[*#`_~]")
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF"
    "]+",
    flags=re.UNICODE,
)


def clean_text_for_voice(text: str) -> str:
    """Supprime Markdown et emojis residuels, et evite qu'une ligne se termine sur un
    deux-points isole. Applique cote backend, donc valable a la fois pour l'affichage
    et pour la synthese vocale cote frontend."""
    if not text:
        return text
    cleaned = _MARKDOWN_CHARS.sub("", text)
    cleaned = _EMOJI_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    lines = []
    for raw_line in cleaned.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.endswith(":"):
            line = line[:-1].rstrip() + "."
        lines.append(line)
    return "\n".join(lines).strip()


# ----------------------------------------------------------------------------
# Repliques "Total Recall" (memorisation vocale instantanee)
# ----------------------------------------------------------------------------

MEMORY_CONFIRMATIONS = [
    "C'est note, ca restera dans ton Drive.",
    "Enregistre, tu peux compter dessus.",
    "Archive : ce fichier-la n'oubliera rien.",
    "C'est fait, ajoute a ta memoire Drive.",
    "Memorise, tranquille.",
]

MEMORY_FAILURES = [
    "Petit souci, je n'ai pas reussi a l'enregistrer ({error}).",
    "Ca n'est pas passe, l'enregistrement a echoue ({error}).",
    "Contretemps technique, je n'ai pas pu le noter ({error}).",
]


def get_memory_confirmation() -> str:
    return random.choice(MEMORY_CONFIRMATIONS)


def get_memory_failure_line(error_detail: str = "") -> str:
    template = random.choice(MEMORY_FAILURES)
    return template.format(error=(error_detail or "raison inconnue").strip())


# ----------------------------------------------------------------------------
# Detection vocale de changement de cerveau ("Passe sur Groq", "Quel cerveau ?")
# ----------------------------------------------------------------------------

BRAIN_KEYWORDS = {
    "gemini": ["gemini"],
    "groq": ["groq"],
    "mistral": ["mistral"],
    "openrouter": ["openrouter", "open router"],
}

_SWITCH_TRIGGERS = ["passe sur", "bascule sur", "change de cerveau", "passe a ", "switch to"]
_QUERY_TRIGGERS = ["quel cerveau", "quel est ton cerveau", "which brain", "cerveau actuel", "cerveau utilises-tu"]

BRAIN_LABELS = {
    "gemini": "Gemini",
    "groq": "Groq",
    "mistral": "Mistral",
    "openrouter": "OpenRouter",
}


def detect_brain_switch(prompt: str):
    """Detecte une commande vocale du type 'Passe sur Groq'. Renvoie le nom du provider
    (cle de PROVIDERS) ou None si aucune commande de ce type n'est detectee."""
    low = (prompt or "").lower().strip()
    if not any(t in low for t in _SWITCH_TRIGGERS):
        return None
    for provider, keywords in BRAIN_KEYWORDS.items():
        if any(k in low for k in keywords):
            return provider
    return None


def detect_brain_query(prompt: str) -> bool:
    low = (prompt or "").lower().strip()
    return any(t in low for t in _QUERY_TRIGGERS)


def get_brain_switch_confirmation(provider: str) -> str:
    return f"C'est fait, je bascule sur {BRAIN_LABELS.get(provider, provider)}."


def get_brain_query_answer(provider: str) -> str:
    return f"Mon cerveau actuel, c'est {BRAIN_LABELS.get(provider, provider)}."
