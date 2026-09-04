from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import Lock
from typing import Any

from ..api.dto import TranscriptionOptions
from ..constants import WHISPER_MODEL

logger = logging.getLogger(__name__)


class TranscriptionCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EngineResult:
    transcript: list[dict[str, Any]]
    duration_seconds: float | None
    detected_language: str | None
    language_probability: float | None


class FasterWhisperEngine:
    def __init__(
        self,
        model_cache: Path,
        *,
        model_factory: Callable[..., Any] | None = None,
    ):
        self.model_cache = model_cache
        self._model_factory = model_factory
        self._model: Any = None
        self._model_lock = Lock()
        self._device = "unknown"
        self._compute_type = "unknown"

    @property
    def engine_version(self) -> str:
        try:
            return version("faster-whisper")
        except PackageNotFoundError:  # pragma: no cover - only source-only test environments
            return "unknown"

    @property
    def device_description(self) -> str:
        return f"{self._device}/{self._compute_type}"

    def _default_factory(self, model_name: str, **kwargs: Any) -> Any:
        try:
            import ctranslate2
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depends on optional runtime packages
            raise RuntimeError("faster-whisper is not installed") from exc

        cuda_available = ctranslate2.get_cuda_device_count() > 0
        if cuda_available:
            self._device = "cuda"
            self._compute_type = "float16"
            try:
                return WhisperModel(model_name, device="cuda", compute_type="float16", **kwargs)
            except (OSError, RuntimeError):
                logger.warning("CUDA model initialization failed; falling back to CPU/int8", exc_info=True)
        self._device = "cpu"
        self._compute_type = "int8"
        return WhisperModel(model_name, device="cpu", compute_type="int8", **kwargs)

    @staticmethod
    def _looks_like_cuda_runtime_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(marker in message for marker in ("cuda", "cublas", "cudnn", ".dll", "out of memory"))

    def _fallback_to_cpu(self) -> bool:
        if self._model_factory is not None or self._device != "cuda":
            return False
        from faster_whisper import WhisperModel  # imported lazily with the model runtime

        with self._model_lock:
            if self._device != "cuda":
                return True
            logger.warning("CUDA inference failed; restarting this transcription on CPU/int8")
            self._model = WhisperModel(
                WHISPER_MODEL,
                device="cpu",
                compute_type="int8",
                download_root=str(self.model_cache),
            )
            self._device = "cpu"
            self._compute_type = "int8"
        return True

    def warmup(self) -> None:
        if self._model is not None:
            return
        with self._model_lock:
            if self._model is not None:
                return
            self.model_cache.mkdir(parents=True, exist_ok=True)
            factory = self._model_factory or self._default_factory
            self._model = factory(WHISPER_MODEL, download_root=str(self.model_cache))
            if self._model_factory is not None:
                self._device = "injected"
                self._compute_type = "injected"

    def transcribe(
        self,
        media_path: Path,
        options: TranscriptionOptions,
        *,
        should_continue: Callable[[], bool],
    ) -> EngineResult:
        self.warmup()
        try:
            return self._transcribe_once(media_path, options, should_continue=should_continue)
        except TranscriptionCancelled:
            raise
        except (OSError, RuntimeError) as exc:
            if not self._looks_like_cuda_runtime_error(exc) or not self._fallback_to_cpu():
                raise
            if not should_continue():
                raise TranscriptionCancelled("rendering window closed") from exc
            return self._transcribe_once(media_path, options, should_continue=should_continue)

    def _transcribe_once(
        self,
        media_path: Path,
        options: TranscriptionOptions,
        *,
        should_continue: Callable[[], bool],
    ) -> EngineResult:
        segments, info = self._model.transcribe(
            str(media_path),
            language=options.language,
            beam_size=options.beam_size,
            vad_filter=options.vad_filter,
            word_timestamps=options.word_timestamps,
            initial_prompt=options.initial_prompt,
        )

        transcript: list[dict[str, Any]] = []
        iterator = iter(segments)
        while True:
            if not should_continue():
                raise TranscriptionCancelled("rendering window closed")
            try:
                segment = next(iterator)
            except StopIteration:
                break
            # A segment may take time to compute; discard it when the boundary passed meanwhile.
            if not should_continue():
                raise TranscriptionCancelled("rendering window closed")
            text = str(segment.text).strip()
            if not text:
                continue
            words: list[dict[str, Any]] = []
            for word in segment.words or []:
                if word.start is None or word.end is None:
                    continue
                normalized_word = str(word.word).strip()
                if not normalized_word:
                    continue
                words.append(
                    {
                        "start": float(word.start),
                        "end": float(word.end),
                        "word": normalized_word,
                        "probability": float(word.probability) if word.probability is not None else None,
                    }
                )
            transcript.append(
                {
                    "id": len(transcript),
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": text,
                    "words": words,
                }
            )

        duration = getattr(info, "duration", None)
        probability = getattr(info, "language_probability", None)
        language = getattr(info, "language", None)
        return EngineResult(
            transcript=transcript,
            duration_seconds=float(duration) if duration is not None else None,
            detected_language=str(language) if language is not None else None,
            language_probability=float(probability) if probability is not None else None,
        )
