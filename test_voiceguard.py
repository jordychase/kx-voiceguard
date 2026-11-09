import base64
import hashlib
import io
import json
import secrets
import time
import wave
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import numpy as np
import pytest
from cryptography.fernet import InvalidToken
from fastapi.testclient import TestClient

from app import create_app
from inference import AudioProblem, decode_wav
from settings import Actor, Settings


def uid():
    return str(uuid4())


def wav(seconds=1, stereo=False, rate=16000):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2 if stereo else 1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate) * (2 if stereo else 1))
    return out.getvalue()


class FakeEngine:
    # Control-flow fixture only. Not evidence of biometric accuracy.
    model_id = "control-test-v1"
    calls = 0
    callback = None
    vector = np.array([1, 0, 0], dtype=np.float32)

    def compute(self, samples, rate, operation):
        self.calls += 1
        if self.callback:
            self.callback()
        return self.vector.copy(), {"fixture": True}


@pytest.fixture
def env(tmp_path):
    tokens = {
        "admin": secrets.token_urlsafe(48),
        "operator": secrets.token_urlsafe(48),
        "other": secrets.token_urlsafe(48),
    }
    tenant, other = uid(), uid()
    actors = {
        hashlib.sha256(token.encode()).hexdigest(): Actor(
            other if role == "other" else tenant,
            "admin" if role != "operator" else "operator",
            role,
        )
        for role, token in tokens.items()
    }
    settings = Settings(str(tmp_path / "test.sqlite"), secrets.token_bytes(32), actors, True)
    engine = FakeEngine()
    app = create_app(settings, engine)
    headers = {role: {"Authorization": "Bearer " + token} for role, token in tokens.items()}
    with TestClient(app) as client:
        yield client, app, settings, engine, headers


def setup(env, states=("FL",), search=True, calibrated=True, actor="admin"):
    client, app, settings, engine, headers = env
    campaign = uid()
    h = headers[actor]
    assert (
        client.put(
            "/v1/settings/tenant",
            json={"enabled": True, "review_reference": "test-review"},
            headers=h,
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/v1/settings/campaigns/" + campaign,
            json={"enabled": True, "review_reference": "test-review"},
            headers=h,
        ).status_code
        == 200
    )
    for state in states:
        p = {
            "enabled": True,
            "version": "test-v1",
            "review_reference": "test-only",
            "valid_until": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
            "retention_days": 30,
            "allow_search": search,
        }
        if calibrated:
            p.update(
                candidate_threshold=0.8,
                calibration_reference="synthetic-control-not-validation",
                calibration_model_id=engine.model_id,
            )
        assert client.put("/v1/settings/states/" + state, json=p, headers=h).status_code == 200
    return campaign


def consent(
    env,
    campaign,
    subject=None,
    states=("FL",),
    scopes=("enroll", "verify", "search"),
    actor="admin",
):
    client, app, settings, engine, headers = env
    subject = subject or uid()
    data = {
        "subject_id": subject,
        "campaign_id": campaign,
        "states": list(states),
        "scopes": list(scopes),
        "signed_release_reference": "test-receipt",
        "notice_version": "test-notice",
        "policy_versions": {s: "test-v1" for s in states},
        "signed_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        "purpose": "fraud_prevention",
        "biometric_notice_acknowledged": True,
        "written_release_attested": True,
    }
    r = client.post("/v1/consents", json=data, headers=headers[actor])
    assert r.status_code == 201, r.text
    return subject, r.json()["consent_id"]


def payload(campaign, subject, consent_id, operation="enroll", **kw):
    return {
        "request_id": uid(),
        "subject_id": subject,
        "campaign_id": campaign,
        "consent_id": consent_id,
        "operation": operation,
        "purpose": "fraud_prevention",
        "investigation": "impersonation",
        "non_litigation_use": True,
        "single_speaker": True,
        "jurisdictions": {
            "caller_location": "FL",
            "caller_residence": "FL",
            "agent_location": "FL",
            "location_confirmed": True,
        },
        "audio_b64": base64.b64encode(wav()).decode(),
        **kw,
    }


def post(env, body, actor="operator"):
    return env[0].post("/v1/analyze", json=body, headers=env[4][actor])


def ready(env, **kw):
    campaign = setup(env, **kw)
    subject, cid = consent(env, campaign)
    return payload(campaign, subject, cid)


def test_default_disabled_and_no_inference(env):
    client, app, settings, engine, headers = env
    settings.enabled = False
    p = payload(uid(), uid(), uid())
    p["audio_b64"] = "garbage"
    result = post(env, p).json()
    assert result["reason"] == "globally_disabled"
    assert engine.calls == 0
    assert client.get("/healthz").json()["processing_enabled"] is False


def test_tenant_campaign_and_state_disabled(env):
    client, app, settings, engine, h = env
    p = payload(uid(), uid(), uid())
    assert post(env, p).json()["reason"] == "tenant_disabled"
    client.put(
        "/v1/settings/tenant",
        json={"enabled": True, "review_reference": "test"},
        headers=h["admin"],
    )
    assert post(env, p).json()["reason"] == "campaign_disabled"
    client.put(
        "/v1/settings/campaigns/" + p["campaign_id"],
        json={"enabled": True, "review_reference": "test"},
        headers=h["admin"],
    )
    assert post(env, p).json()["reason"] == "state_disabled"
    assert engine.calls == 0


def test_no_consent_no_processing(env):
    p = ready(env)
    p["consent_id"] = uid()
    assert post(env, p).json()["reason"] == "consent_missing_revoked_or_expired"
    assert env[3].calls == 0


def test_every_jurisdiction_and_unknown(env):
    p = ready(env)
    p["jurisdictions"]["caller_residence"] = "IL"
    assert post(env, p).json()["reason"] == "state_disabled"
    p["jurisdictions"]["caller_residence"] = None
    assert post(env, p).json()["reason"] == "jurisdiction_unknown"
    assert env[3].calls == 0


def test_consent_scope_and_policy_mismatch(env):
    campaign = setup(env, states=("FL", "IL"))
    subject, cid = consent(env, campaign, scopes=("enroll",))
    p = payload(campaign, subject, cid, "search")
    assert post(env, p).json()["reason"] == "consent_scope_mismatch"
    p["operation"] = "enroll"
    p["jurisdictions"]["caller_residence"] = "IL"
    assert post(env, p).json()["reason"] == "consent_policy_mismatch"


def test_review_only_repeated_voice_not_fraud(env):
    p = ready(env)
    r = post(env, p).json()
    assert r["status"] == "enrolled_for_review"
    p["operation"], p["target_subject_id"] = "verify", p["subject_id"]
    r = post(env, p).json()
    assert r["status"] == "possible_match"
    assert r["decision"] == "review" and r["automatic_action"] == "none"
    assert r["fraud_determination"] == "not_made"
    assert r["identity_determination"] == "not_made"
    assert r["liveness"]["status"] == "not_evaluated"


def test_no_match_does_not_mean_new_person(env):
    p = ready(env)
    post(env, p)
    p.update(operation="verify", target_subject_id=p["subject_id"])
    env[3].vector = np.array([0, 1, 0], dtype=np.float32)
    r = post(env, p).json()
    assert r["status"] == "no_confident_match"
    assert r["new_person_determination"] == "not_made"


def test_cross_tenant_lookup_and_encryption(env):
    p = ready(env)
    post(env, p)
    p.update(operation="verify", target_subject_id=p["subject_id"])
    assert post(env, p, "other").json()["reason"] == "tenant_disabled"
    # Even deliberate copying of IDs into another tenant's configured context cannot reuse consent.
    other_campaign = setup(env, actor="other")
    p["campaign_id"] = other_campaign
    assert post(env, p, "other").json()["reason"] == "consent_missing_revoked_or_expired"
    store = env[1].state.store
    with store.tx() as c:
        row = c.execute("SELECT * FROM embedding").fetchone()
    with pytest.raises(InvalidToken):
        store.cipher(uid()).decrypt(row["vector"])
    assert np.array([1, 0, 0], dtype="<f4").tobytes() not in row["vector"]


def test_same_tenant_search_and_candidate_permission(env):
    p = ready(env)
    post(env, p)
    subject, cid = consent(env, p["campaign_id"])
    q = payload(p["campaign_id"], subject, cid, "search")
    assert post(env, q).json()["candidates"][0]["subject_id"] == p["subject_id"]
    env[0].delete("/v1/consents/" + p["consent_id"], headers=env[4]["admin"])
    assert post(env, q).json()["status"] == "no_confident_match"


def test_search_explicitly_disabled(env):
    p = ready(env, search=False)
    p["operation"] = "search"
    assert post(env, p).json()["reason"] == "search_disabled"
    assert env[3].calls == 0


def test_missing_calibration_never_emits_match(env):
    p = ready(env, calibrated=False)
    post(env, p)
    p.update(operation="verify", target_subject_id=p["subject_id"])
    assert post(env, p).json()["status"] == "calibration_required"


def test_revocation_during_inference_prevents_save(env):
    p = ready(env)
    store = env[1].state.store

    def revoke_during_compute():
        with store.tx() as c:
            c.execute("UPDATE consent SET revoked=1 WHERE id=?", (p["consent_id"],))

    env[3].callback = revoke_during_compute
    assert post(env, p).json()["reason"] == "consent_missing_revoked_or_expired"
    with store.tx() as c:
        assert c.execute("SELECT count(*) FROM embedding").fetchone()[0] == 0


def test_kill_switch_during_inference_prevents_result(env):
    p = ready(env)
    env[3].callback = lambda: setattr(env[2], "enabled", False)
    assert post(env, p).json()["reason"] == "globally_disabled"


def test_changed_policy_during_inference_requires_retry(env):
    p = ready(env)
    store = env[1].state.store

    def change():
        with store.tx() as c:
            x = store.config(c, next(iter(env[2].actors.values())).tenant, "state:FL")
        x["retention_days"] = 1
        store.set_config(next(iter(env[2].actors.values())), "state:FL", x)

    env[3].callback = change
    assert post(env, p).json()["reason"] == "policy_changed_retry_with_current_consent"


def test_expiration_and_purge_when_disabled(env):
    p = ready(env)
    post(env, p)
    env[2].enabled = False
    with env[1].state.store.tx() as c:
        c.execute("UPDATE consent SET expires=?", (time.time() - 1,))
    assert env[1].state.store.purge() == 1
    with env[1].state.store.tx() as c:
        assert c.execute("SELECT count(*) FROM embedding").fetchone()[0] == 0


def test_operator_cannot_enable_or_register_consent(env):
    client, _, _, _, headers = env
    r = client.put(
        "/v1/settings/tenant",
        json={"enabled": True, "review_reference": "test"},
        headers=headers["operator"],
    )
    assert r.status_code == 403
    assert client.get("/v1/settings").status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"purpose": "litigator_screening"},
        {"mode": "enforce"},
        {"non_litigation_use": False},
        {"single_speaker": False},
        {"tenant_id": uid()},
        {"audio_uri": "http://localhost/"},
    ],
)
def test_reject_non_fraud_and_unsafe_contract(env, change):
    p = ready(env)
    p.update(change)
    r = post(env, p)
    assert r.status_code == 422
    assert "audio_b64" not in r.text
    assert env[3].calls == 0


def test_audio_and_body_limits(env):
    with pytest.raises(AudioProblem, match="caller_channel_required"):
        decode_wav(wav(stereo=True), None)
    with pytest.raises(AudioProblem, match="duration"):
        decode_wav(wav(seconds=61), None)
    r = env[0].post(
        "/v1/analyze",
        content=b"x" * 5_700_001,
        headers={**env[4]["operator"], "Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_stream_requires_gate_before_audio(env):
    env[2].enabled = False
    p = payload(uid(), uid(), uid())
    p.pop("audio_b64")
    with env[0].websocket_connect("/v1/stream", headers=env[4]["operator"]) as ws:
        ws.send_json(p)
        assert ws.receive_json()["reason"] == "globally_disabled"
    assert env[3].calls == 0


def test_stream_memory_window_review(env):
    p = ready(env)
    p.pop("audio_b64")
    with env[0].websocket_connect("/v1/stream", headers=env[4]["operator"]) as ws:
        ws.send_json(p)
        assert ws.receive_json()["status"] == "ready"
        ws.send_bytes(b"\x00\x00" * 160)
        ws.send_text("finish")
        r = ws.receive_json()
        assert r["status"] == "enrolled_for_review" and r["automatic_action"] == "none"


def test_wrong_key_refuses_existing_database(env):
    from store import Store

    settings = env[2]
    wrong = Settings(settings.db_path, secrets.token_bytes(32), settings.actors)
    with pytest.raises(ValueError, match="encryption key mismatch"):
        Store(wrong)


def test_candidate_policy_disabled_does_not_match(env):
    p = ready(env)
    post(env, p)
    # Candidate enrolled under FL+IL; disabling IL invalidates it even for a later FL probe.
    store = env[1].state.store
    with store.tx() as c:
        row = c.execute("SELECT body FROM consent WHERE id=?", (p["consent_id"],)).fetchone()
        data = json.loads(row[0])
        data["states"].append("IL")
        data["policy_versions"]["IL"] = "test-v1"
        c.execute("UPDATE consent SET body=? WHERE id=?", (json.dumps(data), p["consent_id"]))
    subject, cid = consent(env, p["campaign_id"])
    q = payload(p["campaign_id"], subject, cid, "search")
    assert post(env, q).json()["status"] == "no_confident_match"


def test_rate_limit_per_tenant(env):
    p = ready(env)
    for _ in range(10):
        assert post(env, p).status_code == 200
    assert post(env, p).status_code == 429


def test_audit_does_not_contain_audio_or_vectors(env):
    p = ready(env)
    post(env, p)
    r = env[0].get("/v1/audit", headers=env[4]["admin"])
    assert r.status_code == 200
    assert "audio_b64" not in r.text and p["subject_id"] not in r.text
    assert "vector" not in r.text


def test_retention_reduction_purges_old_vectors_while_disabled(env):
    p = ready(env)
    post(env, p)
    store = env[1].state.store
    identity = next(iter(env[2].actors.values()))
    with store.tx() as c:
        c.execute("UPDATE embedding SET created=?", (time.time() - 2 * 86400,))
        config = store.config(c, identity.tenant, "state:FL")
    config["retention_days"] = 1
    store.set_config(identity, "state:FL", config)
    env[2].enabled = False
    assert store.purge() == 1
