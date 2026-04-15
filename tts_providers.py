"""
tts_providers.py — Providers TTS pour TutoBuilder Vision.

PRINCIPE D'ERREUR :
    generer() ne retourne PLUS jamais None silencieusement.
    Elle lève des exceptions typées que tasks.py capture et affiche
    proprement dans le toast frontend.

    TTSErrorCleAPI      → clé manquante, invalide, expirée
    TTSErrorReseau      → timeout, connexion refusée, DNS
    TTSErrorAPI         → réponse inattendue du serveur (4xx/5xx)
    TTSErrorConversion  → ffmpeg a échoué sur le fichier audio

Providers disponibles :
    ├── ElevenLabsProvider  — Cloud, qualité premium, multilingue
    └── CartesiaProvider    — Cloud, ultra-rapide (Sonic-2)
"""

import os
import subprocess
from abc import ABC, abstractmethod
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
#  EXCEPTIONS TYPÉES — messages humains, directement affichables dans le toast
# ═══════════════════════════════════════════════════════════════════════════════

class TTSError(Exception):
    """Classe de base — toutes les erreurs TTS héritent de là."""
    pass

class TTSErrorCleAPI(TTSError):
    """Clé API absente, invalide ou expirée."""
    pass

class TTSErrorReseau(TTSError):
    """Problème réseau : timeout, connexion refusée, DNS."""
    pass

class TTSErrorAPI(TTSError):
    """Le serveur a répondu, mais avec une erreur (4xx/5xx)."""
    pass

class TTSErrorConversion(TTSError):
    """La conversion audio (MP3→WAV ou fix header) a échoué."""
    pass


# ═══════════════════════════════════════════════════════════════════════════════
#  UTILITAIRES AUDIO
# ═══════════════════════════════════════════════════════════════════════════════

def get_wav_duration_ms(wav_path: str) -> float:
    """
    Lit la durée réelle d'un WAV.
    Corrige le bug Cartesia : getnframes() = INT32_MAX (2147483647).
    """
    import wave
    try:
        file_size = os.path.getsize(wav_path)
        with wave.open(wav_path, "rb") as wf:
            sr        = wf.getframerate()
            channels  = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            nframes   = wf.getnframes()
        if nframes >= 2_000_000_000:
            data_bytes  = max(0, file_size - 44)
            real_frames = data_bytes // (sampwidth * channels)
            return max(400.0, (real_frames / sr) * 1000.0)
        return max(400.0, (nframes / sr) * 1000.0)
    except Exception:
        try:
            return max(400.0, (os.path.getsize(wav_path) - 44) / (22050 * 2) * 1000.0)
        except Exception:
            return 3000.0


def fix_wav_header(wav_path: str) -> str:
    """Réécrit le header WAV via ffmpeg (corrige le bug Cartesia)."""
    fixed = wav_path + "_fixed.wav"
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path,
         "-ar", "22050", "-ac", "1", "-sample_fmt", "s16", fixed],
        capture_output=True, text=True,
    )
    if r.returncode == 0 and os.path.exists(fixed):
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
                langue: str = "fr") -> str:
        """
        Génère un fichier WAV et retourne son chemin absolu.
        Lève une exception TTSError* si quelque chose se passe mal.
        Ne retourne JAMAIS None.
        """
        ...


# ═══════════════════════════════════════════════════════════════════════════════
#  ELEVENLABS
# ═══════════════════════════════════════════════════════════════════════════════

class ElevenLabsProvider(TTSProvider):

    VOIX_CATALOGUE = {
        "narrateur_pro": {
            "id":    "onwK4e9ZLuTAKqWW03F9",
            "label": "Narrateur Pro — Clair, professionnel",
            "settings": {"stability": 0.50, "similarity_boost": 0.80, "style": 0.30},
        },
        "narratrice_pro": {
            "id":    "XB0fDUnXU5powFXDhCwa",
            "label": "Narratrice Pro — Douce, narrative",
            "settings": {"stability": 0.45, "similarity_boost": 0.80, "style": 0.35},
        },
        "expert": {
            "id":    "N2lVS1w4EtoT3dr4eOWO",
            "label": "Expert — Grave, autorité",
            "settings": {"stability": 0.55, "similarity_boost": 0.75, "style": 0.25},
        },
        "experte": {
            "id":    "XrExE9yKIg1WjnnlVkGX",
            "label": "Experte — Confiante, expressive",
            "settings": {"stability": 0.50, "similarity_boost": 0.78, "style": 0.30},
        },
        "guide": {
            "id":    "TX3LPaxmHKxFdv7VOQHJ",
            "label": "Guide — Chaleureux, accessible",
            "settings": {"stability": 0.40, "similarity_boost": 0.80, "style": 0.40},
        },
        "pedagogique": {
            "id":    "cgSgspJ2msm6clMCkdW9",
            "label": "Pédagogique — Claire, rassurante",
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

    def _get_voice_config(self, voix: str) -> tuple:
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
                langue: str = "fr") -> str:
        import requests

        # ── Vérification clé avant même d'appeler l'API ──────────────────
        if not self.api_key:
            raise TTSErrorCleAPI(
                "Clé API ElevenLabs manquante. "
                "Ajoutez ELEVENLABS_API_KEY dans votre fichier .env et redémarrez le serveur."
            )
        if not self.api_key.startswith("sk_"):
            raise TTSErrorCleAPI(
                "Clé API ElevenLabs invalide (doit commencer par 'sk_'). "
                "Vérifiez votre clé sur elevenlabs.io → Profile → API Keys."
            )
        if not texte or not texte.strip():
            raise ValueError("Le texte à synthétiser est vide.")

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

        except requests.Timeout:
            raise TTSErrorReseau(
                "ElevenLabs ne répond pas (timeout 60s). "
                "Ça arrive parfois — réessayez dans quelques instants."
            )
        except requests.ConnectionError:
            raise TTSErrorReseau(
                "Impossible de joindre ElevenLabs. "
                "Vérifiez votre connexion internet."
            )
        except Exception as e:
            raise TTSErrorReseau(f"Erreur réseau inattendue : {e}")

        # ── Traitement des codes HTTP ────────────────────────────────────
        if r.status_code == 401:
            raise TTSErrorCleAPI(
                "Clé API ElevenLabs refusée (erreur 401). "
                "La clé est peut-être expirée ou révoquée. "
                "Vérifiez sur elevenlabs.io → Profile → API Keys."
            )
        if r.status_code == 403:
            raise TTSErrorCleAPI(
                "Accès refusé par ElevenLabs (erreur 403). "
                "Votre abonnement ne couvre peut-être pas cette fonctionnalité."
            )
        if r.status_code == 422:
            raise TTSErrorAPI(
                f"Voix '{voice_id}' introuvable sur ElevenLabs (erreur 422). "
                "L'identifiant de voix est peut-être obsolète."
            )
        if r.status_code == 429:
            raise TTSErrorAPI(
                "Trop de requêtes envoyées à ElevenLabs (erreur 429). "
                "Attendez quelques secondes et relancez."
            )
        if r.status_code >= 500:
            raise TTSErrorAPI(
                f"ElevenLabs rencontre une panne serveur (erreur {r.status_code}). "
                "Ça arrive — réessayez dans quelques instants. "
                "Statut en temps réel : status.elevenlabs.io"
            )
        if r.status_code != 200:
            # Extraire le message d'erreur JSON si possible
            try:
                detail = r.json().get("detail", {})
                msg    = detail.get("message", r.text[:200]) if isinstance(detail, dict) else str(detail)
            except Exception:
                msg = r.text[:200]
            raise TTSErrorAPI(
                f"ElevenLabs a répondu avec une erreur inattendue ({r.status_code}) : {msg}"
            )

        # ── Écriture et conversion ────────────────────────────────────────
        try:
            with open(mp3_path, "wb") as f:
                f.write(r.content)
        except Exception as e:
            raise TTSErrorConversion(f"Impossible d'écrire le fichier MP3 : {e}")

        if not self._mp3_to_wav(mp3_path, wav_path):
            raise TTSErrorConversion(
                "La conversion MP3 → WAV a échoué. "
                "Vérifiez que ffmpeg est installé (commande : ffmpeg -version)."
            )

        try:
            os.remove(mp3_path)
        except Exception:
            pass

        dur = get_wav_duration_ms(wav_path)
        print(f"✅ ElevenLabs → {filename} ({dur:.0f}ms)")
        return wav_path


# ═══════════════════════════════════════════════════════════════════════════════
#  CARTESIA
# ═══════════════════════════════════════════════════════════════════════════════

class CartesiaProvider(TTSProvider):

    VOIX_CATALOGUE = {
        "narrateur_pro":  {"id": "a0e99841-438c-4a64-b679-ae501e7d6091"},
        "narratrice_pro": {"id": "79a125e8-cd45-4c13-8a67-188112f4dd22"},
        "expert":         {"id": "5619d38c-cf51-4d8e-9575-48f61a280413"},
        "experte":        {"id": "b7d50908-b17c-442d-ad8d-810c63997ed9"},
        "guide":          {"id": "41534e16-2966-4c6b-9670-111411def906"},
        "pedagogique":    {"id": "694f9389-aac1-45b6-b726-9d9369183238"},
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
                langue: str = "fr") -> str:
        import requests

        if not self.api_key:
            raise TTSErrorCleAPI(
                "Clé API Cartesia manquante. "
                "Ajoutez CARTESIA_API_KEY dans votre fichier .env et redémarrez le serveur."
            )
        if not texte or not texte.strip():
            raise ValueError("Le texte à synthétiser est vide.")

        cfg       = self.VOIX_CATALOGUE.get(voix, self.VOIX_CATALOGUE["narrateur_pro"])
        voice_id  = cfg["id"]
        lang_code = self.LANGUE_CODES.get((langue or "fr").lower()[:2], "fr")
        wav_path  = os.path.join(output_dir, filename or f"ca_{voix[:12]}.wav")

        payload = {
            "model_id":   "sonic-2",
            "transcript": texte.strip(),
            "voice":      {"mode": "id", "id": voice_id},
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

        except requests.Timeout:
            raise TTSErrorReseau(
                "Cartesia ne répond pas (timeout 60s). "
                "Ça arrive — réessayez dans quelques instants."
            )
        except requests.ConnectionError:
            raise TTSErrorReseau(
                "Impossible de joindre Cartesia. "
                "Vérifiez votre connexion internet."
            )
        except Exception as e:
            raise TTSErrorReseau(f"Erreur réseau inattendue : {e}")

        if r.status_code == 401:
            raise TTSErrorCleAPI(
                "Clé API Cartesia refusée (erreur 401). "
                "Vérifiez votre clé sur play.cartesia.ai → API Keys."
            )
        if r.status_code == 429:
            raise TTSErrorAPI(
                "Trop de requêtes envoyées à Cartesia (erreur 429). "
                "Attendez quelques secondes et relancez."
            )
        if r.status_code >= 500:
            raise TTSErrorAPI(
                f"Cartesia rencontre une panne serveur (erreur {r.status_code}). "
                "Ça arrive — réessayez dans quelques instants."
            )
        if r.status_code != 200:
            try:
                msg = r.json().get("message", r.text[:200])
            except Exception:
                msg = r.text[:200]
            raise TTSErrorAPI(
                f"Cartesia a répondu avec une erreur ({r.status_code}) : {msg}"
            )

        try:
            with open(wav_path, "wb") as f:
                f.write(r.content)
        except Exception as e:
            raise TTSErrorConversion(f"Impossible d'écrire le fichier WAV : {e}")

        if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000:
            raise TTSErrorConversion(
                "Cartesia a renvoyé un fichier audio vide ou corrompu. "
                "Réessayez — ça arrive lors de pics de charge."
            )

        fix_wav_header(wav_path)
        dur = get_wav_duration_ms(wav_path)
        print(f"✅ Cartesia → {filename} ({dur:.0f}ms)")
        return wav_path


# ═══════════════════════════════════════════════════════════════════════════════
#  FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

class TTSProviderFactory:

    _REGISTRY = {
        "elevenlabs": ElevenLabsProvider,
        "cartesia":   CartesiaProvider,
    }

    @classmethod
    def create(cls, nom: str, **kwargs) -> TTSProvider:
        klass = cls._REGISTRY.get(nom.lower())
        if klass is None:
            raise TTSError(
                f"Provider TTS inconnu : '{nom}'. "
                f"Disponibles : {list(cls._REGISTRY)}"
            )
        return klass(**kwargs)

    @classmethod
    def lister(cls) -> list:
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