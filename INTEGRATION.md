# Integration and handoff

Keep VoiceGuard alongside the existing scrub endpoint as a separate optional `voice_fraud_review`
object. Never append its candidates to KX `reasons`, mark `blocked`, or invoke the existing
name/phone/address auto-link logic. The call flow must work without voice analysis when disabled,
unavailable, consent is declined, or location is unknown. A review signal is not an adverse action.
No KX integration is installed by this repository.

## HTTP request example

The UUIDs below are placeholders, not existing consent or subject records. Create a campaign and
state policy and register the external release first. At rest the service stores an encrypted
embedding, not this WAV or a transcript.

```json
{
  "request_id": "00000000-0000-4000-8000-000000000001",
  "subject_id": "00000000-0000-4000-8000-000000000002",
  "campaign_id": "00000000-0000-4000-8000-000000000003",
  "consent_id": "00000000-0000-4000-8000-000000000004",
  "operation": "verify",
  "target_subject_id": "00000000-0000-4000-8000-000000000005",
  "purpose": "fraud_prevention",
  "investigation": "impersonation",
  "non_litigation_use": true,
  "single_speaker": true,
  "jurisdictions": {
    "caller_location": "FL",
    "caller_residence": "FL",
    "agent_location": "FL",
    "additional_states": [],
    "location_confirmed": true
  },
  "caller_channel": 0,
  "audio_b64": "<base64 PCM16 WAV bytes>"
}
```

Operation `enroll` stores/replaces the subject's current embedding in this campaign. Do not retry
an enrollment after an uncertain response without checking your own call ledger: request UUIDs are
correlation IDs, not an idempotency guarantee. Comparison probes are never enrolled automatically.
`verify` needs a target UUID. `search` excludes the probe subject, needs explicit search policies and
consent on both sides, and searches only this tenant and campaign. Keep campaign IDs stable for
repeat-caller analysis. `no_confident_match` does not establish a new person.

Possible statuses: `not_processed`, `unavailable`, `insufficient_quality`, `enrolled_for_review`,
`calibration_required`, `possible_match`, `no_confident_match`. Every response uses `review_only`,
`automatic_action: none`, `fraud_determination: not_made`, and liveness `not_evaluated`.
Reject/match is deliberately not a true/false identity guarantee. HTTP authentication/validation
errors retain standard 401/403/422/429 codes; integrations must also handle timeouts and 5xx.

## Live audio without a durable recording

Connect to `wss://<host>/v1/stream` with an authorization header. Send the same metadata JSON without
`audio_b64` and without `caller_channel`. Wait for `status: ready` **before sending audio**. If
permission fails, the server responds and closes without accepting audio frames. Then send binary
PCM16LE/16kHz/mono caller-only frames (maximum 65,536 bytes per message, even byte count). Send the
text message `finish` to process the current window and receive one review result. The connection
closes afterward. Maximum window is 60 seconds; idle timeout 15 seconds; total capture wall time
70 seconds. Use multiple independently authorized windows if needed; enrollment should collect
20 seconds of detected caller speech first. Incoming frames and final processing recheck controls.

Caller and agent in a mixed channel cannot be separated by this release. Do not mark
`single_speaker: true` unless your upstream capture/segmentation has established it. Pseudonymous
matching still creates a potentially regulated voiceprint; no recording file is required for that.

## Configure without enabling capture

Prepare notices/releases, reliable jurisdiction collection, state review references, expiry and
retention. The state configuration is a record of your approval, not an independent legal opinion.
Register synthetic or properly consented pilot subjects in isolation. Require reviewed held-out
score evaluation with the pinned model before supplying a threshold. Keep search off unless needed.
Global, tenant and campaign switches remain separate. Disable any switch to stop processing; expiry
and deletion keep running. Revocation endpoints remain usable while the feature is off.

A Claude handoff can start at `schema.py` (contract), `policy.py` (all gates), `service.py` (review-only
orchestration) and `test_voiceguard.py` (security/control fixtures). Do not weaken existing gates to
make an integration test pass. Use an isolated test tenant and never import the litigation cache.
