"""Optional actual-model HTTP/live tests using an explicitly supplied synthetic WAV fixture."""

import base64
import os
from pathlib import Path

import pytest

from inference import MODEL_ID, Engine
from test_voiceguard import env, post, ready  # noqa: F401


@pytest.mark.skipif(
    not os.getenv("VOICEGUARD_SYNTHETIC_WAV"), reason="synthetic audio fixture not supplied"
)
def test_real_model_http_and_memory_only_stream(env):  # noqa: F811
    client, app, settings, fake, headers = env
    app.state.service.engine = Engine(settings)
    p = ready(env, calibrated=False)
    raw = Path(os.environ["VOICEGUARD_SYNTHETIC_WAV"]).read_bytes()
    p["audio_b64"] = base64.b64encode(raw).decode()
    result = post(env, p).json()
    assert result["status"] == "enrolled_for_review"
    assert result["model_id"] == MODEL_ID and result["quality"]["speech_seconds"] >= 20
    p.update(operation="verify", target_subject_id=p["subject_id"])
    assert post(env, p).json()["status"] == "calibration_required"
    p.pop("audio_b64")
    # WAV decoding in the test supplies caller PCM frames, as a telephony bridge would.
    import io
    import wave

    with wave.open(io.BytesIO(raw), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        pcm = w.readframes(w.getnframes())
    with client.websocket_connect("/v1/stream", headers=headers["operator"]) as ws:
        ws.send_json(p)
        assert ws.receive_json()["status"] == "ready"
        for i in range(0, len(pcm), 32000):
            ws.send_bytes(pcm[i : i + 32000])
        ws.send_text("finish")
        result = ws.receive_json()
        assert result["status"] == "calibration_required" and result["model_id"] == MODEL_ID
        assert result["automatic_action"] == "none"
