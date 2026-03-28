"""
tts_providers.py — Système de providers TTS interchangeables pour Monument V8.

Architecture :
    TTSProvider (classe abstraite)
    ├── ElevenLabsProvider   — API cloud, haute qualité, payant
    ├── CoquiTTSProvider     — local, gratuit, open source
    └── BarkProvider         — local, expressif (émotions, rires, pauses)

Usage :
    from tts_providers import TTSProviderFactory
    provider = TTSProviderFactory.create("coqui")
    path = provider.generer(texte="Bonjour", voix="default", output_dir="./temp")
"""

import os
from abc import ABC, abstractmethod
from typing import Optional, Dict, List


class TTSProvider(ABC):
    """Interface commune pour tous les moteurs de synthèse vocale."""

    @property
    @abstractmethod
    def nom(self) -> str:
        ...

    @property
    @abstractmethod
    def est_local(self) -> bool:
        ...

    @abstractmethod
    def voix_disponibles(self) -> List[Dict[str, str]]:
        ...

    @abstractmethod
    def generer(self, texte: str, voix: str, output_dir: str,
                filename: Optional[str] = None, **kwargs) -> Optional[str]:
        ...

    @abstractmethod
    def est_disponible(self) -> bool:
        ...

    def info(self) -> str:
        mode = "🏠 Local" if self.est_local else "☁️ Cloud"
        return f"{self.nom} ({mode})"


# ═══════════════════════════════════════════════
#  ELEVENLABS (CLOUD)
# ═══════════════════════════════════════════════

class ElevenLabsProvider(TTSProvider):

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    @property
    def nom(self) -> str:
        return "ElevenLabs"

    @property
    def est_local(self) -> bool:
        return False

    def voix_disponibles(self) -> List[Dict[str, str]]:
        return [
            {"id": "pNInz6obpgDQGcFmaJgB", "label": "👨‍🏫 Adam (Expert)", "langue": "multi"},
            {"id": "EXAVITQu4vr4PUuX88re", "label": "👱‍♂️ Charlie (Apprenti)", "langue": "multi"},
            {"id": "21m00Tcm4TlvDq8ikWAM", "label": "🎙️ Rachel (Narratrice)", "langue": "multi"},
        ]

    def generer(self, texte: str, voix: str, output_dir: str,
                filename: Optional[str] = None, **kwargs) -> Optional[str]:
        import requests

        if not self.api_key:
            print("❌ ElevenLabs : clé API manquante.")
            return None

        timeout = kwargs.get("timeout", 30)
        filename = filename or f"elevenlabs_{voix[:8]}.mp3"
        output = os.path.join(output_dir, filename)

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voix}"
        headers = {"xi-api-key": self.api_key, "Content-Type": "application/json"}
        payload = {"text": texte, "model_id": "eleven_multilingual_v2"}

        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if r.status_code == 200:
                with open(output, "wb") as f:
                    f.write(r.content)
                return output
            print(f"⚠️ ElevenLabs HTTP {r.status_code}: {r.text[:200]}")
            return None
        except requests.RequestException as e:
            print(f"❌ ElevenLabs réseau : {e}")
            return None

    def est_disponible(self) -> bool:
        return bool(self.api_key)


# ═══════════════════════════════════════════════
#  COQUI TTS (LOCAL)
# ═══════════════════════════════════════════════

class CoquiTTSProvider(TTSProvider):
    """
    Local, gratuit, open source.
    Installation : pip install TTS
    """

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
            {"id": "female", "label": "👩 Voix féminine", "langue": "fr"},
            {"id": "clone", "label": "🎭 Cloner une voix (.wav)", "langue": "multi"},
        ]

    def _charger_modele(self):
        if self._tts is None:
            from TTS.api import TTS
            print(f"🔄 Chargement Coqui : {self.model_name}")
            self._tts = TTS(model_name=self.model_name)
            print("✅ Modèle Coqui chargé.")
        return self._tts

    def generer(self, texte: str, voix: str, output_dir: str,
                filename: Optional[str] = None, **kwargs) -> Optional[str]:
        try:
            tts = self._charger_modele()
        except ImportError:
            print("❌ Coqui TTS non installé. Lancez : pip install TTS")
            return None

        filename = filename or f"coqui_{voix}.wav"
        output = os.path.join(output_dir, filename)
        langue = kwargs.get("langue", "fr")
        speaker_wav = kwargs.get("speaker_wav", None)

        try:
            if voix == "clone" and speaker_wav:
                tts.tts_to_file(text=texte, file_path=output,
                                speaker_wav=speaker_wav, language=langue)
            else:
                tts_kwargs = {"text": texte, "file_path": output}
                if hasattr(tts, 'languages') and tts.languages:
                    tts_kwargs["language"] = langue
                tts.tts_to_file(**tts_kwargs)

            return output if os.path.exists(output) else None
        except Exception as e:
            print(f"❌ Coqui TTS erreur : {e}")
            return None

    def est_disponible(self) -> bool:
        try:
            import TTS
            return True
        except ImportError:
            return False


# ═══════════════════════════════════════════════
#  BARK / SUNO (LOCAL, EXPRESSIF)
# ═══════════════════════════════════════════════

class BarkProvider(TTSProvider):
    """
    Local, très expressif : émotions, rires [laughs], pauses ..., musique.
    Installation : pip install git+https://github.com/suno-ai/bark.git scipy
    GPU recommandé (~4 Go VRAM).
    """

    VOIX_PRESETS = {
        "homme_fr":  "v2/fr_speaker_0",
        "femme_fr":  "v2/fr_speaker_1",
        "homme_en":  "v2/en_speaker_6",
        "femme_en":  "v2/en_speaker_9",
        "narrateur": "v2/en_speaker_3",
    }

    def __init__(self, use_gpu: bool = True, use_small: bool = False):
        self.use_gpu = use_gpu
        self.use_small = use_small

    @property
    def nom(self) -> str:
        return "Bark (Suno)"

    @property
    def est_local(self) -> bool:
        return True

    def voix_disponibles(self) -> List[Dict[str, str]]:
        return [
            {"id": "homme_fr",  "label": "🧑 Homme français",  "langue": "fr"},
            {"id": "femme_fr",  "label": "👩 Femme française",  "langue": "fr"},
            {"id": "homme_en",  "label": "👨 Homme anglais",    "langue": "en"},
            {"id": "femme_en",  "label": "👩 Femme anglaise",   "langue": "en"},
            {"id": "narrateur", "label": "🎙️ Narrateur (EN)",  "langue": "en"},
        ]

    def generer(self, texte: str, voix: str, output_dir: str,
                filename: Optional[str] = None, **kwargs) -> Optional[str]:
        try:
            import numpy as np
            from scipy.io.wavfile import write as write_wav

            os.environ["SUNO_USE_SMALL_MODELS"] = "1" if self.use_small else "0"
            if not self.use_gpu:
                os.environ["SUNO_OFFLOAD_CPU"] = "1"

            from bark import generate_audio, SAMPLE_RATE
            from bark.generation import preload_models
        except ImportError:
            print("❌ Bark non installé. Lancez :")
            print("   pip install git+https://github.com/suno-ai/bark.git scipy")
            return None

        filename = filename or f"bark_{voix}.wav"
        output = os.path.join(output_dir, filename)
        preset = self.VOIX_PRESETS.get(voix, "v2/fr_speaker_0")

        try:
            print(f"🔄 Bark : génération avec '{preset}'...")
            preload_models()
            audio_array = generate_audio(texte, history_prompt=preset)

            import numpy as np
            from scipy.io.wavfile import write as write_wav
            audio_array = np.clip(audio_array, -1.0, 1.0)
            write_wav(output, SAMPLE_RATE, (audio_array * 32767).astype(np.int16))

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
#  PIPER TTS (LOCAL, RAPIDE SUR CPU)
# ═══════════════════════════════════════════════

class Pyttsx3TTSProvider(TTSProvider):
    """
    Moteur vocal natif Windows (SAPI5).
    Aucune dépendance externe, fonctionne hors-ligne.
    Installation : pip install pyttsx3
    """

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
            return [{"id": v.id, "label": v.name, "langue": "fr"} for v in voices] or [{"id": "default", "label": "Voix par défaut", "langue": "fr"}]
        except Exception:
            return [{"id": "default", "label": "Voix par défaut", "langue": "fr"}]

    def generer(self, texte: str, voix: str, output_dir: str,
                filename: Optional[str] = None, **kwargs) -> Optional[str]:
        try:
            import pyttsx3
        except ImportError:
            print("❌ pyttsx3 non installé. Lancez : pip install pyttsx3")
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


class TTSProviderFactory:

    _REGISTRY = {
        "elevenlabs": ElevenLabsProvider,
        "coqui":      CoquiTTSProvider,
        "bark":       BarkProvider,
        "piper":      Pyttsx3TTSProvider,
        "pyttsx3":    Pyttsx3TTSProvider,
    }

    @classmethod
    def create(cls, nom: str, **kwargs) -> Optional[TTSProvider]:
        provider_class = cls._REGISTRY.get(nom.lower())
        if provider_class is None:
            print(f"❌ Provider TTS inconnu : '{nom}'. Disponibles : {list(cls._REGISTRY.keys())}")
            return None
        return provider_class(**kwargs)

    @classmethod
    def lister(cls) -> Dict[str, str]:
        result = {}
        for name, klass in cls._REGISTRY.items():
            try:
                instance = klass() if name != "elevenlabs" else klass(api_key="")
                status = "✅ Installé" if instance.est_disponible() else "⚠️ Non installé"
                result[name] = f"{instance.info()} — {status}"
            except Exception:
                result[name] = f"{name} — ❌ Erreur"
        return result

    @classmethod
    def enregistrer(cls, nom: str, provider_class: type):
        if not issubclass(provider_class, TTSProvider):
            raise TypeError(f"{provider_class} doit hériter de TTSProvider")
        cls._REGISTRY[nom.lower()] = provider_class