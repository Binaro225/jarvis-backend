"""
JARVIS - Agent de vision d'ecran (screen_agent.py)
---------------------------------------------------
Script autonome qui tourne en arriere-plan sur ta machine (independamment de la fenetre
web de JARVIS) et permet a JARVIS de "voir" ton ecran, ou qu'il soit :

  1. Raccourci clavier global (par defaut Ctrl+Alt+J) : capture instantanee de l'ecran,
     envoyee au backend avec un prompt par defaut ("Regarde mon ecran et dis-moi ce qui
     s'y passe").
  2. Commande vocale optionnelle (si les dependances vocales sont installees) : dis
     "Jarvis regarde" pour declencher la meme capture sans toucher au clavier.

Le resultat (reponse texte de JARVIS) est affiche dans la console et, si pyttsx3 est
installe, lu a voix haute localement (utile si ce script tourne sans navigateur ouvert).

INSTALLATION
    pip install pyautogui pillow keyboard requests
    # Optionnel (commande vocale) :
    pip install SpeechRecognition pyaudio
    # Optionnel (voix hors-ligne) :
    pip install pyttsx3

CONFIGURATION (variables d'environnement)
    JARVIS_BACKEND_URL   URL de ton backend FastAPI, ex: https://ton-service.onrender.com
    JARVIS_HOTKEY        Raccourci clavier (defaut: "ctrl+alt+j")
    JARVIS_PROVIDER      Fournisseur LLM preferé pour la vision (optionnel)
    JARVIS_MODEL         Modele preferé pour la vision (optionnel, laisser vide pour
                         laisser le backend choisir/decouvrir dynamiquement)

LANCEMENT
    python screen_agent.py
    (le processus reste actif ; Ctrl+C pour quitter)
"""

import os
import io
import sys
import base64
import requests

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    from PIL import Image  # noqa: F401  (utilise implicitement par pyautogui.screenshot)
except ImportError:
    pass

try:
    import keyboard
except ImportError:
    keyboard = None

# Dependances vocales optionnelles : le script fonctionne sans elles (hotkey uniquement).
try:
    import speech_recognition as sr
except ImportError:
    sr = None

try:
    import pyttsx3
    _tts_engine = pyttsx3.init()
except Exception:
    _tts_engine = None


BACKEND_URL = os.getenv("JARVIS_BACKEND_URL", "").rstrip("/")
HOTKEY = os.getenv("JARVIS_HOTKEY", "ctrl+alt+j")
PREFERRED_PROVIDER = os.getenv("JARVIS_PROVIDER") or None
PREFERRED_MODEL = os.getenv("JARVIS_MODEL") or None
DEFAULT_VISION_PROMPT = "Regarde mon ecran et dis-moi ce qui s'y passe."
VOICE_TRIGGER_PHRASES = ["jarvis regarde", "regarde mon ecran", "jarvis vision"]


def say(text: str):
    """Affiche la reponse et la lit a voix haute localement si pyttsx3 est disponible."""
    print(f"\nJARVIS > {text}\n")
    if _tts_engine:
        try:
            _tts_engine.say(text)
            _tts_engine.runAndWait()
        except Exception:
            pass  # la voix locale est un confort, pas une dependance bloquante


def capture_screen_base64() -> str:
    """Capture l'ecran entier et l'encode en base64 PNG (sans prefixe data:)."""
    if pyautogui is None:
        raise RuntimeError("pyautogui n'est pas installe. Lance : pip install pyautogui pillow")
    screenshot = pyautogui.screenshot()
    buffer = io.BytesIO()
    screenshot.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def send_to_jarvis(prompt: str, image_base64: str) -> str:
    """Envoie le prompt + l'image capturee au backend JARVIS et renvoie la reponse texte."""
    if not BACKEND_URL:
        raise RuntimeError(
            "JARVIS_BACKEND_URL n'est pas definie. Exporte cette variable d'environnement "
            "avec l'URL de ton backend avant de lancer ce script."
        )
    payload = {
        "prompt": prompt,
        "image_base64": image_base64,
        "provider": PREFERRED_PROVIDER,
        "model": PREFERRED_MODEL,
    }
    resp = requests.post(f"{BACKEND_URL}/chat", json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data.get("response", "(reponse vide)")


def trigger_screen_capture(prompt: str = DEFAULT_VISION_PROMPT):
    """Point d'entree commun : capture l'ecran, l'envoie a JARVIS, affiche/lit la reponse."""
    print("Capture d'ecran en cours...")
    try:
        image_b64 = capture_screen_base64()
        print("Envoi a JARVIS (fallback vision automatique si le premier modele echoue)...")
        answer = send_to_jarvis(prompt, image_b64)
        say(answer)
    except Exception as e:
        print(f"Erreur lors de la capture/analyse : {e}", file=sys.stderr)


# ----------------------------------------------------------------------------
# Declencheur clavier (toujours disponible si le paquet 'keyboard' est installe)
# ----------------------------------------------------------------------------

def start_hotkey_listener():
    if keyboard is None:
        print("Paquet 'keyboard' non installe : le raccourci clavier est desactive.")
        print("Installe-le avec : pip install keyboard")
        return
    keyboard.add_hotkey(HOTKEY, trigger_screen_capture)
    print(f"Raccourci actif : {HOTKEY} -> capture d'ecran + analyse par JARVIS.")


# ----------------------------------------------------------------------------
# Declencheur vocal optionnel (necessite SpeechRecognition + pyaudio)
# ----------------------------------------------------------------------------

def start_voice_listener():
    if sr is None:
        print("Commande vocale desactivee (SpeechRecognition non installe).")
        print("Installe-la avec : pip install SpeechRecognition pyaudio")
        return

    recognizer = sr.Recognizer()
    microphone = sr.Microphone()

    def on_audio(recognizer_instance, audio):
        try:
            text = recognizer_instance.recognize_google(audio, language="fr-FR").lower()
        except Exception:
            return  # silence ou incomprehension : on ignore et on continue d'ecouter
        if any(phrase in text for phrase in VOICE_TRIGGER_PHRASES):
            trigger_screen_capture()

    print("Ecoute vocale active : dis 'Jarvis regarde' pour declencher une capture d'ecran.")
    with microphone as source:
        recognizer.adjust_for_ambient_noise(source, duration=1)
    recognizer.listen_in_background(microphone, on_audio)


# ----------------------------------------------------------------------------
# Point d'entree
# ----------------------------------------------------------------------------

def main():
    print("=== JARVIS Screen Agent ===")
    if not BACKEND_URL:
        print("ATTENTION : JARVIS_BACKEND_URL n'est pas definie. Definis-la avant de continuer.")

    start_hotkey_listener()
    start_voice_listener()

    print("Agent actif. Ctrl+C pour quitter.")
    try:
        if keyboard is not None:
            keyboard.wait()  # bloque indefiniment en ecoutant le raccourci
        else:
            import time
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        print("\nArret de l'agent de vision.")


if __name__ == "__main__":
    main()
