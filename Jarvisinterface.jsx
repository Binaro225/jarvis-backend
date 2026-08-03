/**
 * JarvisInterface.jsx
 * --------------------
 * Correction : Suppression des boucles de messages + Gestion silencieuse du micro
 */

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Canvas } from '@react-three/fiber';
import GalaxyScene from './galaxyscene.jsx';

const LS_BACKEND = 'jarvis_backend_url';
const LS_PROVIDER = 'jarvis_provider';
const LS_MODEL = 'jarvis_model';
const MAX_HISTORY_MESSAGES = 16;

export default function JarvisInterface() {
  const [backendUrl, setBackendUrl] = useState(() => localStorage.getItem(LS_BACKEND) || '');
  const [connected, setConnected] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(!localStorage.getItem(LS_BACKEND));

  const [jarvisState, setJarvisState] = useState('idle'); // idle | listening | thinking | speaking
  const [responseText, setResponseText] = useState('');
  const [cardVisible, setCardVisible] = useState(false);

  const [galaxyNodes, setGalaxyNodes] = useState([]);
  const [galaxyLinks, setGalaxyLinks] = useState([]);
  const [sourceNodeIds, setSourceNodeIds] = useState([]);
  const [activeCategories, setActiveCategories] = useState([]);

  const [models, setModels] = useState({});
  const [selectedProvider, setSelectedProvider] = useState(() => localStorage.getItem(LS_PROVIDER) || '');
  const [selectedModel, setSelectedModel] = useState(() => localStorage.getItem(LS_MODEL) || '');
  const [modelPickerOpen, setModelPickerOpen] = useState(false);

  const [textValue, setTextValue] = useState('');
  const [isListening, setIsListening] = useState(false);

  const historyRef = useRef([]);
  const recognitionRef = useRef(null);
  const isSpeakingRef = useRef(false);
  const transcriptBufferRef = useRef('');

  // --------------------------------------------------------------------------
  // Chargement Initial
  // --------------------------------------------------------------------------
  const loadGalaxy = useCallback(async (url) => {
    try {
      const res = await fetch(`${url.replace(/\/$/, '')}/api/galaxy`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setGalaxyNodes(data.nodes || []);
      setGalaxyLinks(data.links || []);
      setConnected(true);
    } catch (e) {
      setConnected(false);
    }
  }, []);

  const loadModels = useCallback(async (url) => {
    try {
      const res = await fetch(`${url.replace(/\/$/, '')}/api/models`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setModels(data || {});
    } catch (e) {}
  }, []);

  useEffect(() => {
    if (backendUrl) {
      loadGalaxy(backendUrl);
      loadModels(backendUrl);
    }
  }, [backendUrl, loadGalaxy, loadModels]);

  useEffect(() => {
    if ('speechSynthesis' in window) {
      window.speechSynthesis.getVoices();
      window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
    }
  }, []);

  // --------------------------------------------------------------------------
  // Synthèse Vocale (Bloque le micro pendant la réponse)
  // --------------------------------------------------------------------------
  const speak = useCallback((text) => {
    if (!window.speechSynthesis || !text) {
      isSpeakingRef.current = false;
      setJarvisState('idle');
      return;
    }

    isSpeakingRef.current = true;
    window.speechSynthesis.cancel();

    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = 'fr-FR';
    utter.rate = 1.0;

    utter.onstart = () => {
      isSpeakingRef.current = true;
      setJarvisState('speaking');
    };

    const handleSpeechEnd = () => {
      isSpeakingRef.current = false;
      setJarvisState('idle');
    };

    utter.onend = handleSpeechEnd;
    utter.onerror = handleSpeechEnd;

    window.speechSynthesis.speak(utter);
  }, []);

  // --------------------------------------------------------------------------
  // Envoi au Backend (Sans Boucle)
  // --------------------------------------------------------------------------
  const sendPrompt = useCallback(async (prompt) => {
    if (!prompt || !prompt.trim() || !backendUrl) return;

    // Vider le buffer immédiatement pour empêcher toute répitation
    transcriptBufferRef.current = '';

    const snapshot = historyRef.current.slice();
    historyRef.current = [...historyRef.current, { role: 'user', content: prompt }].slice(-MAX_HISTORY_MESSAGES);

    setCardVisible(false);
    setJarvisState('thinking');

    try {
      const res = await fetch(`${backendUrl.replace(/\/$/, '')}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt,
          history: snapshot,
          provider: selectedProvider || undefined,
          model: selectedModel || undefined,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      historyRef.current = [...historyRef.current, { role: 'assistant', content: data.response }].slice(-MAX_HISTORY_MESSAGES);
      setResponseText(data.response || '');
      setCardVisible(true);
      setSourceNodeIds(data.source_node_ids || []);
      setActiveCategories(data.active_categories || []);

      speak(data.response || '');
    } catch (e) {
      setResponseText("Erreur de connexion avec le backend.");
      setCardVisible(true);
      setJarvisState('idle');
      isSpeakingRef.current = false;
    }
  }, [backendUrl, selectedProvider, selectedModel, speak]);

  // --------------------------------------------------------------------------
  // Reconnaissance Vocale Ponctuelle (Empêche les bips en boucle)
  // --------------------------------------------------------------------------
  useEffect(() => {
    const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognitionCtor) return;

    const recognition = new SpeechRecognitionCtor();
    recognition.lang = 'fr-FR';
    recognition.continuous = false; // Mode coup par coup : empêche le micro de tourner et biper sans arrêt
    recognition.interimResults = false;

    recognition.onstart = () => {
      setIsListening(true);
      setJarvisState('listening');
      transcriptBufferRef.current = '';
    };

    recognition.onresult = (e) => {
      if (e.results.length > 0) {
        transcriptBufferRef.current = e.results[0][0].transcript;
      }
    };

    recognition.onend = () => {
      setIsListening(false);
      const finalText = transcriptBufferRef.current.trim();
      
      // Réinitialisation stricte
      transcriptBufferRef.current = '';

      if (finalText && finalText.length >= 2) {
        sendPrompt(finalText);
      } else {
        setJarvisState('idle');
      }
    };

    recognition.onerror = () => {
      setIsListening(false);
      transcriptBufferRef.current = '';
      setJarvisState('idle');
    };

    recognitionRef.current = recognition;
  }, [sendPrompt]);

  const toggleMic = () => {
    if (isSpeakingRef.current) {
      window.speechSynthesis.cancel();
      isSpeakingRef.current = false;
    }

    if (isListening) {
      if (recognitionRef.current) {
        try { recognitionRef.current.abort(); } catch (e) {}
      }
      setIsListening(false);
      setJarvisState('idle');
    } else {
      if (recognitionRef.current) {
        try {
          transcriptBufferRef.current = '';
          recognitionRef.current.start();
        } catch (e) {}
      }
    }
  };

  // --------------------------------------------------------------------------
  // Rendu UI
  // --------------------------------------------------------------------------
  const saveBackendUrl = (url) => {
    setBackendUrl(url);
    localStorage.setItem(LS_BACKEND, url);
    setSettingsOpen(false);
  };

  const chooseModel = async (provider, modelId) => {
    setSelectedProvider(provider);
    setSelectedModel(modelId);
    localStorage.setItem(LS_PROVIDER, provider);
    localStorage.setItem(LS_MODEL, modelId);
    setModelPickerOpen(false);
    if (backendUrl) {
      try {
        await fetch(`${backendUrl.replace(/\/$/, '')}/api/models/select`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider, model: modelId }),
        });
      } catch (e) {}
    }
  };

  return (
    <div style={styles.app}>
      <style>{CSS}</style>

      <div style={styles.topbar}>
        <div style={styles.brand}>
          <span className={`jx-dot ${connected ? 'jx-dot-on' : 'jx-dot-off'}`} />
          <span style={styles.brandName}>J.A.R.V.I.S.</span>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button style={styles.iconBtn} onClick={() => setModelPickerOpen(true)}>🧠</button>
          <button style={styles.iconBtn} onClick={() => setSettingsOpen(true)}>⚙</button>
        </div>
      </div>

      <div style={styles.canvasWrap}>
        <Canvas camera={{ position: [0, 6, 22], fov: 55 }}>
          <GalaxyScene
            nodes={galaxyNodes}
            links={galaxyLinks}
            sourceNodeIds={sourceNodeIds}
            activeCategories={activeCategories}
            jarvisState={jarvisState}
          />
        </Canvas>
      </div>

      {cardVisible && responseText && (
        <div style={styles.responseCard}>
          <p style={styles.responseText}>{responseText}</p>
        </div>
      )}

      <div style={styles.inputbar}>
        <button
          style={{ ...styles.micBtn, ...(jarvisState === 'listening' ? styles.micBtnListening : {}) }}
          onClick={toggleMic}
        >
          🎙
        </button>
        <input
          style={styles.textField}
          value={textValue}
          onChange={(e) => setTextValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && textValue.trim()) {
              sendPrompt(textValue.trim());
              setTextValue('');
            }
          }}
          placeholder="Écrire un message..."
        />
      </div>

      {settingsOpen && (
        <div style={styles.modalBackdrop} onClick={() => setSettingsOpen(false)}>
          <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
            <h3 style={styles.modalTitle}>Réglages</h3>
            <input
              style={styles.fieldInput}
              defaultValue={backendUrl}
              placeholder="https://ton-service.onrender.com"
              onKeyDown={(e) => { if (e.key === 'Enter') saveBackendUrl(e.target.value.trim()); }}
              id="jx-backend-input"
            />
            <button
              style={styles.modalBtn}
              onClick={() => saveBackendUrl(document.getElementById('jx-backend-input').value.trim())}
            >
              Enregistrer
            </button>
          </div>
        </div>
      )}

      {modelPickerOpen && (
        <div style={styles.modalBackdrop} onClick={() => setModelPickerOpen(false)}>
          <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
            <h3 style={styles.modalTitle}>Choisir un cerveau</h3>
            {Object.keys(models).length === 0 && <p style={{ color: '#7fa3b3' }}>Aucun modèle découvert.</p>}
            {Object.entries(models).map(([provider, list]) => (
              <div key={provider} style={{ marginBottom: 14 }}>
                <div style={styles.providerLabel}>{provider}</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {list.map((m) => (
                    <button
                      key={m.id}
                      style={{
                        ...styles.modelChip,
                        ...(selectedProvider === provider && selectedModel === m.id ? styles.modelChipActive : {}),
                      }}
                      onClick={() => chooseModel(provider, m.id)}
                    >
                      {m.name || m.id}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const styles = {
  app: {
    position: 'fixed', inset: 0, background: '#02060c', color: '#eaf7fb',
    fontFamily: "'Rajdhani', sans-serif", overflow: 'hidden',
    display: 'flex', flexDirection: 'column',
  },
  topbar: {
    position: 'absolute', top: 0, left: 0, right: 0, zIndex: 10,
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '14px 16px', paddingTop: 'calc(14px + env(safe-area-inset-top, 0px))',
  },
  brand: { display: 'flex', alignItems: 'center', gap: 8 },
  brandName: { fontWeight: 700, letterSpacing: 2, fontSize: 14, color: '#7ff2ff' },
  iconBtn: {
    width: 36, height: 36, borderRadius: '50%', border: '1px solid rgba(110,220,255,.22)',
    background: 'rgba(9,24,42,.55)', color: '#7ff2ff', fontSize: 16, cursor: 'pointer',
  },
  canvasWrap: { position: 'absolute', inset: 0 },
  responseCard: {
    position: 'absolute', left: 14, right: 14, bottom: 90, zIndex: 10,
    background: 'rgba(10,28,48,.85)', border: '1px solid rgba(110,220,255,.25)',
    borderRadius: 16, padding: '14px 16px', backdropFilter: 'blur(6px)',
    boxShadow: '0 0 24px rgba(34,232,255,.15)',
  },
  responseText: { margin: 0, fontSize: 15.5, lineHeight: 1.4, color: '#eaf7fb' },
  inputbar: {
    position: 'absolute', left: 0, right: 0, bottom: 0, zIndex: 10,
    display: 'flex', gap: 8, alignItems: 'center', padding: 12,
    paddingBottom: 'calc(12px + env(safe-area-inset-bottom, 0px))',
  },
  micBtn: {
    width: 54, height: 54, borderRadius: '50%', flexShrink: 0,
    background: 'radial-gradient(circle at 35% 30%, rgba(34,232,255,.35), rgba(9,24,42,.9))',
    border: '1px solid #22e8ff', color: '#eafeff', fontSize: 20, cursor: 'pointer',
  },
  micBtnListening: {
    background: 'radial-gradient(circle at 35% 30%, rgba(255,176,46,.4), rgba(42,24,9,.9))',
    borderColor: '#ffb02e',
  },
  textField: {
    flex: 1, background: 'rgba(9,24,42,.55)', border: '1px solid rgba(110,220,255,.22)',
    borderRadius: 999, padding: '12px 16px', color: '#eaf7fb', fontSize: 15, outline: 'none',
  },
  modalBackdrop: {
    position: 'fixed', inset: 0, background: 'rgba(0,4,10,.72)', zIndex: 20,
    display: 'flex', alignItems: 'flex-end',
  },
  modal: {
    width: '100%', background: '#050f1c', borderTop: '1px solid rgba(110,220,255,.22)',
    borderRadius: '20px 20px 0 0', padding: '20px 18px calc(24px + env(safe-area-inset-bottom, 0px))',
    maxHeight: '75vh', overflowY: 'auto',
  },
  modalTitle: { margin: '0 0 14px', fontSize: 14, letterSpacing: 1.5, color: '#7ff2ff', textTransform: 'uppercase' },
  fieldInput: {
    width: '100%', background: 'rgba(9,24,42,.55)', border: '1px solid rgba(110,220,255,.22)',
    borderRadius: 8, padding: '11px 13px', color: '#eaf7fb', fontSize: 13, marginBottom: 14, outline: 'none',
    boxSizing: 'border-box',
  },
  modalBtn: {
    width: '100%', padding: 13, borderRadius: 10, border: '1px solid #22e8ff',
    background: 'linear-gradient(135deg, rgba(34,232,255,.25), rgba(34,232,255,.08))',
    color: '#7ff2ff', fontSize: 12, letterSpacing: 1.5, textTransform: 'uppercase', cursor: 'pointer',
  },
  providerLabel: { fontSize: 11, letterSpacing: 1, color: '#7fa3b3', textTransform: 'uppercase', marginBottom: 6 },
  modelChip: {
    padding: '7px 12px', borderRadius: 999, border: '1px solid rgba(110,220,255,.22)',
    background: 'rgba(9,24,42,.55)', color: '#7fa3b3', fontSize: 11.5, cursor: 'pointer',
  },
  modelChipActive: { borderColor: '#22e8ff', color: '#7ff2ff', boxShadow: '0 0 10px rgba(34,232,255,.25)' },
};

const CSS = `
  .jx-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .jx-dot-on { background: #22e8ff; box-shadow: 0 0 8px #22e8ff; animation: jx-blink 2.6s ease-in-out infinite; }
  .jx-dot-off { background: #ff4d5e; box-shadow: 0 0 8px #ff4d5e; }
  @keyframes jx-blink { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
`;
