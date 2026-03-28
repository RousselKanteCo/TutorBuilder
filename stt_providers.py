"""
stt_providers.py — Système de providers STT (Speech-to-Text) interchangeables pour Monument V8.

Architecture :
    STTProvider (classe abstraite)
    ├── WhisperProvider   — local, OpenAI, haute qualité, GPU recommandé
    └── VoskProvider      — local, léger, fonctionne bien sur CPU, hors-ligne total

Usage :
    from stt_providers import STTProviderFactory

    provider = STTProviderFactory.create("vosk", model_lang="fr")
    segments = provider.transcrire("audio.wav")
"""

import os
import json
import wave
import subprocess
import numpy as np
from abc import ABC, abstractmethod
from typing import Optional, Dict, List


TEMP_DIR = os.path.join(os.path.dirname(__file__), "temp")
os.makedirs(TEMP_DIR, exist_ok=True)


# ═══════════════════════════════════════════════
#  CLASSE ABSTRAITE (CONTRAT)
# ═══════════════════════════════════════════════

class STTProvider(ABC):
    """Interface commune pour tous les moteurs de transcription."""

    @property
    @abstractmethod
    def nom(self) -> str:
        """Nom affiché dans l'UI."""
        ...

    @property
    @abstractmethod
    def est_local(self) -> bool:
        """True si le provider fonctionne sans internet."""
        ...

    @abstractmethod
    def modeles_disponibles(self) -> List[Dict[str, str]]:
        """
        Retourne la liste des modèles disponibles.
        Chaque modèle = {"id": "...", "label": "Nom affiché", "taille": "1.5 Go"}
        """
        ...

    @abstractmethod
    def transcrire(self, audio_path: str, langue: str = "fr", **kwargs) -> List[dict]:
        """
        Transcrit un fichier audio.

        Args:
            audio_path: Chemin vers le fichier WAV (16kHz mono)
            langue:     Code langue ISO (fr, en, es...)
            **kwargs:   Paramètres spécifiques au provider

        Returns:
            Liste de segments : [{"start": ms, "end": ms, "text": "..."}, ...]
        """
        ...

    @abstractmethod
    def est_disponible(self) -> bool:
        """Vérifie si le provider est installé / configuré."""
        ...

    def info(self) -> str:
        """Description courte pour les logs."""
        mode = "🏠 Local" if self.est_local else "☁️ Cloud"
        return f"{self.nom} ({mode})"


# ═══════════════════════════════════════════════
#  UTILITAIRE : Extraction audio
# ═══════════════════════════════════════════════

def extraire_audio_wav(video_path: str, output_wav: Optional[str] = None,
                       sample_rate: int = 16000) -> Optional[str]:
    """
    Extrait la piste audio d'une vidéo en WAV mono via ffmpeg.

    Args:
        video_path:  Chemin de la vidéo source
        output_wav:  Chemin de sortie (auto-généré si None)
        sample_rate: Fréquence d'échantillonnage (16000 pour Whisper/Vosk)

    Returns:
        Chemin du fichier WAV, ou None si erreur.
    """
    if output_wav is None:
        output_wav = os.path.join(TEMP_DIR, "temp_wave.wav")

    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-ar", str(sample_rate), "-ac", "1", "-vn", output_wav],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"❌ ffmpeg erreur : {result.stderr[:300]}")
            return None
        return output_wav

    except FileNotFoundError:
        print("❌ ffmpeg introuvable. Installez-le : https://ffmpeg.org")
        return None


# ═══════════════════════════════════════════════
#  WHISPER (OPENAI — LOCAL)
# ═══════════════════════════════════════════════

class WhisperProvider(STTProvider):
    """
    Provider Whisper (OpenAI) — local, haute qualité.

    Installation : pip install openai-whisper
    Modèles : tiny → large (qualité croissante, taille croissante)
    GPU recommandé pour les modèles medium et large.
    """

    MODELES = {
        "tiny":   {"label": "⚡ Tiny (rapide, ~1 Go VRAM)",    "taille": "75 Mo"},
        "base":   {"label": "🔹 Base (bon compromis)",          "taille": "140 Mo"},
        "small":  {"label": "🔸 Small (bonne qualité)",         "taille": "460 Mo"},
        "medium": {"label": "🟠 Medium (très bon, GPU)",        "taille": "1.5 Go"},
        "large":  {"label": "🔴 Large (meilleur, GPU requis)",  "taille": "3 Go"},
    }

    def __init__(self, model_size: str = "base"):
        self.model_size = model_size
        self._model = None  # Lazy loading

    @property
    def nom(self) -> str:
        return "Whisper (OpenAI)"

    @property
    def est_local(self) -> bool:
        return True

    def modeles_disponibles(self) -> List[Dict[str, str]]:
        return [
            {"id": mid, "label": info["label"], "taille": info["taille"]}
            for mid, info in self.MODELES.items()
        ]

    def _charger_modele(self):
        """Charge le modèle Whisper (lazy, une seule fois)."""
        if self._model is None:
            import whisper
            print(f"🔄 Chargement Whisper '{self.model_size}'...")
            self._model = whisper.load_model(self.model_size)
            print("✅ Modèle Whisper chargé.")
        return self._model

    def transcrire(self, audio_path: str, langue: str = "fr", **kwargs) -> List[dict]:
        try:
            model = self._charger_modele()
        except ImportError:
            print("❌ Whisper non installé. Lancez : pip install openai-whisper")
            return []

        try:
            result = model.transcribe(
                audio_path,
                language=langue if langue != "auto" else None,
                **kwargs
            )
            segments = [
                {
                    "start": int(s["start"] * 1000),
                    "end":   int(s["end"] * 1000),
                    "text":  s["text"].strip(),
                }
                for s in result.get("segments", [])
            ]
            return segments

        except Exception as e:
            print(f"❌ Whisper transcription : {e}")
            return []

    def est_disponible(self) -> bool:
        try:
            import whisper
            return True
        except ImportError:
            return False


# ═══════════════════════════════════════════════
#  VOSK (LOCAL, LÉGER, HORS-LIGNE)
# ═══════════════════════════════════════════════

class VoskProvider(STTProvider):
    """
    Provider Vosk — léger, 100% hors-ligne, très bon sur CPU.

    Installation :
        pip install vosk
        Puis télécharger un modèle depuis https://alphacephei.com/vosk/models
        Exemple : vosk-model-fr-0.22 (~1.4 Go) ou vosk-model-small-fr-0.22 (~40 Mo)

    Le modèle doit être décompressé dans un dossier local.
    """

    # Modèles recommandés par langue (à télécharger manuellement)
    MODELES_RECOMMANDES = {
        "fr": {
            "small": {
                "label": "🇫🇷 Français léger (~40 Mo)",
                "url": "https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip",
                "dossier": "vosk-model-small-fr-0.22",
            },
            "large": {
                "label": "🇫🇷 Français complet (~1.4 Go)",
                "url": "https://alphacephei.com/vosk/models/vosk-model-fr-0.22.zip",
                "dossier": "vosk-model-fr-0.22",
            },
        },
        "en": {
            "small": {
                "label": "🇬🇧 Anglais léger (~40 Mo)",
                "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
                "dossier": "vosk-model-small-en-us-0.15",
            },
            "large": {
                "label": "🇬🇧 Anglais complet (~1.8 Go)",
                "url": "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip",
                "dossier": "vosk-model-en-us-0.22",
            },
        },
        "es": {
            "small": {
                "label": "🇪🇸 Espagnol léger (~40 Mo)",
                "url": "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip",
                "dossier": "vosk-model-small-es-0.42",
            },
        },
    }

    def __init__(self, model_path: Optional[str] = None, model_lang: str = "fr",
                 model_size: str = "small"):
        """
        Args:
            model_path: Chemin direct vers le dossier du modèle Vosk.
                        Si None, cherche automatiquement dans ./models/
            model_lang: Langue du modèle (fr, en, es)
            model_size: Taille du modèle (small, large)
        """
        self.model_lang = model_lang
        self.model_size = model_size
        self._model = None

        if model_path:
            self.model_path = model_path
        else:
            # Chemin par défaut : ./models/vosk-model-small-fr-0.22/
            info = self._get_model_info()
            if info:
                self.model_path = os.path.join(
                    os.path.dirname(__file__), "models", info["dossier"]
                )
            else:
                self.model_path = ""

    def _get_model_info(self) -> Optional[dict]:
        """Récupère les infos du modèle demandé."""
        lang_models = self.MODELES_RECOMMANDES.get(self.model_lang, {})
        return lang_models.get(self.model_size)

    @property
    def nom(self) -> str:
        return "Vosk"

    @property
    def est_local(self) -> bool:
        return True

    def modeles_disponibles(self) -> List[Dict[str, str]]:
        result = []
        for lang, sizes in self.MODELES_RECOMMANDES.items():
            for size, info in sizes.items():
                result.append({
                    "id": f"{lang}_{size}",
                    "label": info["label"],
                    "taille": "~40 Mo" if size == "small" else "~1.4 Go",
                    "url": info["url"],
                })
        return result

    def _charger_modele(self):
        """Charge le modèle Vosk (lazy)."""
        if self._model is None:
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)  # Réduire la verbosité

            if not os.path.isdir(self.model_path):
                info = self._get_model_info()
                msg = f"❌ Modèle Vosk introuvable : {self.model_path}\n"
                if info:
                    msg += f"   Téléchargez-le ici : {info['url']}\n"
                    msg += f"   Décompressez dans : {os.path.dirname(self.model_path)}/"
                print(msg)
                raise FileNotFoundError(msg)

            print(f"🔄 Chargement Vosk depuis '{self.model_path}'...")
            self._model = Model(self.model_path)
            print("✅ Modèle Vosk chargé.")
        return self._model

    def transcrire(self, audio_path: str, langue: str = "fr", **kwargs) -> List[dict]:
        try:
            model = self._charger_modele()
        except (ImportError, FileNotFoundError) as e:
            if "ImportError" in type(e).__name__:
                print("❌ Vosk non installé. Lancez : pip install vosk")
            return []

        try:
            from vosk import KaldiRecognizer

            wf = wave.open(audio_path, "rb")
            sample_rate = wf.getframerate()

            rec = KaldiRecognizer(model, sample_rate)
            rec.SetWords(True)  # Active les timestamps par mot

            segments = []
            buffer_text = ""
            seg_start = 0.0

            # Lecture par blocs de 4000 frames
            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break

                if rec.AcceptWaveform(data):
                    result = json.loads(rec.Result())
                    text = result.get("text", "").strip()
                    if text:
                        # Extraire les timestamps des mots
                        words = result.get("result", [])
                        if words:
                            start_ms = int(words[0]["start"] * 1000)
                            end_ms = int(words[-1]["end"] * 1000)
                        else:
                            start_ms = int(seg_start * 1000)
                            end_ms = start_ms + len(text) * 60  # Estimation

                        segments.append({
                            "start": start_ms,
                            "end": end_ms,
                            "text": text,
                        })
                        seg_start = end_ms / 1000.0

            # Dernier segment résiduel
            final = json.loads(rec.FinalResult())
            final_text = final.get("text", "").strip()
            if final_text:
                words = final.get("result", [])
                if words:
                    start_ms = int(words[0]["start"] * 1000)
                    end_ms = int(words[-1]["end"] * 1000)
                else:
                    start_ms = int(seg_start * 1000)
                    end_ms = start_ms + len(final_text) * 60
                segments.append({
                    "start": start_ms,
                    "end": end_ms,
                    "text": final_text,
                })

            wf.close()
            return segments

        except Exception as e:
            print(f"❌ Vosk transcription : {e}")
            return []

    def est_disponible(self) -> bool:
        try:
            import vosk
            return os.path.isdir(self.model_path)
        except ImportError:
            return False


# ═══════════════════════════════════════════════
#  FACTORY — Création simplifiée des providers
# ═══════════════════════════════════════════════

class STTProviderFactory:
    """
    Fabrique de providers STT.

    Usage :
        # Lister
        for name, info in STTProviderFactory.lister().items():
            print(f"{name}: {info}")

        # Créer
        provider = STTProviderFactory.create("vosk", model_lang="fr")
        segments = provider.transcrire("audio.wav")
    """

    _REGISTRY = {
        "whisper": WhisperProvider,
        "vosk":    VoskProvider,
    }

    @classmethod
    def create(cls, nom: str, **kwargs) -> Optional[STTProvider]:
        """
        Crée un provider par son nom.

        Args:
            nom:     "whisper" ou "vosk"
            **kwargs: Arguments passés au constructeur
                      (ex: model_size pour Whisper, model_lang pour Vosk)
        """
        provider_class = cls._REGISTRY.get(nom.lower())
        if provider_class is None:
            print(f"❌ Provider STT inconnu : '{nom}'. Disponibles : {list(cls._REGISTRY.keys())}")
            return None
        return provider_class(**kwargs)

    @classmethod
    def lister(cls) -> Dict[str, str]:
        """Retourne un dict {nom: description} des providers enregistrés."""
        result = {}
        for name, klass in cls._REGISTRY.items():
            try:
                instance = klass()
                status = "✅ Prêt" if instance.est_disponible() else "⚠️ Non installé/configuré"
                result[name] = f"{instance.info()} — {status}"
            except Exception:
                result[name] = f"{name} — ❌ Erreur"
        return result

    @classmethod
    def enregistrer(cls, nom: str, provider_class: type):
        """Enregistre un provider STT personnalisé."""
        if not issubclass(provider_class, STTProvider):
            raise TypeError(f"{provider_class} doit hériter de STTProvider")
        cls._REGISTRY[nom.lower()] = provider_class
        print(f"✅ Provider STT '{nom}' enregistré.")
