"""Speaker embeddings for an utterance (sherpa-onnx, a CAM++ model).

An utterance with too little speech in it gives no embedding: a short
"lights off" says little about who said it, and unknown beats wrong.
"""
from __future__ import annotations

import array
import math

RATE = 16000
MIN_SPEECH_S = 1.0
_FRAME = RATE // 50  # 20 ms
_SPEECH_RMS = 0.01   # of full scale


def samples(pcm: bytes, width: int = 2, channels: int = 1) -> list[float]:
    """Signed 16-bit little-endian PCM (the first channel) as floats in -1..1."""
    if width != 2:
        return []
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    return [x / 32768.0 for x in a[::max(1, channels)]]


def speech_seconds(signal: list[float], rate: int = RATE) -> float:
    frame = max(1, rate // 50)
    voiced = 0
    for i in range(0, len(signal) - frame + 1, frame):
        chunk = signal[i:i + frame]
        if math.sqrt(sum(x * x for x in chunk) / frame) >= _SPEECH_RMS:
            voiced += 1
    return voiced * frame / rate


class Embedder:
    def __init__(self, model: str, threads: int = 1) -> None:
        import sherpa_onnx

        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=model, num_threads=threads))

    def embed(self, signal: list[float], rate: int = RATE) -> list[float] | None:
        if speech_seconds(signal, rate) < MIN_SPEECH_S:
            return None
        stream = self._extractor.create_stream()
        stream.accept_waveform(rate, signal)
        stream.input_finished()
        if not self._extractor.is_ready(stream):
            return None
        return list(self._extractor.compute(stream))
