"""
tts_providers.py — Providers TTS pour TutoBuilder Vision.

Providers disponibles :
    ├── ElevenLabsProvider  — Cloud, qualité premium, multilingue automatique
    └── CartesiaProvider    — Cloud, ultra-rapide (< 100ms latence), Sonic-2
"""

import os
import subprocess
from abc import ABC, abstractmethod
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
#  UTILITAIRE : lecture durée WAV robuste (corrige bug Cartesia INT32_MAX)
# ═══════════════════════════════════════════════════════════════════════════════

def get_wav_duration_ms(wav_path: str) -> float:
    """
    Lit la vraie durée d'un fichier WAV.
    Cartesia retourne des WAV avec getnframes() = INT32_MAX (2147483647).
    Dans ce cas on calcule depuis la taille réelle du fichier.
    """
    import wave
    try:
        file_size = os.path.getsize(wav_path)
        with wave.open(wav_path, "rb") as wf:
            sr        = wf.getframerate()
            channels  = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            nframes   = wf.getnframes()

        # Détecter header corrompu (INT32_MAX)
        if nframes >= 2_000_000_000:
            data_bytes  = max(0, file_size - 44)
            real_frames = data_bytes // (sampwidth * channels)
            return max(400.0, (real_frames / sr) * 1000.0)

        return max(400.0, (nframes / sr) * 1000.0)

    except Exception:
        try:
            file_size = os.path.getsize(wav_path)
            return max(400.0, (file_size - 44) / (22050 * 2) * 1000.0)
        except Exception:
            return 3000.0


def fix_wav_header(wav_path: str) -> str:
    """
    Corrige un fichier WAV avec header corrompu via ffmpeg.
    Retourne le chemin du fichier corrigé (même chemin).
    """
    fixed = wav_path + "_fixed.wav"
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path,
         "-ar", "22050", "-ac", "1", "-sample_fmt", "s16", fixed],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and os.path.exists(fixed):
        os.replace(fixed, wav_path)
    return wav_path


# ═══════════════════════════════════════════════════════════════════════════════
#  BASE
# ═══════════════════════════════════════════════════════════════════════════════

class TTSProvider(ABC):

    @property
    @abstractmethod
    def nom(self) -> str: ...

    @abstractmethod
    def est_disponible(self) -> bool: ...

    @abstractmethod
    def generer(self, texte: str, voix: str, output_dir: str,
                filename: Optional[str] = None,
                langue: str = "fr") -> Optional[str]: ...


# ═══════════════════════════════════════════════════════════════════════════════
#  ELEVENLABS
# ═══════════════════════════════════════════════════════════════════════════════

class ElevenLabsProvider(TTSProvider):

    VOIX_CATALOGUE = {
        "narrateur_pro": {
            "id":    "onwK4e9ZLuTAKqWW03F9",
            "label": "🎙️ Narrateur Pro — Clair, professionnel",
            "desc":  "Idéal pour tutoriels, documentations techniques",
            "settings": {"stability": 0.50, "similarity_boost": 0.80, "style": 0.30},
        },
        "narratrice_pro": {
            "id":    "XB0fDUnXU5powFXDhCwa",
            "label": "🎙️ Narratrice Pro — Douce, narrative",
            "desc":  "Idéal pour documentaires, e-learning",
            "settings": {"stability": 0.45, "similarity_boost": 0.80, "style": 0.35},
        },
        "expert": {
            "id":    "N2lVS1w4EtoT3dr4eOWO",
            "label": "👨‍💼 Expert — Grave, autorité",
            "desc":  "Présentations formelles, formations professionnelles",
            "settings": {"stability": 0.55, "similarity_boost": 0.75, "style": 0.25},
        },
        "experte": {
            "id":    "XrExE9yKIg1WjnnlVkGX",
            "label": "👩‍💼 Experte — Confiante, expressive",
            "desc":  "Formations professionnelles, pitches",
            "settings": {"stability": 0.50, "similarity_boost": 0.78, "style": 0.30},
        },
        "guide": {
            "id":    "TX3LPaxmHKxFdv7VOQHJ",
            "label": "👨 Guide — Chaleureux, accessible",
            "desc":  "Formations grand public, e-learning débutants",
            "settings": {"stability": 0.40, "similarity_boost": 0.80, "style": 0.40},
        },
        "pedagogique": {
            "id":    "cgSgspJ2msm6clMCkdW9",
            "label": "👩‍🏫 Pédagogique — Claire, rassurante",
            "desc":  "Tutoriels techniques, formations step-by-step",
            "settings": {"stability": 0.60, "similarity_boost": 0.82, "style": 0.20},
        },
    }

    LANGUE_CODES = {
        "fr": "fr", "en": "en", "es": "es", "de": "de",
        "it": "it", "pt": "pt", "nl": "nl", "pl": "pl",
        "hi": "hi", "ja": "ja", "zh": "zh", "ar": "ar",
    }

    def __init__(self, api_key: str = "",
                 model: str = "eleven_multilingual_v2"):
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        self.model   = model

    @property
    def nom(self) -> str:
        return "ElevenLabs"

    def est_disponible(self) -> bool:
        return bool(self.api_key and self.api_key.startswith("sk_"))

    def _get_voice_config(self, voix: str) -> tuple[str, dict]:
        if voix in self.VOIX_CATALOGUE:
            cfg = self.VOIX_CATALOGUE[voix]
            return cfg["id"], cfg.get("settings", {})
        return voix, {"stability": 0.50, "similarity_boost": 0.80, "style": 0.30}

    def _mp3_to_wav(self, mp3_path: str, wav_path: str) -> bool:
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", mp3_path,
                 "-ar", "22050", "-ac", "1", "-sample_fmt", "s16", wav_path],
                capture_output=True, text=True,
            )
            return r.returncode == 0 and os.path.exists(wav_path)
        except FileNotFoundError:
            return False

    def generer(self, texte: str, voix: str, output_dir: str,
                filename: Optional[str] = None,
                langue: str = "fr") -> Optional[str]:
        import requests

        if not self.est_disponible():
            print("❌ ElevenLabs : ELEVENLABS_API_KEY manquante ou invalide dans .env")
            return None

        if not texte or not texte.strip():
            return None

        voice_id, settings = self._get_voice_config(voix)
        lang_code = self.LANGUE_CODES.get((langue or "fr").lower()[:2], "fr")

        base     = (filename or f"el_{voix[:12]}.wav").replace(".wav", "")
        mp3_path = os.path.join(output_dir, base + ".mp3")
        wav_path = os.path.join(output_dir, base + ".wav")

        url     = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key":   self.api_key,
            "Content-Type": "application/json",
            "Accept":       "audio/mpeg",
        }
        payload = {
            "text":          texte.strip(),
            "model_id":      self.model,
            "language_code": lang_code,
            "voice_settings": {
                "stability":         settings.get("stability",        0.50),
                "similarity_boost":  settings.get("similarity_boost", 0.80),
                "style":             settings.get("style",            0.30),
                "use_speaker_boost": True,
            },
        }

        try:
            r = requests.post(url, json=payload, headers=headers, timeout=60)

            if r.status_code == 401:
                print("❌ ElevenLabs : clé API invalide.")
                return None
            if r.status_code == 422:
                print(f"❌ ElevenLabs : voice_id '{voice_id}' introuvable.")
                return None
            if r.status_code != 200:
                print(f"⚠️ ElevenLabs HTTP {r.status_code}: {r.text[:300]}")
                return None

            with open(mp3_path, "wb") as f:
                f.write(r.content)

            if self._mp3_to_wav(mp3_path, wav_path):
                try:
                    os.remove(mp3_path)
                except Exception:
                    pass
                # Vérifier durée (ElevenLabs via ffmpeg → header correct)
                dur = get_wav_duration_ms(wav_path)
                print(f"✅ ElevenLabs → {filename} ({dur:.0f}ms)")
                return wav_path

            print("⚠️ Conversion WAV échouée — retour MP3")
            return mp3_path

        except requests.Timeout:
            print("❌ ElevenLabs : timeout 60s")
            return None
        except Exception as e:
            print(f"❌ ElevenLabs : {e}")
            return None


# ═══════════════════════════════════════════════════════════════════════════════
#  CARTESIA
# ═══════════════════════════════════════════════════════════════════════════════

class CartesiaProvider(TTSProvider):

    VOIX_CATALOGUE = {
        "narrateur_pro": {
            "id":    "a0e99841-438c-4a64-b679-ae501e7d6091",
            "label": "🎙️ Narrateur Pro — Professionnel, clair",
        },
        "narratrice_pro": {
            "id":    "79a125e8-cd45-4c13-8a67-188112f4dd22",
            "label": "🎙️ Narratrice Pro — Douce, narrative",
        },
        "expert": {
            "id":    "5619d38c-cf51-4d8e-9575-48f61a280413",
            "label": "👨‍💼 Expert — Posé, autorité",
        },
        "experte": {
            "id":    "b7d50908-b17c-442d-ad8d-810c63997ed9",
            "label": "👩‍💼 Experte — Confiante, directe",
        },
        "guide": {
            "id":    "41534e16-2966-4c6b-9670-111411def906",
            "label": "👨 Guide — Chaleureux, accessible",
        },
        "pedagogique": {
            "id":    "694f9389-aac1-45b6-b726-9d9369183238",
            "label": "👩‍🏫 Pédagogique — Claire, rassurante",
        },
    }

    LANGUE_CODES = {
        "fr": "fr", "en": "en", "es": "es", "de": "de",
        "it": "it", "pt": "pt", "nl": "nl", "ja": "ja",
        "zh": "zh", "ar": "ar", "ru": "ru", "hi": "hi",
        "sv": "sv", "tr": "tr", "ko": "ko",
    }

    BASE_URL    = "https://api.cartesia.ai/tts/bytes"
    API_VERSION = "2024-06-10"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.environ.get("CARTESIA_API_KEY", "")

    @property
    def nom(self) -> str:
        return "Cartesia (Sonic-2)"

    def est_disponible(self) -> bool:
        return bool(self.api_key)

    def generer(self, texte: str, voix: str, output_dir: str,
                filename: Optional[str] = None,
                langue: str = "fr") -> Optional[str]:
        import requests

        if not self.est_disponible():
            print("❌ Cartesia : CARTESIA_API_KEY manquante dans .env")
            return None

        if not texte or not texte.strip():
            return None

        cfg       = self.VOIX_CATALOGUE.get(voix, self.VOIX_CATALOGUE["narrateur_pro"])
        voice_id  = cfg["id"]
        lang_code = self.LANGUE_CODES.get((langue or "fr").lower()[:2], "fr")

        wav_path = os.path.join(output_dir, filename or f"ca_{voix[:12]}.wav")

        payload = {
            "model_id":   "sonic-2",
            "transcript": texte.strip(),
            "voice": {
                "mode": "id",
                "id":   voice_id,
            },
            "output_format": {
                "container":   "wav",
                "encoding":    "pcm_s16le",
                "sample_rate": 22050,
            },
            "language": lang_code,
        }

        headers = {
            "X-API-Key":        self.api_key,
            "Cartesia-Version": self.API_VERSION,
            "Content-Type":     "application/json",
        }

        try:
            r = requests.post(self.BASE_URL, json=payload, headers=headers, timeout=60)

            if r.status_code == 401:
                print("❌ Cartesia : clé API invalide.")
                return None
            if r.status_code != 200:
                print(f"⚠️ Cartesia HTTP {r.status_code}: {r.text[:300]}")
                return None

            with open(wav_path, "wb") as f:
                f.write(r.content)

            if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000:
                print("❌ Cartesia : fichier WAV vide.")
                return None

            # ── CORRECTION HEADER CORROMPU ──────────────────────────────
            # Cartesia retourne parfois getnframes() = INT32_MAX
            # On passe par ffmpeg pour réécrire un header propre
            fix_wav_header(wav_path)

            dur = get_wav_duration_ms(wav_path)
            print(f"✅ Cartesia → {filename} ({dur:.0f}ms)")
            return wav_path

        except requests.Timeout:
            print("❌ Cartesia : timeout 60s")
            return None
        except Exception as e:
            print(f"❌ Cartesia : {e}")
            return None


# ═══════════════════════════════════════════════════════════════════════════════
#  FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

class TTSProviderFactory:

    _REGISTRY = {
        "elevenlabs": ElevenLabsProvider,
        "cartesia":   CartesiaProvider,
    }

    @classmethod
    def create(cls, nom: str, **kwargs) -> Optional[TTSProvider]:
        klass = cls._REGISTRY.get(nom.lower())
        if klass is None:
            print(f"❌ Provider TTS inconnu : '{nom}'. Disponibles : {list(cls._REGISTRY)}")
            return None
        return klass(**kwargs)

    @classmethod
    def lister(cls) -> list[dict]:
        result = []
        for name, klass in cls._REGISTRY.items():
            try:
                p = klass()
                result.append({
                    "id":         name,
                    "nom":        p.nom,
                    "disponible": p.est_disponible(),
                })
            except Exception:
                result.append({"id": name, "nom": name, "disponible": False})
        return result