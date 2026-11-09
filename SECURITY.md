# Security and data lifecycle

- API keys: generate 48 random bytes, use TLS and authorization headers, and store only their SHA-256
  digests server-side. Tenant and role come from that trusted configuration, never from JSON input.
- Admin keys can configure policy, attest consent and revoke/delete; operators can analyze only.
  Use separately controlled credentials and audit your consent service. Two roles are a pilot
  boundary, not a replacement for enterprise SSO, approval workflows or independent authorization.
- Every state starts disabled. The pilot has no statutory fraud exception bypass. An approved
  policy version expires after at most 90 days; consent must cover each applicable state/version.
- Consent checks bracket inference and result storage in database transactions. A concurrent
  revocation before result commit discards the result. Revocation after commit cannot retract a
  response already delivered; consumers must purge cached results and stop subsequent use.
- Embeddings use Fernet authenticated encryption with HKDF-derived per-tenant keys. The master key
  belongs in the secret manager. A database verifier prevents silent startup with the wrong key.
  Rotating this master key requires an explicit decrypt/re-encrypt migration, not replacing the env
  variable. API key rotation is independent. Model changes require re-enrollment/recalibration.
- No user-supplied remote URLs, file paths or model paths. Models load only from trusted operator
  configuration and their exact committed SHA-256s must match. No pickle/checkpoint loading.
- Audio is bounded and held in memory for each request/window, never written by this application.
  Python/native runtimes can retain copies; this is not a cryptographic RAM erasure guarantee. Turn
  off swap/core dumps and body logging at the host, proxy, APM and telephony layers. The upstream
  telephony vendor may separately record; configure its retention and consent independently.
- Encrypted vectors expire no later than consent expiry or the stricter state retention limit
  (default 30, maximum 90 days). Current policies/consents are rechecked before every comparison.
  Candidate policy retention reductions prevent further use immediately; physical expiry cleanup
  still follows the stored expiry unless the operator explicitly deletes the subject sooner.
- A one-minute sweeper operates while processing is disabled. Revocation immediately deletes linked
  live vectors and prevents further use; subject deletion removes live consent metadata too.
  SQLite secure_delete and WAL truncation bound remnants on the live database. Filesystem snapshots,
  provider backups and SSD blocks require separately configured retention/erasure. The API explicitly
  reports backup erasure as operator-managed and never claims deletion from immutable backups.
- Audit records expire after 90 days and contain actor key IDs, policy versions/snapshot hashes,
  request and consent UUIDs, event and outcome; no audio, vectors, scores, names or phone numbers.
  Consent metadata includes opaque external receipt references; proof remains in your consent store.
- SQLite is single-instance, one worker and one replica. Tenant keys isolate ciphertext, but this
  release does not use PostgreSQL RLS. Never mount the same SQLite file in multiple service replicas.
- Invalid inputs do not echo request bodies. Access logs are disabled in the Docker runtime. Enforce
  provider quotas/firewall limits and monitor aggregate error/rate/latency metrics without audio.
- The public MIT source contains no consent records, API keys, customer recordings or biometric
  databases. Report security issues privately through the repository owner's GitHub contact; do not
  publish keys, audio or sample identities in an issue.
