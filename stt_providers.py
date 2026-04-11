"""
stt_providers.py — Provider STT : Faster-Whisper uniquement.
"""

import os
import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


def extraire_audio_wav(video_path: str, output_wav: str = None) -> str | None:
    if not output_wav:
        output_wav = str(Path(video_path).with_suffix(".wav"))
    Path(output_wav).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        output_wav,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not os.path.exists(output_wav):
        logger.error(f"ffmpeg extraction échouée : {result.stderr[-300:]}")
        return None
    return output_wav


class STTProvider(ABC):
    nom: str = "base"
    @abstractmethod
    def est_disponible(self) -> bool: ...
    @abstractmethod
    def transcrire(self, wav_path: str, langue: str = "fr") -> list[dict]: ...


class FasterWhisperProvider(STTProvider):
    nom = "Faster-Whisper"

    def __init__(self, model_size: str = "medium", **kwargs):
        self.model_size = model_size
        self._model = None

    def est_disponible(self) -> bool:
        try:
            import faster_whisper  # noqa
            return True
        except ImportError:
            return False

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
            logger.info(f"Chargement modèle Faster-Whisper '{self.model_size}'...")
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            return self._model
        except Exception as e:
            logger.error(f"Impossible de charger '{self.model_size}' : {e}")
            if self.model_size != "small":
                self.model_size = "small"
                self._model = None
                return self._load_model()
            return None

    def transcrire(self, wav_path: str, langue: str = "fr") -> list[dict]:
        model = self._load_model()
        if model is None:
            raise RuntimeError("Faster-Whisper non disponible.")
        lang_code = (langue or "fr").lower()[:2]
        segments_iter, info = model.transcribe(
            wav_path, language=lang_code, beam_size=5,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 300},
        )
        results = []
        for i, seg in enumerate(segments_iter):
            text = (seg.text or "").strip()
            if text:
                results.append({
                    "index": i,
                    "start_ms": int(seg.start * 1000),
                    "end_ms": int(seg.end * 1000),
                    "text": text,
                })
        logger.info(f"Faster-Whisper : {len(results)} segments, langue={info.language}")
        # Découper automatiquement les segments trop longs
        MAX_CHARS = 120
        final_results = []
        for seg in results:
            text = seg["text"].strip()
            if len(text) <= MAX_CHARS:
                final_results.append(seg)
                continue
            
            # Découper aux fins de phrase
            import re
            sentences = re.split(r'(?<=[.!?])\s+', text)
            chunks, current = [], ""
            for sent in sentences:
                test = (current + " " + sent).strip() if current else sent
                if len(test) <= MAX_CHARS:
                    current = test
                else:
                    if current:
                        chunks.append(current)
                    current = sent
            if current:
                chunks.append(current)
            
            # Répartir les timecodes proportionnellement
            dur = seg["end_ms"] - seg["start_ms"]
            total = sum(len(c) for c in chunks) or 1
            cursor = seg["start_ms"]
            for j, chunk in enumerate(chunks):
                ratio  = len(chunk) / total
                sub_dur = max(200, int(dur * ratio))
                sub_end = seg["end_ms"] if j == len(chunks)-1 else cursor + sub_dur
                final_results.append({
                    "index":    len(final_results),
                    "start_ms": cursor,
                    "end_ms":   sub_end,
                    "text":     chunk,
                })
                cursor = sub_end

        return final_results  # ← remplacer l'ancien "return results"


class STTProviderFactory:
    @classmethod
    def create(cls, engine: str, **kwargs) -> STTProvider:
        # Accepte tous les alias possibles
        aliases = {
            "faster_whisper": FasterWhisperProvider,
            "faster-whisper": FasterWhisperProvider,
            "fasterwhisper":  FasterWhisperProvider,
            "whisper":        FasterWhisperProvider,
            "vosk":           FasterWhisperProvider,  # fallback
        }
        klass = aliases.get(engine, FasterWhisperProvider)
        return klass(**kwargs)

    @classmethod
    def lister(cls) -> list[dict]:
        p = FasterWhisperProvider()
        return [{"id": "faster_whisper", "nom": p.nom, "disponible": p.est_disponible()}]