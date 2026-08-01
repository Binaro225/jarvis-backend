import speech_recognition as sr
import pyttsx3
import pywhatkit
import datetime
import sys
import wikipedia
import random

# ==========================================
# ⚙️ CONFIGURATION AUDIO (POUR QU'IL PARLE)
# ==========================================
try:
    engine = pyttsx3.init('sapi5')
    voices = engine.getProperty('voices')
    
    # Recherche automatique de la voix française
    fr_voice = None
    for voice in voices:
        if "french" in voice.name.lower() or "français" in voice.name.lower():
            fr_voice = voice.id
            break
            
    if fr_voice:
        engine.setProperty('voice', fr_voice)
    else:
        # Si pas trouvé, on force la voix par défaut (souvent index 0 ou 1)
        engine.setProperty('voice', voices[0].id)

    engine.setProperty('rate', 170) # Vitesse de lecture un peu plus lente pour bien comprendre les articles

except Exception as e:
    print(f"Erreur configuration audio : {e}")

def parler(texte):
    """L'assistant parle (et affiche le texte)"""
    print(f"🤖 Jarvis : {texte}")
    try:
        engine.say(texte)
        engine.runAndWait()
    except:
        pass

def ecouter():
    """L'oreille de l'assistant"""
    r = sr.Recognizer()
    with sr.Microphone() as source:
        print("\n🎤 [J'écoute... Parle maintenant]")
        r.adjust_for_ambient_noise(source, duration=0.5)
        r.pause_threshold = 1.0 
        
        try:
            # On écoute un peu plus longtemps pour les questions complexes
            audio = r.listen(source, timeout=5, phrase_time_limit=10)
            print("⏳ [Réflexion...]")
            commande = r.recognize_google(audio, language='fr-FR')
            print(f"👤 Tu as dit : {commande}")
            return commande.lower()
        except sr.WaitTimeoutError:
            return ""
        except sr.UnknownValueError:
            return ""
        except sr.RequestError:
            parler("Attention, je n'ai plus d'internet.")
            return ""

# ==========================================
# 🧠 L'INTELLIGENCE (WIKIPÉDIA + LOGIQUE)
# ==========================================
def rechercher_connaissance(sujet):
    """Fonction qui va chercher l'info sur le web et la lire"""
    parler(f"Je recherche des informations sur {sujet}, un instant...")
    try:
        wikipedia.set_lang("fr")
        # On demande un résumé de 3 phrases pour ne pas que ce soit trop long
        info = wikipedia.summary(sujet, sentences=3)
        parler("Voici ce que j'ai trouvé :")
        parler(info)
        parler("Est-ce que tu veux savoir autre chose ?")
    except wikipedia.exceptions.DisambiguationError:
        parler("Ce sujet est trop vaste, sois plus précis s'il te plaît.")
    except wikipedia.exceptions.PageError:
        parler("Je n'ai trouvé aucun article correspondant dans ma base de données.")
    except:
        parler("Désolé, je n'arrive pas à lire cet article.")

# ==========================================
# 🚀 CERVEAU PRINCIPAL
# ==========================================
def cerveau():
    parler("Système encyclopédique activé. Je t'écoute.")

    while True:
        commande = ecouter()

        if commande == "":
            continue

        # --- 1. CONVERSATION "HUMAINE" SIMULÉE ---
        if "bonjour" in commande or "salut" in commande:
            reponses = ["Bonjour !", "Salut chef.", "Ravi de vous entendre."]
            parler(random.choice(reponses))

        elif "ça va" in commande or "comment vas-tu" in commande:
            parler("Je vais très bien. Mon processeur est froid et ma mémoire est vide. Et toi ?")
            
        elif "merci" in commande:
            parler("Il n'y a pas de quoi.")

        elif "qui es-tu" in commande:
            parler("Je suis Jarvis, une intelligence artificielle connectée à l'encyclopédie mondiale Wikipédia.")

        # --- 2. INTELLIGENCE (LECTURE D'ARTICLES) ---
        # Si la phrase contient ces mots déclencheurs, on lance la recherche intelligente
        
        elif "parle-moi de" in commande:
            sujet = commande.replace("parle-moi de", "").strip()
            rechercher_connaissance(sujet)

        elif "c'est quoi" in commande:
            sujet = commande.replace("c'est quoi", "").strip()
            rechercher_connaissance(sujet)

        elif "qui est" in commande:
            sujet = commande.replace("qui est", "").strip()
            rechercher_connaissance(sujet)
        
        elif "définition" in commande:
            sujet = commande.replace("définition", "").replace("de", "").strip()
            rechercher_connaissance(sujet)

        # --- 3. COMMANDES UTILES ---
        elif "youtube" in commande:
            sujet = commande.replace("youtube", "").replace("mets", "").replace("joue", "").strip()
            parler(f"Ok, je mets {sujet} sur YouTube.")
            pywhatkit.playonyt(sujet)
            
        elif "heure" in commande:
            heure = datetime.datetime.now().strftime("%H heures %M")
            parler(f"Il est {heure}.")

        elif "stop" in commande or "quitter" in commande:
            parler("Extinction du système. À bientôt.")
            sys.exit()

        # --- 4. SI ON NE COMPREND PAS, ON SUPPOSE QUE C'EST UNE RECHERCHE ---
        else:
            # Astuce : Si je ne connais pas la commande, je tente de chercher sur Wikipédia
            # Ça rend le robot "intelligent" car il essaie de comprendre n'importe quoi.
            parler("Je ne suis pas sûr de comprendre, laisse moi vérifier dans ma base de connaissances...")
            rechercher_connaissance(commande)

if __name__ == "__main__":
    cerveau()