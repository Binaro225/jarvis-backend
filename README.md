# Multi-LLM Backend (FastAPI)

Backend Python léger exposant une API unifiée vers plusieurs fournisseurs LLM **gratuits** :
Google Gemini, Groq, Mistral, OpenRouter. Prêt à déployer gratuitement sur **Render**.

## Structure

```
main.py            # App FastAPI + gestionnaire multi-fournisseurs + boucle de function calling
connector.py        # Fonctions "tool" qui appellent le Web App Google Apps Script
tools.py             # Définitions des tools (schémas) + conversion OpenAI/Gemini + dispatcher
persona.py           # Personnalité JARVIS (prompt système) + message de boot
requirements.txt    # Dépendances
Procfile             # Commande de démarrage pour Render
.env.example         # Variables d'environnement attendues
```

## Fournisseurs & modèles

| Fournisseur | Variable d'env      | Modèles                                              |
|-------------|----------------------|-------------------------------------------------------|
| gemini      | `GEMINI_API_KEY`     | `gemini-1.5-flash`, `gemini-1.5-pro`                  |
| groq        | `GROQ_API_KEY`       | `llama-3.3-70b-versatile`, `deepseek-r1-distill-llama-70b` |
| mistral     | `MISTRAL_API_KEY`    | `mistral-small-latest`, `open-mistral-7b`             |
| openrouter  | `OPENROUTER_API_KEY` | modèles `:free` (ex: `meta-llama/llama-3.3-70b-instruct:free`) |

Tu n'as besoin de renseigner **que** les clés des fournisseurs que tu comptes utiliser.

Où obtenir des clés gratuites :
- Gemini : https://aistudio.google.com/apikey
- Groq : https://console.groq.com/keys
- Mistral : https://console.mistral.ai/api-keys
- OpenRouter : https://openrouter.ai/keys

## Lancer en local

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # puis remplis tes clés API
export $(cat .env | xargs) # ou utilise un outil comme python-dotenv/direnv
uvicorn main:app --reload
```

L'API est alors disponible sur http://localhost:8000 (docs interactives sur `/docs`).

## Déploiement sur Render (gratuit)

1. Pousse ce dossier sur un dépôt GitHub.
2. Sur [render.com](https://render.com), clique sur **New +** → **Web Service**.
3. Connecte ton dépôt GitHub.
4. Render détecte le `Procfile` automatiquement. Sinon configure manuellement :
   - **Build Command** : `pip install -r requirements.txt`
   - **Start Command** : `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Dans l'onglet **Environment**, ajoute tes variables (`GEMINI_API_KEY`, `GROQ_API_KEY`, etc.).
6. Choisis le plan **Free** puis clique sur **Create Web Service**.

Render build et démarre automatiquement le service ; tu obtiens une URL publique du type
`https://ton-service.onrender.com`.

> Note : le plan gratuit de Render met le service en veille après une période d'inactivité.
> La première requête après une période d'inactivité peut donc prendre quelques dizaines de
> secondes (cold start).

## Endpoints

### `GET /health`
Vérifie que le service tourne (utilisé par Render).

### `GET /providers`
Liste les fournisseurs, leurs modèles disponibles, et si leur clé API est configurée.

### `POST /switch-brain`
Change dynamiquement le fournisseur/modèle actif par défaut.

```bash
curl -X POST https://ton-service.onrender.com/switch-brain \
  -H "Content-Type: application/json" \
  -d '{"provider": "groq", "model": "llama-3.3-70b-versatile"}'
```

### `POST /chat`
Envoie un prompt au LLM actif (ou à un fournisseur précis en override ponctuel).

```bash
# Utilise le cerveau actif par défaut
curl -X POST https://ton-service.onrender.com/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explique-moi la relativité restreinte en 3 phrases."}'

# Override ponctuel du fournisseur, sans changer l'état global
curl -X POST https://ton-service.onrender.com/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Bonjour !", "provider": "mistral", "model": "open-mistral-7b"}'
```

Réponse type :

```json
{
  "provider": "groq",
  "model": "llama-3.3-70b-versatile",
  "response": "..."
}
```

## Function Calling / Tool Use

Le backend peut appeler automatiquement 4 outils, qui passent par le connecteur Google Apps
Script (projet séparé `gas-connector`) :

| Tool                    | Paramètres                  | Action Apps Script |
|--------------------------|------------------------------|---------------------|
| `search_google_drive`    | `query`                      | `search_drive`      |
| `get_unread_emails`      | *(aucun)*                    | `read_email`         |
| `send_gmail`              | `to`, `subject`, `body`      | `send_email`         |
| `save_note_to_drive`     | `title`, `content`           | `create_note` (Google Doc) |

### Variables d'environnement requises

```bash
GAS_WEBAPP_URL=https://script.google.com/macros/s/XXXXXXXXXXXX/exec
GAS_API_SECRET=le-meme-secret-que-API_SECRET-cote-apps-script   # optionnel mais recommandé
```

Sans `GAS_WEBAPP_URL`, tout appel de tool renverra une erreur explicite dans `tools_used`
(le LLM en sera informé et pourra le signaler à l'utilisateur), mais `/chat` continuera de
fonctionner normalement pour les questions qui n'ont pas besoin d'outils.

### Fonctionnement

1. Le prompt de l'utilisateur est envoyé au LLM actif avec la liste des 4 tools disponibles
   (format function calling propre à chaque fournisseur — OpenAI-style pour Groq/Mistral/
   OpenRouter, `functionDeclarations` pour Gemini).
2. Le LLM décide **seul**, selon la question, s'il doit appeler un ou plusieurs outils
   (ex : "Résume mes derniers mails" → `get_unread_emails`).
3. Si oui, le backend exécute la fonction Python correspondante (`connector.py`), qui appelle
   le Web App Apps Script.
4. Le résultat est renvoyé au LLM dans un second appel API, qui rédige alors sa réponse finale
   en tenant compte du résultat de l'outil.
5. Ce cycle peut se répéter (max **5 itérations**, garde-fou anti-boucle infinie) si le LLM a
   besoin d'enchaîner plusieurs outils avant de répondre.

La réponse de `/chat` inclut désormais un champ `tools_used` qui détaille chaque outil appelé,
ses arguments, et son résultat — utile pour du debug ou pour afficher la traçabilité côté
frontend.

### Exemples

```bash
# Le LLM détecte qu'il doit chercher dans Drive
curl -X POST https://ton-service.onrender.com/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Trouve-moi le fichier budget 2026 dans mon Drive et résume son contenu."}'

# Le LLM détecte qu'il doit lire les emails non lus
curl -X POST https://ton-service.onrender.com/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Ai-je reçu des mails importants aujourd'\''hui ?"}'

# Le LLM détecte qu'il doit envoyer un email
curl -X POST https://ton-service.onrender.com/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Envoie un email à paul@example.com pour lui dire que la réunion est reportée à 15h."}'

# Désactiver le function calling pour une requête (réponse texte pure)
curl -X POST https://ton-service.onrender.com/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explique-moi la photosynthèse.", "use_tools": false}'
```

Réponse type avec un outil utilisé :

```json
{
  "provider": "groq",
  "model": "llama-3.3-70b-versatile",
  "response": "Tu as 3 e-mails non lus. Le plus récent vient de...",
  "tools_used": [
    {
      "name": "get_unread_emails",
      "arguments": {},
      "result": { "emails": [ { "from": "...", "subject": "...", "snippet": "..." } ] }
    }
  ]
}
```

### Limites connues

- Tous les fournisseurs ne supportent pas le function calling avec la même fiabilité selon
  le modèle choisi ; les modèles `:free` d'OpenRouter en particulier peuvent parfois ignorer
  les tools ou mal formatter les arguments. En cas de doute, teste avec Groq (Llama 3.3 70B)
  qui a un support function calling robuste.
- Chaque itération d'outil consomme un appel API supplémentaire au LLM (donc du quota gratuit
  et de la latence) : `MAX_TOOL_ITERATIONS = 5` dans `main.py` limite les dérives.

## Personnalité JARVIS

`persona.py` définit un prompt système appliqué **par défaut à tous les appels `/chat`**,
quel que soit le fournisseur actif :

- Ton distingué, humour sec, esprit caustique tourné vers les situations (jamais vers l'utilisateur).
- Vouvoiement systématique, adresse « Monsieur » (ou « Sir » en anglais) placée avec à-propos.
- Réponses volontairement courtes (1 à 2 phrases), pensées pour être lues à voix haute par le
  frontend PWA plutôt que pour être lues à l'écran.
- Consigne explicite de résumer les résultats longs (recherche Drive, e-mails) plutôt que de
  les réciter, puisque le détail complet est de toute façon affiché via les cartes d'outils
  côté frontend.
- Usage des tools sans hésitation ni confirmation superflue quand la demande l'implique.

Le champ `system` envoyé dans le corps de `/chat` **s'ajoute** à cette personnalité (instructions
ponctuelles supplémentaires) au lieu de la remplacer — JARVIS reste JARVIS même si le client
précise un contexte additionnel.

### Surcharge sans toucher au code

```bash
JARVIS_SYSTEM_PROMPT="..."   # remplace entièrement le prompt système par défaut
JARVIS_BOOT_MESSAGE="..."    # remplace entièrement le message de boot
```

### `GET /boot`

Message d'accueil fixe, à appeler par le frontend à la connexion. Volontairement **non généré
par un LLM** (texte statique renvoyé instantanément) pour garantir un démarrage rapide et
toujours identique, même si un fournisseur LLM est indisponible.

```bash
curl https://ton-service.onrender.com/boot
```

```json
{ "message": "Système en ligne, Monsieur. Tous les connecteurs Drive et Gmail sont opérationnels. Quel est votre bon plaisir ?" }
```

## Documentation interactive

Une fois lancé, Swagger UI est disponible sur `/docs` et Redoc sur `/redoc`.