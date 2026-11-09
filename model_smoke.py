"""Docker build-time native runtime smoke; no person, microphone, or recording involved."""

from types import SimpleNamespace

import numpy as np

from inference import AudioProblem, Engine

engine = Engine(SimpleNamespace(speaker_path="speaker.onnx", vad_path="vad.onnx"))
engine._load()
assert engine.extractor.dim == 256
try:
    engine.compute(np.zeros(16000 * 8, dtype=np.float32), 16000, "verify")
except AudioProblem as error:
    assert str(error) == "insufficient_speech"
else:
    raise AssertionError("Silence must not pass the speech-quality gate")
print("Pinned model/runtime smoke passed: 256 dimensions; silence rejected.")
