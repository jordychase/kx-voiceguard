# KX VoiceGuard

Open-source, self-hosted voice similarity for **human fraud review**. MIT-licensed application;
separately licensed, pinned pretrained models. This is a pilot API, not a fraud classifier.

**Off by default. Review only. No automatic blocking, suppression, identity linking or fraud labels.**
No connection to the KX litigator database, PACER, or litigation-history scrubber is installed.
Repeated callers and people who exercise legal rights are not thereby fraudulent.

## What works

- CPU speaker embeddings with WeSpeaker ResNet34-LM through sherpa-onnx; Silero speech detection.
- Enrollment, one-to-one comparison, optional same-tenant **and same-campaign** search.
- HTTP memory-only WAV windows or live WebSocket PCM windows; no server recording files.
- Independent global, tenant, campaign and state switches. Every switch starts off.
- Caller location, caller residence, agent location and any additional applicable states must be
  declared from reliable context. Unknown states fail closed. Phone area codes are insufficient.
- Every enabled state requires a versioned policy, approval reference and expiry. The pilot always
  requires advance biometric notice and an externally documented written release, including probe
  comparisons. It contains no automatic state-law exemptions.
- Consent scopes, state coverage, policy versions, revocation and expiry checked before inference
  and again before saving or returning results; candidate consent is checked too.
- Tenant identity comes from authenticated API keys. Tenant-derived encryption keys, bounded
  input/concurrency/rate limits, policy audit references and consent deletion.
- Missing calibration returns `calibration_required`; a threshold must cite its exact model ID.
  No score is a calibrated probability of identity or fraud.

## Run locally (Python 3.12)

```sh
uv sync --frozen
uv run python download_models.py
uv run python bootstrap.py
uv run --env-file .env uvicorn main:app --host 127.0.0.1 --port 8080 --no-access-log
uv run pytest -q
uv run ruff check .
uv run pip-audit --progress-spinner off
```

Bootstrap creates ignored `.env` with permissions 0600. It generates one tenant, separate admin
and operator keys, their server-side SHA-256 digests, and a random database encryption key.
Never commit, print or share `.env`. Only the `VOICEGUARD_*` values belong in the service's secret
manager. The raw staging keys are client credentials and are not needed by the server.

`VOICEGUARD_ENABLED=false` is the default even if omitted. The global flag is process configuration:
changing it requires a restart. Tenant/campaign/state kill switches are live database controls.
Keys should be rotated by updating the trusted authentication configuration and restarting.
Key rotation terminates with the old process; drain/close its active connections.
`VOICEGUARD_DB` defaults to `data/voiceguard.sqlite`; Docker uses the `/data` volume.

## API

All `/v1/*` requests need `Authorization: Bearer <key>`; keys never go in URLs.
`/healthz` and `/openapi.json` are public and contain no tenant records or secrets.

| Route | Role | Purpose |
| --- | --- | --- |
| `GET /v1/settings` | admin | Effective defaults and tenant configuration |
| `PUT /v1/settings/tenant` | admin | Tenant switch; `enabled`, `review_reference` |
| `PUT /v1/settings/campaigns/{uuid}` | admin | Campaign switch, same body |
| `PUT /v1/settings/states/{code}` | admin | Versioned state policy; see schema/OpenAPI |
| `POST /v1/consents` | admin | Register an external written-release attestation |
| `DELETE /v1/consents/{uuid}` | admin | Revoke use and delete dependent live embeddings |
| `DELETE /v1/subjects/{uuid}` | admin | Delete this tenant's subject and consents |
| `POST /v1/maintenance/purge` | admin | Trigger expiration cleanup; automatic every minute |
| `GET /v1/audit` | admin | Most recent 100 tenant audit events |
| `POST /v1/analyze` | operator/admin | Enroll, verify or search a memory-only WAV window |
| `WS /v1/stream` | operator/admin | Caller-only PCM16LE/16kHz/mono live window |

Subjects, campaigns, requests and consent IDs are opaque UUIDs. Do not substitute names, phone
numbers or emails. Keep external mappings in your own access-controlled system.

A state policy uses `enabled`, `mode: consent_required`, `version`, `review_reference`,
`valid_until` (no more than 90 days), `retention_days` (1–90, default 30), and `allow_search`
(default false). For comparisons, provide `candidate_threshold`, `calibration_reference`, and
`calibration_model_id` together, after an independently reviewed call-domain evaluation.
Reusing a different model's threshold is rejected. All relevant policies use the stricter threshold.

Consent registration is an **attestation interface**, not a signature-verification product. Your
consent system must present the approved notice before capture, obtain the actual written release,
verify authority and retain proof. The response explicitly reports that signatures were not
independently verified. Withdrawal must call the revocation API immediately. Do not substitute
continued conversation, generic call-recording notice, or an operator checkbox for a valid release.

See [INTEGRATION.md](INTEGRATION.md) for request examples and the live protocol,
[SECURITY.md](SECURITY.md) for the data lifecycle, and [ACCURACY.md](ACCURACY.md) for evaluation.

## Limits and deployment

This release is a **single-process pilot**, one replica, one persistent SQLite volume. Exact scans
are capped at 1,000 candidate embeddings per tenant/campaign. Exceeding the cap is `unavailable`,
never a clean result. No million-person scale or latency claim is made. For scale, replace this
store with tenant-partitioned PostgreSQL/pgvector, enforce RLS and test filtered-search recall.

Input: uncompressed PCM16 WAV, 8/16/48kHz, mono or explicit caller channel of stereo; max 60 seconds
and 4 MB decoded upload. Separate channels are never silently downmixed. Require caller-only
segments; diarization is not implemented. Minimum detected speech is 20 seconds for enrollment,
6 for comparisons. These conservative quality gates are not validated accuracy thresholds.
WebSocket processing begins after a window finishes, not continuously on each audio frame.

The Docker image downloads only checksum-pinned public models, drops privileges before API startup,
uses one worker, and stores only encrypted embeddings on `/data`. Mount a persistent volume and set
secrets before deploying. Do not expose Uvicorn without a TLS reverse proxy. The provided Railway
configuration uses Docker and `/healthz`. Deploy staging first and leave processing disabled.

**Not implemented:** liveness/deepfake detection, diarization, an identity proofing service, automatic
fraud decisions, a UI, an external review queue, cross-customer matching, or production integration.
Responses always mark liveness `not_evaluated`. Synthetic/replay investigations need independent
challenge/step-up evidence; similarity alone cannot distinguish a real speaker from a voice clone.

The MIT license permits reuse and modification. Fraud-only purpose restrictions are enforced by
this application's API contract, not by a non-open-source field-of-use license. Forks can modify
code; API attestations do not prove the caller's true purpose.
