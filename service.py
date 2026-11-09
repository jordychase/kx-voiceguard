"""Review-only orchestration. A similarity score is neither identity proof nor fraud proof."""

import base64
import json
import threading
import time

import numpy as np
from cryptography.fernet import InvalidToken

from inference import AudioProblem, Engine, decode_wav
from policy import Denied, authorize, candidate_eligible
from schema import Context


class Service:
    def __init__(self, settings, store, engine=None):
        self.settings, self.store = settings, store
        self.engine = engine if engine is not None else Engine(settings)
        self.slots = threading.BoundedSemaphore(2)

    @staticmethod
    def result(status, reason=None, **fields):
        return {
            "status": status,
            "reason": reason,
            "mode": "review_only",
            "decision": "review",
            "automatic_action": "none",
            "fraud_determination": "not_made",
            "identity_determination": "not_made",
            "liveness": {"status": "not_evaluated"},
            **fields,
        }

    def preflight(self, actor, context):
        with self.store.tx() as c:
            try:
                return authorize(self.store, c, actor, context)
            except Denied as ex:
                self.store.audit(c, actor, "processing_denied", ex.reason)
                return self.result("not_processed", ex.reason)

    def analyze(self, actor, context, raw=None, pcm=None):
        first = self.preflight(actor, context)
        if isinstance(first, dict):
            return first
        if not self.slots.acquire(blocking=False):
            return self.result("unavailable", "busy_retry_later")
        try:
            if pcm is not None:
                if not pcm or len(pcm) % 2 or len(pcm) > 60 * 16000 * 2:
                    raise AudioProblem("invalid_stream_length")
                samples, rate = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768, 16000
            else:
                samples, rate = decode_wav(raw, context.caller_channel)
            started = time.monotonic()
            vector, quality = self.engine.compute(samples, rate, context.operation)
            elapsed = time.monotonic() - started
            with self.store.tx() as c:
                try:
                    policies, snapshot = authorize(self.store, c, actor, context)
                    if snapshot != first[1]:
                        raise Denied("policy_changed_retry_with_current_consent")
                    output = self._finish(c, actor, context, vector, policies)
                    self.store.audit(
                        c,
                        actor,
                        context.operation,
                        json.dumps(
                            {
                                "status": output["status"],
                                "request_id": str(context.request_id),
                                "policy_versions": {
                                    state: p["version"] for state, p in policies.items()
                                },
                                "policy_snapshot": snapshot,
                                "consent_id": str(context.consent_id),
                            }
                        ),
                    )
                except Denied as ex:
                    self.store.audit(c, actor, "processing_denied", ex.reason)
                    return self.result("not_processed", ex.reason)
            return {
                **output,
                "request_id": str(context.request_id),
                "model_id": self.engine.model_id,
                "quality": quality,
                "compute_seconds": round(elapsed, 4),
            }
        except AudioProblem as ex:
            return self.result("insufficient_quality", str(ex))
        except (RuntimeError, OSError, ValueError, InvalidToken):
            # Don't echo native errors, filesystem locations, vectors, or decoder inputs.
            return self.result("unavailable", "model_or_processing_unavailable")
        finally:
            self.slots.release()

    def _finish(self, c, actor, context, vector, policies):
        tenant, subject, campaign = actor.tenant, str(context.subject_id), str(context.campaign_id)
        now = time.time()
        if context.operation == "enroll":
            consent = c.execute(
                "SELECT expires FROM consent WHERE tenant=? AND id=?",
                (tenant, str(context.consent_id)),
            ).fetchone()
            expires = min(
                consent[0], now + min(p["retention_days"] for p in policies.values()) * 86400
            )
            ciphertext = self.store.cipher(tenant).encrypt(vector.astype("<f4").tobytes())
            c.execute(
                "INSERT INTO embedding VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(tenant,subject,campaign) "
                "DO UPDATE SET consent=excluded.consent, model=excluded.model, vector=excluded.vector, "
                "expires=excluded.expires, created=excluded.created",
                (
                    tenant,
                    subject,
                    campaign,
                    str(context.consent_id),
                    self.engine.model_id,
                    ciphertext,
                    expires,
                    now,
                ),
            )
            return self.result("enrolled_for_review", expires_at=expires)
        params = [tenant, campaign, self.engine.model_id, now]
        query = "SELECT * FROM embedding WHERE tenant=? AND campaign=? AND model=? AND expires>?"
        if context.operation == "verify":
            query += " AND subject=?"
            params.append(str(context.target_subject_id))
        else:
            query += " AND subject<>?"
            params.append(subject)
        # A pilot capacity limit never silently truncates search and calls it a no-match.
        rows = c.execute(query + " LIMIT ?", (*params, self.settings.max_candidates + 1)).fetchall()
        if len(rows) > self.settings.max_candidates:
            return self.result("unavailable", "candidate_capacity_exceeded")
        matches, inspected, uncalibrated = [], 0, False
        for row in rows:
            candidate_policies = candidate_eligible(
                self.store, c, actor, row, context.operation, now
            )
            if not candidate_policies:
                continue
            all_policies = [*policies.values(), *candidate_policies.values()]
            if any(
                now >= row["created"] + p["retention_days"] * 86400
                for p in candidate_policies.values()
            ):
                continue
            if any(
                p["candidate_threshold"] is None
                or not p["calibration_reference"]
                or p.get("calibration_model_id") != self.engine.model_id
                for p in all_policies
            ):
                uncalibrated = True
                continue
            if len({p["calibration_reference"] for p in all_policies}) != 1:
                uncalibrated = True
                continue
            stored = np.frombuffer(self.store.cipher(tenant).decrypt(row["vector"]), dtype="<f4")
            if stored.shape != vector.shape or not np.isfinite(stored).all():
                raise RuntimeError("stored_embedding_invalid")
            score = float(np.clip(np.dot(vector, stored), -1, 1))
            inspected += 1
            if score >= max(p["candidate_threshold"] for p in all_policies):
                matches.append({"subject_id": row["subject"], "similarity": round(score, 5)})
        if uncalibrated:
            return self.result("calibration_required", "local_evaluation_required", candidates=[])
        matches.sort(key=lambda item: item["similarity"], reverse=True)
        return self.result(
            "possible_match" if matches else "no_confident_match",
            candidates=matches[:5],
            eligible_compared=inspected,
            new_person_determination="not_made",
        )

    def from_request(self, actor, request):
        context = Context.model_validate(request.model_dump(exclude={"audio_b64"}))
        gate = self.preflight(actor, context)
        if isinstance(gate, dict):
            return gate
        try:
            raw = base64.b64decode(request.audio_b64, validate=True)
        except (ValueError, TypeError):
            return self.result("insufficient_quality", "invalid_base64")
        return self.analyze(actor, context, raw=raw)
