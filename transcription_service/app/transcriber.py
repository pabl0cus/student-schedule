from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from .config import Settings


@dataclass(slots=True)
class TranscriptionResult:
    transcript: list[dict[str, Any]]
    duration_seconds: float | None
    detected_language: str | None
    language_probability: float | None


class Transcriber(Protocol):
    def transcribe(self, media_path: Path, *, initial_prompt: str | None = None) -> TranscriptionResult: ...


class FasterWhisperTranscriber:
    """Lazily loads faster-whisper so API startup does not allocate VRAM."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model: Any = None
        self._model_lock = Lock()

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model

        with self._model_lock:
            if self._model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:  # pragma: no cover - depends on optional runtime package
                    raise RuntimeError(
                        "faster-whisper is not installed; install transcription_service/requirements.txt"
                    ) from exc

                self._model = WhisperModel(
                    self.settings.whisper_model,
                    device=self.settings.whisper_device,
                    compute_type=self.settings.whisper_compute_type,
                )
        return self._model

    def warmup(self) -> None:
        """Download and initialize the configured model without transcribing media."""

        self._get_model()

    def transcribe(self, media_path: Path, *, initial_prompt: str | None = None) -> TranscriptionResult:
        model = self._get_model()
        segments, info = model.transcribe(
            str(media_path),
            language="uk",
            beam_size=5,
            vad_filter=True,
            word_timestamps=True,
            initial_prompt=initial_prompt,
        )

        transcript: list[dict[str, Any]] = []
        for index, segment in enumerate(segments):
            words = []
            for word in segment.words or []:
                if word.start is None or word.end is None:
                    continue
                words.append(
                    {
                        "start": float(word.start),
                        "end": float(word.end),
                        "word": word.word.strip(),
                        "probability": float(word.probability) if word.probability is not None else None,
                    }
                )

            transcript.append(
                {
                    "id": index,
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": segment.text.strip(),
                    "words": words,
                }
            )

        duration = getattr(info, "duration", None)
        language_probability = getattr(info, "language_probability", None)
        return TranscriptionResult(
            transcript=transcript,
            duration_seconds=float(duration) if duration is not None else None,
            detected_language=getattr(info, "language", None),
            language_probability=float(language_probability) if language_probability is not None else None,
        )
