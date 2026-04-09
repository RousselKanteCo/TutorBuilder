"""
tts_providers.py — Système de providers TTS interchangeables pour TutoBuilder Vision.

Architecture :
    TTSProvider (classe abstraite)
    ├── ElevenLabsProvider   — API cloud, meilleure qualité, multilingue dynamique
    ├── CoquiTTSProvider     — local, gratuit, open source
    ├── BarkProvider         — local, expressif (émotions, rires, pauses)
    └── Pyttsx3TTSProvider   — Windows SAPI5, fallback offline

VOIX DYNAMIQUE :
    ElevenLabs avec eleven_multilingual_v2 détecte automatiquement la langue
    du texte passé. Une seule voix = même personnage en FR, EN, ES, DE, IT...
    Il suffit de passer le texte dans la bonne langue — le modèle s'adapte.
"""

import os
import subprocess
from abc import ABC, abstractmethod
from typing import Optional, Dict, List


class TTSProvider(ABC):

    @property
    @abstractmethod
    def nom(self) -> str: ...

    @property
    @abstractmethod
    def est_local(self) -> bool: ...

    @abstractmethod
    def voix_disponibles(self) -> List[Dict[str, str]]: ...

    @abstractmethod
    def generer(self, texte: str, voix: str, output_dir: str,
                filename: Optional[str] = None, **kwargs) -> Optional[str]: ...

    @abstractmethod
    def est_disponible(self) -> bool: ...

    def info(self) -> str:
        return f"{self.nom} ({'🏠 Local' if self.est_local else '☁️ Cloud'})"


# ═══════════════════════════════════════════════════════════════
#  ELEVENLABS — Multilingue dynamique
# ═══════════════════════════════════════════════════════════════
#
#  COMMENT ÇA MARCHE :
#  ────────────────────
#  Le modèle eleven_multilingual_v2 analyse le texte reçu et
#  synthétise dans la langue détectée, avec la même voix.
#
#  Exemple :
#    voix = "narrateur_pro"
#    texte FR → "Bonjour, bienvenue dans ce tutoriel..."   → voix FR
#    texte EN → "Hello, welcome to this tutorial..."       → même voix EN
#    texte ES → "Hola, bienvenido a este tutorial..."      → même voix ES
#
#  Langues supportées : FR, EN, ES, DE, IT, PT, PL, NL, HI, JA, ZH, AR
#
#  AJOUTER VOS PROPRES VOIX :
#  ────────────────────────────
#  1. https://elevenlabs.io/voice-library → filtrer French
#  2. Écouter → "Add to my voices"
#  3. My Voices → copier le Voice ID (21 caractères)
#  4. Ajouter dans VOIX_CATALOGUE ci-dessous
#
#  RÉGLAGES :
#  ──────────
#  stability        [0–1] : 0.3 = très expressif | 0.7 = très constant
#  similarity_boost [0–1] : fidélité à la voix originale (recommandé: 0.80)
#  style            [0–1] : accentue le style (recommandé: 0.35)
#  use_speaker_boost      : toujours True en production
# ═══════════════════════════════════════════════════════════════

class ElevenLabsProvider(TTSProvider):

    # ── Catalogue : une voix = un personnage, toutes les langues ──────────
    VOIX_CATALOGUE = {

        # ── Voix masculines ───────────────────────────────────────────────
        "narrateur_pro": {
            "id":     "onwK4e9ZLuTAKqWW03F9",
            "label":  "🎙️ Narrateur Pro — Clair, professionnel",
            "desc":   "Idéal pour tutoriels, documentations techniques",
            "settings": {"stability": 0.50, "similarity_boost": 0.80, "style": 0.30},
        },
        "expert": {
            "id":     "N2lVS1w4EtoT3dr4eOWO",
            "label":  "👨‍💼 Expert — Grave, autorité",
            "desc":   "Présentations formelles, formations professionnelles",
            "settings": {"stability": 0.55, "similarity_boost": 0.75, "style": 0.25},
        },
        "guide": {
            "id":     "TX3LPaxmHKxFdv7VOQHJ",
            "label":  "👨 Guide — Chaleureux, accessible",
            "desc":   "Formations grand public, e-learning",
            "settings": {"stability": 0.40, "similarity_boost": 0.80, "style": 0.40},
        },

        # ── Voix féminines ────────────────────────────────────────────────
        "narratrice_pro": {
            "id":     "XB0fDUnXU5powFXDhCwa",
            "label":  "🎙️ Narratrice Pro — Douce, narrative",
            "desc":   "Idéal pour documentaires, e-learning",
            "settings": {"stability": 0.45, "similarity_boost": 0.80, "style": 0.35},
        },
        "experte": {
            "id":     "XrExE9yKIg1WjnnlVkGX",
            "label":  "👩‍💼 Experte — Confiante, expressive",
            "desc":   "Formations professionnelles, pitches",
            "settings": {"stability": 0.50, "similarity_boost": 0.78, "style": 0.30},
        },
        "pedagogique": {
            "id":     "cgSgspJ2msm6clMCkdW9",
            "label":  "👩‍🏫 Pédagogique — Claire, rassurante",
            "desc":   "Tutoriels techniques, formation step-by-step",
            "settings": {"stability": 0.60, "similarity_boost": 0.82, "style": 0.20},
        },
    }

    # ── Mapping langue → paramètre language_code ElevenLabs ───────────────
    LANGUE_CODES = {
        "fr": "fr", "fr-FR": "fr", "french": "fr",
        "en": "en", "en-US": "en", "en-GB": "en", "english": "en",
        "es": "es", "es-ES": "es", "spanish": "es",
        "de": "de", "german": "de",
        "it": "it", "italian": "it",
        "pt": "pt", "portuguese": "pt",
        "nl": "nl", "dutch": "nl",
        "pl": "pl", "polish": "pl",
        "hi": "hi", "hindi": "hi",
        "ja": "ja", "japanese": "ja",
        "zh": "zh", "chinese": "zh",
        "ar": "ar", "arabic": "ar",
    }

    def __init__(self, api_key: str = "",
                 model: str = "eleven_multilingual_v2",
                 output_format: str = "mp3_44100_128"):
        self.api_key       = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        self.model         = model
        self.output_format = output_format
        # eleven_multilingual_v2  → qualité max, toutes langues (recommandé)
        # eleven_turbo_v2_5       → 2× plus rapide, très bon
        # eleven_flash_v2_5       → ultra rapide, pour tests

    @property
    def nom(self) -> str:
        return f"ElevenLabs ({self.model.replace('eleven_', '').replace('_', ' ')})"

    @property
    def est_local(self) -> bool:
        return False

    def voix_disponibles(self) -> List[Dict[str, str]]:
        return [
            {"id": k, "label": v["label"], "desc": v.get("desc", ""), "langue": "multi"}
            for k, v in self.VOIX_CATALOGUE.items()
        ]

    def _get_voice_config(self, voix: str) -> tuple[str, dict]:
        """
        Retourne (voice_id_elevenlabs, voice_settings).
        Accepte soit une clé catalogue ("narrateur_pro")
        soit un voice_id direct ElevenLabs (21 chars).
        """
        if voix in self.VOIX_CATALOGUE:
            cfg = self.VOIX_CATALOGUE[voix]
            return cfg["id"], cfg.get("settings", {})

        # Voice ID direct (passé depuis l'interface ou l'API)
        return voix, {"stability": 0.50, "similarity_boost": 0.80, "style": 0.30}

    def _mp3_to_wav(self, mp3_path: str, wav_path: str) -> bool:
        """Convertit MP3 → WAV 22050Hz mono via ffmpeg (compatible pipeline numpy)."""
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", mp3_path,
                 "-ar", "22050", "-ac", "1",
                 "-sample_fmt", "s16",
                 wav_path],
                capture_output=True, text=True,
            )
            return result.returncode == 0 and os.path.exists(wav_path)
        except FileNotFoundError:
            print("❌ ffmpeg introuvable — conversion MP3→WAV impossible.")
            return False

    def generer(self, texte: str, voix: str, output_dir: str,
                filename: Optional[str] = None, **kwargs) -> Optional[str]:
        import requests

        if not self.api_key:
            print("❌ ElevenLabs : ELEVENLABS_API_KEY manquante dans .env")
            return None

        if not texte or not texte.strip():
            print("⚠️ ElevenLabs : texte vide, segment ignoré.")
            return None

        # Résoudre voix → voice_id + settings
        voice_id, settings = self._get_voice_config(voix)

        # Langue : détection automatique par le modèle (eleven_multilingual_v2)
        # On peut passer language_code pour forcer si nécessaire
        langue_brute   = kwargs.get("langue", "fr")
        language_code  = self.LANGUE_CODES.get(langue_brute, langue_brute[:2].lower())

        # Chemins fichiers
        base     = (filename or f"el_{voix[:12]}.wav").replace(".wav", "")
        mp3_path = os.path.join(output_dir, base + ".mp3")
        wav_path = os.path.join(output_dir, base + ".wav")

        # ── Appel API ElevenLabs ──────────────────────────────────────────
        url     = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key":   self.api_key,
            "Content-Type": "application/json",
            "Accept":       "audio/mpeg",
        }
        payload = {
            "text":       texte.strip(),
            "model_id":   self.model,
            "language_code": language_code,   # Guide la détection, pas obligatoire
            "voice_settings": {
                "stability":         settings.get("stability",        0.50),
                "similarity_boost":  settings.get("similarity_boost", 0.80),
                "style":             settings.get("style",            0.30),
                "use_speaker_boost": True,
            },
        }

        try:
            r = requests.post(
                url, json=payload, headers=headers,
                timeout=kwargs.get("timeout", 60),
            )

            if r.status_code == 401:
                print("❌ ElevenLabs : clé API invalide.")
                return None
            if r.status_code == 422:
                print(f"❌ ElevenLabs : voice_id '{voice_id}' introuvable. Vérifiez My Voices.")
                return None
            if r.status_code != 200:
                print(f"⚠️ ElevenLabs HTTP {r.status_code}: {r.text[:300]}")
                return None

            # Sauvegarder le MP3
            with open(mp3_path, "wb") as f:
                f.write(r.content)

            # Convertir en WAV pour le pipeline numpy
            if self._mp3_to_wav(mp3_path, wav_path):
                try:
                    os.remove(mp3_path)
                except Exception:
                    pass
                return wav_path

            # Fallback : retourner le MP3 si conversion échouée
            print("⚠️ Conversion WAV échouée — retour MP3 (compatibilité réduite)")
            return mp3_path

        except requests.Timeout:
            print(f"❌ ElevenLabs : timeout ({kwargs.get('timeout', 60)}s)")
            return None
        except requests.RequestException as e:
            print(f"❌ ElevenLabs réseau : {e}")
            return None

    def est_disponible(self) -> bool:
        return bool(self.api_key)

    def tester_connexion(self) -> bool:
        """Vérifie que la clé API est valide et liste les voix disponibles."""
        import requests
        try:
            r = requests.get(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": self.api_key},
                timeout=10,
            )
            if r.status_code == 200:
                data   = r.json()
                voices = data.get("voices", [])
                print(f"✅ ElevenLabs connecté — {len(voices)} voix disponibles dans votre compte.")
                for v in voices:
                    print(f"   {v['name']:30s} → {v['voice_id']}")
                return True
            print(f"❌ ElevenLabs auth échouée : {r.status_code}")
            return False
        except Exception as e:
            print(f"❌ ElevenLabs test connexion : {e}")
            return False


# ═══════════════════════════════════════════════
#  COQUI TTS (LOCAL)
# ═══════════════════════════════════════════════

class CoquiTTSProvider(TTSProvider):

    def __init__(self, model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2"):
        self.model_name = model_name
        self._tts = None

    @property
    def nom(self) -> str:
        return "Coqui TTS"

    @property
    def est_local(self) -> bool:
        return True

    def voix_disponibles(self) -> List[Dict[str, str]]:
        return [
            {"id": "default", "label": "🔊 Voix par défaut", "langue": "fr"},
            {"id": "clone",   "label": "🎭 Cloner une voix (.wav)", "langue": "multi"},
        ]

    def _charger_modele(self):
        if self._tts is None:
            from TTS.api import TTS
            self._tts = TTS(model_name=self.model_name)
        return self._tts

    def generer(self, texte: str, voix: str, output_dir: str,
                filename: Optional[str] = None, **kwargs) -> Optional[str]:
        try:
            tts = self._charger_modele()
        except ImportError:
            print("❌ Coqui TTS non installé : pip install TTS")
            return None

        filename    = filename or f"coqui_{voix}.wav"
        output      = os.path.join(output_dir, filename)
        langue      = kwargs.get("langue", "fr")
        speaker_wav = kwargs.get("speaker_wav")

        try:
            if voix == "clone" and speaker_wav:
                tts.tts_to_file(text=texte, file_path=output,
                                speaker_wav=speaker_wav, language=langue)
            else:
                tts_kwargs = {"text": texte, "file_path": output}
                if hasattr(tts, "languages") and tts.languages:
                    tts_kwargs["language"] = langue
                tts.tts_to_file(**tts_kwargs)
            return output if os.path.exists(output) else None
        except Exception as e:
            print(f"❌ Coqui erreur : {e}")
            return None

    def est_disponible(self) -> bool:
        try:
            import TTS
            return True
        except ImportError:
            return False


# ═══════════════════════════════════════════════
#  BARK (LOCAL, EXPRESSIF)
# ═══════════════════════════════════════════════

class BarkProvider(TTSProvider):

    VOIX_PRESETS = {
        "homme_fr":  "v2/fr_speaker_0",
        "femme_fr":  "v2/fr_speaker_1",
        "homme_en":  "v2/en_speaker_6",
        "femme_en":  "v2/en_speaker_9",
    }

    def __init__(self, use_gpu: bool = True, use_small: bool = False):
        self.use_gpu   = use_gpu
        self.use_small = use_small

    @property
    def nom(self) -> str:
        return "Bark (Suno)"

    @property
    def est_local(self) -> bool:
        return True

    def voix_disponibles(self) -> List[Dict[str, str]]:
        return [
            {"id": "homme_fr", "label": "🧑 Homme français", "langue": "fr"},
            {"id": "femme_fr", "label": "👩 Femme française", "langue": "fr"},
            {"id": "homme_en", "label": "👨 Homme anglais",   "langue": "en"},
            {"id": "femme_en", "label": "👩 Femme anglaise",  "langue": "en"},
        ]

    def generer(self, texte: str, voix: str, output_dir: str,
                filename: Optional[str] = None, **kwargs) -> Optional[str]:
        try:
            os.environ["SUNO_USE_SMALL_MODELS"] = "1" if self.use_small else "0"
            if not self.use_gpu:
                os.environ["SUNO_OFFLOAD_CPU"] = "1"
            from bark import generate_audio, SAMPLE_RATE
            from bark.generation import preload_models
            import numpy as np
            from scipy.io.wavfile import write as write_wav
        except ImportError:
            print("❌ Bark non installé : pip install git+https://github.com/suno-ai/bark.git scipy")
            return None

        filename = filename or f"bark_{voix}.wav"
        output   = os.path.join(output_dir, filename)
        preset   = self.VOIX_PRESETS.get(voix, "v2/fr_speaker_0")

        try:
            preload_models()
            audio = generate_audio(texte, history_prompt=preset)
            audio = np.clip(audio, -1.0, 1.0)
            write_wav(output, SAMPLE_RATE, (audio * 32767).astype(np.int16))
            return output if os.path.exists(output) else None
        except Exception as e:
            print(f"❌ Bark erreur : {e}")
            return None

    def est_disponible(self) -> bool:
        try:
            import bark
            return True
        except ImportError:
            return False


# ═══════════════════════════════════════════════
#  PYTTSX3 — Fallback Windows offline
# ═══════════════════════════════════════════════

class Pyttsx3TTSProvider(TTSProvider):

    def __init__(self, rate: int = 150, volume: float = 1.0):
        self.rate   = rate
        self.volume = volume

    @property
    def nom(self) -> str:
        return "Windows TTS (pyttsx3)"

    @property
    def est_local(self) -> bool:
        return True

    def voix_disponibles(self) -> List[Dict[str, str]]:
        try:
            import pyttsx3
            engine = pyttsx3.init()
            voices = engine.getProperty("voices")
            engine.stop()
            return [{"id": v.id, "label": v.name, "langue": "fr"} for v in voices] \
                   or [{"id": "default", "label": "Voix par défaut", "langue": "fr"}]
        except Exception:
            return [{"id": "default", "label": "Voix par défaut", "langue": "fr"}]

    def generer(self, texte: str, voix: str, output_dir: str,
                filename: Optional[str] = None, **kwargs) -> Optional[str]:
        try:
            import pyttsx3
        except ImportError:
            print("❌ pyttsx3 non installé : pip install pyttsx3")
            return None

        filename = filename or "pyttsx3_output.wav"
        output   = os.path.join(output_dir, filename)

        try:
            engine = pyttsx3.init()
            engine.setProperty("rate",   self.rate)
            engine.setProperty("volume", self.volume)
            if voix and voix != "default":
                try:
                    engine.setProperty("voice", voix)
                except Exception:
                    pass
            engine.save_to_file(texte, output)
            engine.runAndWait()
            engine.stop()

            if not os.path.exists(output) or os.path.getsize(output) <= 44:
                print("❌ pyttsx3 : fichier vide")
                return None
            return output
        except Exception as e:
            print(f"❌ pyttsx3 erreur : {e}")
            return None

    def est_disponible(self) -> bool:
        try:
            import pyttsx3
            return True
        except ImportError:
            return False


# ═══════════════════════════════════════════════
#  FACTORY
# ═══════════════════════════════════════════════

class TTSProviderFactory:

    _REGISTRY = {
        "elevenlabs": ElevenLabsProvider,
        "coqui":      CoquiTTSProvider,
        "bark":       BarkProvider,
        "pyttsx3":    Pyttsx3TTSProvider,
        "piper":      Pyttsx3TTSProvider,   # alias legacy
    }

    @classmethod
    def create(cls, nom: str, **kwargs) -> Optional[TTSProvider]:
        klass = cls._REGISTRY.get(nom.lower())
        if klass is None:
            print(f"❌ Provider inconnu : '{nom}'. Disponibles : {list(cls._REGISTRY)}")
            return None
        return klass(**kwargs)

    @classmethod
    def lister(cls) -> Dict[str, str]:
        result = {}
        for name, klass in cls._REGISTRY.items():
            try:
                inst   = klass() if name != "elevenlabs" else klass(api_key="")
                status = "✅ Installé" if inst.est_disponible() else "⚠️ Non installé"
                result[name] = f"{inst.info()} — {status}"
            except Exception:
                result[name] = f"{name} — ❌ Erreur"
        return result

    @classmethod
    def enregistrer(cls, nom: str, provider_class: type):
        if not issubclass(provider_class, TTSProvider):
            raise TypeError(f"{provider_class} doit hériter de TTSProvider")
        cls._REGISTRY[nom.lower()] = provider_class