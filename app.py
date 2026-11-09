"""Authenticated API and bounded live PCM windows; no persistent raw recording path."""

import asyncio
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from policy import Denied, state_policies, validate_consent_policy
from schema import STATES, AudioRequest, Consent, Context, StatePolicy, Switch
from service import Service
from settings import Settings
from store import Store

MAX_BODY = 5_700_000


class BodyLimit:
    """Bound all HTTP JSON in memory before Pydantic; never use multipart spool files."""

    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path.startswith("/v1/"):
            headers = dict(scope.get("headers", []))
            actor = self.settings.actor(headers.get(b"authorization", b"").decode("latin1"))
            if actor is None:
                return await JSONResponse({"error": "authentication_required"}, status_code=401)(
                    scope, receive, send
                )
            if path == "/v1/analyze" and not self.settings.enabled:
                return await JSONResponse(Service.result("not_processed", "globally_disabled"))(
                    scope, receive, send
                )
        chunks, length = [], 0
        started = time.monotonic()
        while True:
            try:
                msg = await asyncio.wait_for(receive(), timeout=10)
            except TimeoutError:
                return await JSONResponse({"error": "body_timeout"}, status_code=408)(
                    scope, receive, send
                )
            if time.monotonic() - started > 20:
                return await JSONResponse({"error": "body_timeout"}, status_code=408)(
                    scope, receive, send
                )
            if msg["type"] == "http.disconnect":
                return
            length += len(msg.get("body", b""))
            if length > MAX_BODY:
                return await JSONResponse({"error": "request_too_large"}, status_code=413)(
                    scope, receive, send
                )
            chunks.append(msg.get("body", b""))
            if not msg.get("more_body"):
                break
        sent = False

        async def bounded_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)


class Limits:
    def __init__(self):
        self.entries = {}
        self.lock = threading.Lock()
        self.streams = {}

    def check(self, actor, bucket="request", limit=60):
        now = time.monotonic()
        key = (actor.tenant, bucket)
        with self.lock:
            # Configured tenants only, bounded entries; all keys for a tenant share quota.
            count, until = self.entries.get(key, (0, now + 60))
            if now >= until:
                count, until = 0, now + 60
            if count >= limit:
                raise HTTPException(429, "rate_limited")
            self.entries[key] = count + 1, until

    def stream_enter(self, actor):
        with self.lock:
            if self.streams.get(actor.tenant, 0) >= 2:
                return False
            self.streams[actor.tenant] = self.streams.get(actor.tenant, 0) + 1
            return True

    def stream_leave(self, actor):
        with self.lock:
            self.streams[actor.tenant] -= 1


def create_app(settings=None, engine=None):
    settings = settings or Settings.from_env()
    store = Store(settings)
    service = Service(settings, store, engine)
    limits = Limits()

    @asynccontextmanager
    async def lifespan(app):
        async def purge_loop():
            while True:
                await run_in_threadpool(store.purge)
                await asyncio.sleep(60)

        task = asyncio.create_task(purge_loop())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            store.close()

    app = FastAPI(
        title="KX VoiceGuard", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.add_middleware(BodyLimit, settings=settings)
    app.state.store, app.state.service, app.state.limits = store, service, limits

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI's default validation errors can echo the entire audio or consent body.
        return JSONResponse(
            {
                "error": "invalid_request",
                "fields": [{"location": e["loc"], "type": e["type"]} for e in exc.errors()],
            },
            status_code=422,
        )

    def actor(request: Request):
        identity = settings.actor(request.headers.get("authorization"))
        if not identity:
            raise HTTPException(401, "authentication_required")
        limits.check(identity)
        return identity

    def admin(identity=Depends(actor)):
        if identity.role != "admin":
            raise HTTPException(403, "admin_required")
        return identity

    @app.get("/healthz")
    def health():
        with store.tx() as c:
            c.execute("SELECT 1")
        return {
            "status": "ok",
            "build_revision": os.getenv("VOICEGUARD_SOURCE_REVISION", "local"),
            "processing_enabled": settings.enabled,
            "mode": "review_only",
            "purpose": "fraud_prevention",
            "automatic_action": "none",
        }

    @app.get("/v1/settings")
    def get_settings(identity=Depends(admin)):
        with store.tx() as c:
            rows = c.execute(
                "SELECT key,body FROM config WHERE tenant=?", (identity.tenant,)
            ).fetchall()
        return {
            "global_enabled": settings.enabled,
            "mode": "review_only",
            "defaults": {
                "tenant_enabled": False,
                "campaign_enabled": False,
                "state_enabled": False,
                "consent_required": True,
                "allow_search": False,
            },
            "configured": {r["key"]: json.loads(r["body"]) for r in rows},
        }

    @app.put("/v1/settings/tenant")
    def tenant_switch(body: Switch, identity=Depends(admin)):
        store.set_config(identity, "tenant", body.model_dump(mode="json"))
        return {"saved": True, "effective_requires_global_flag": True, "mode": "review_only"}

    @app.put("/v1/settings/campaigns/{campaign}")
    def campaign_switch(campaign: UUID, body: Switch, identity=Depends(admin)):
        store.set_config(identity, "campaign:" + str(campaign), body.model_dump(mode="json"))
        return {"saved": True, "mode": "review_only"}

    @app.put("/v1/settings/states/{state}")
    def set_state(state: str, body: StatePolicy, identity=Depends(admin)):
        if state not in STATES:
            raise HTTPException(422, "invalid_state")
        now = datetime.now(timezone.utc)
        if body.enabled and (body.valid_until <= now or (body.valid_until - now).days > 90):
            raise HTTPException(422, "policy_must_expire_within_90_days")
        calibration = (
            body.candidate_threshold,
            body.calibration_reference,
            body.calibration_model_id,
        )
        if any(v is not None for v in calibration) and not all(v is not None for v in calibration):
            raise HTTPException(422, "threshold_calibration_and_model_required_together")
        if (
            body.calibration_model_id is not None
            and body.calibration_model_id != service.engine.model_id
        ):
            raise HTTPException(422, "calibration_model_mismatch")
        store.set_config(identity, "state:" + state, body.model_dump(mode="json"))
        return {"saved": True, "mode": "review_only", "consent_required": True}

    @app.post("/v1/consents", status_code=201)
    def add_consent(body: Consent, identity=Depends(admin)):
        now = time.time()
        if not (
            body.signed_at.timestamp() <= now < body.expires_at.timestamp() <= now + 90 * 86400
            and body.signed_at < body.expires_at
        ):
            raise HTTPException(422, "invalid_consent_time_window")
        data = body.model_dump(mode="json")
        id = str(uuid4())
        with store.tx() as c:
            try:
                policies = state_policies(store, c, identity.tenant, set(body.states), now)
                validate_consent_policy(data, policies)
            except Denied as ex:
                raise HTTPException(409, ex.reason) from None
            c.execute(
                "INSERT INTO consent(tenant,id,subject,campaign,body,expires) VALUES(?,?,?,?,?,?)",
                (
                    identity.tenant,
                    id,
                    str(body.subject_id),
                    str(body.campaign_id),
                    json.dumps(data),
                    body.expires_at.timestamp(),
                ),
            )
            store.audit(c, identity, "consent_registered", "external_written_release_attested")
        return {
            "consent_id": id,
            "receipt_validation": "caller_attestation",
            "signature_independently_verified": False,
        }

    @app.delete("/v1/consents/{consent_id}")
    def revoke(consent_id: UUID, identity=Depends(admin)):
        with store.tx() as c:
            c.execute(
                "UPDATE consent SET revoked=1 WHERE tenant=? AND id=?",
                (identity.tenant, str(consent_id)),
            )
            count = c.execute(
                "DELETE FROM embedding WHERE tenant=? AND consent=?",
                (identity.tenant, str(consent_id)),
            ).rowcount
            store.audit(c, identity, "consent_revoked", "vectors_deleted")
        store.purge()
        return {
            "use_revoked": True,
            "live_vectors_deleted": count,
            "backup_erasure": "operator_managed",
        }

    @app.delete("/v1/subjects/{subject_id}")
    def delete_subject(subject_id: UUID, identity=Depends(admin)):
        with store.tx() as c:
            count = c.execute(
                "DELETE FROM embedding WHERE tenant=? AND subject=?",
                (identity.tenant, str(subject_id)),
            ).rowcount
            c.execute(
                "DELETE FROM consent WHERE tenant=? AND subject=?",
                (identity.tenant, str(subject_id)),
            )
            store.audit(c, identity, "subject_deleted", "live_records_deleted")
        store.purge()
        return {"live_vectors_deleted": count, "backup_erasure": "operator_managed"}

    @app.post("/v1/maintenance/purge")
    def purge(identity=Depends(admin)):
        # Purge only the authenticated tenant via revocation APIs; shared worker purges expired records.
        store.purge()
        return {"expired_records_purged": True}

    @app.get("/v1/audit")
    def audit(identity=Depends(admin)):
        with store.tx() as c:
            rows = c.execute(
                "SELECT id,actor,event,reason,at FROM audit WHERE tenant=? "
                "ORDER BY at DESC LIMIT 100",
                (identity.tenant,),
            ).fetchall()
        return {"events": [dict(row) for row in rows]}

    @app.post("/v1/analyze")
    def analyze(body: AudioRequest, identity=Depends(actor)):
        limits.check(identity, "analysis", 10)
        return service.from_request(identity, body)

    @app.websocket("/v1/stream")
    async def stream(ws: WebSocket):
        # Auth header only; no credentials in URLs. Separate caller-only PCM16LE, 16kHz mono.
        identity = settings.actor(ws.headers.get("authorization"))
        if not identity:
            await ws.close(code=1008)
            return
        try:
            limits.check(identity, "analysis", 10)
        except HTTPException:
            await ws.close(code=1008)
            return
        if not limits.stream_enter(identity):
            await ws.close(code=1013)
            return
        audio = bytearray()
        try:
            await ws.accept()
            initial = await asyncio.wait_for(ws.receive_text(), timeout=10)
            if len(initial) > 16384:
                await ws.close(code=1009)
                return
            context = Context.model_validate_json(initial)
            gate = await run_in_threadpool(service.preflight, identity, context)
            if isinstance(gate, dict):
                await ws.send_json(gate)
                await ws.close()
                return
            if context.caller_channel is not None:
                await ws.send_json(
                    service.result("not_processed", "stream_requires_mono_caller_audio")
                )
                await ws.close()
                return
            await ws.send_json(
                {"status": "ready", "format": "pcm16le_16000_mono", "max_seconds": 60}
            )
            started = time.monotonic()
            while time.monotonic() - started < 70:
                msg = await asyncio.wait_for(ws.receive(), timeout=15)
                if msg["type"] == "websocket.disconnect":
                    return
                if msg.get("text") == "finish":
                    output = await run_in_threadpool(
                        service.analyze, identity, context, None, bytes(audio)
                    )
                    await ws.send_json(output)
                    await ws.close()
                    return
                chunk = msg.get("bytes")
                if chunk is None or len(chunk) > 65536 or len(chunk) % 2:
                    await ws.close(code=1008)
                    return
                # Recheck kill switches/revocation before retaining every incoming frame.
                gate = await run_in_threadpool(service.preflight, identity, context)
                if isinstance(gate, dict):
                    await ws.send_json(gate)
                    await ws.close()
                    return
                if len(audio) + len(chunk) > 60 * 16000 * 2:
                    await ws.close(code=1009)
                    return
                audio.extend(chunk)
            await ws.close(code=1008)
        except (ValueError, asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
            try:
                await ws.close(code=1008)
            except RuntimeError:
                pass
        finally:
            audio[:] = b"\x00" * len(audio)
            audio.clear()
            limits.stream_leave(identity)

    return app
