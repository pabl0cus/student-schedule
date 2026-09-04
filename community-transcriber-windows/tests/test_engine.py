from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from student_schedule_worker.api.dto import TranscriptionOptions
from student_schedule_worker.engine.whisper import FasterWhisperEngine, TranscriptionCancelled

OPTIONS = TranscriptionOptions(
    model="large-v3",
    language="uk",
    beam_size=5,
    vad_filter=True,
    word_timestamps=True,
    initial_prompt="Лекція",
)


class FakeModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def transcribe(self, _path: str, **kwargs):
        self.calls.append(kwargs)
        word = SimpleNamespace(start=0.1, end=0.4, word=" привіт ", probability=0.9)
        segment = SimpleNamespace(start=0.0, end=1.0, text=" Привіт ", words=[word])
        info = SimpleNamespace(duration=1.0, language="uk", language_probability=0.99)
        return iter([segment]), info


def test_engine_maps_segments_and_forwards_required_options(tmp_path: Path) -> None:
    model = FakeModel()
    engine = FasterWhisperEngine(tmp_path / "models", model_factory=lambda *_args, **_kwargs: model)
    result = engine.transcribe(tmp_path / "audio.flac", OPTIONS, should_continue=lambda: True)
    assert result.transcript == [
        {
            "id": 0,
            "start": 0.0,
            "end": 1.0,
            "text": "Привіт",
            "words": [{"start": 0.1, "end": 0.4, "word": "привіт", "probability": 0.9}],
        }
    ]
    assert model.calls == [
        {
            "language": "uk",
            "beam_size": 5,
            "vad_filter": True,
            "word_timestamps": True,
            "initial_prompt": "Лекція",
        }
    ]


def test_engine_aborts_at_segment_boundary(tmp_path: Path) -> None:
    model = FakeModel()
    engine = FasterWhisperEngine(tmp_path / "models", model_factory=lambda *_args, **_kwargs: model)
    decisions = iter([True, False])
    with pytest.raises(TranscriptionCancelled):
        engine.transcribe(tmp_path / "audio.flac", OPTIONS, should_continue=lambda: next(decisions))


def test_cuda_runtime_failure_restarts_once_on_cpu(tmp_path: Path) -> None:
    good_model = FakeModel()

    class BrokenCudaModel:
        def transcribe(self, _path: str, **_kwargs):
            raise RuntimeError("Library cublas64_12.dll is not found")

    class RecoveringEngine(FasterWhisperEngine):
        def _fallback_to_cpu(self) -> bool:
            self._model = good_model
            self._device = "cpu"
            self._compute_type = "int8"
            return True

    engine = RecoveringEngine(tmp_path / "models")
    engine._model = BrokenCudaModel()
    engine._device = "cuda"
    result = engine.transcribe(tmp_path / "audio.flac", OPTIONS, should_continue=lambda: True)
    assert result.transcript[0]["text"] == "Привіт"
    assert engine.device_description == "cpu/int8"


def test_non_cuda_decoder_failure_is_not_retried(tmp_path: Path) -> None:
    class BrokenFileModel:
        def transcribe(self, _path: str, **_kwargs):
            raise RuntimeError("invalid media container")

    engine = FasterWhisperEngine(tmp_path / "models", model_factory=lambda *_args, **_kwargs: BrokenFileModel())
    with pytest.raises(RuntimeError, match="invalid media"):
        engine.transcribe(tmp_path / "bad-media", OPTIONS, should_continue=lambda: True)
