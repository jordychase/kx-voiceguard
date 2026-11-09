"""Generate project-local staging credentials without displaying them. Never commit .env."""

import base64
import hashlib
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4

path = Path(".env")
if path.exists():
    raise SystemExit(".env exists; refusing to replace keys")
tenant = str(uuid4())
admin, operator = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
auth = [
    {"tenant_id": tenant, "role": role, "sha256": hashlib.sha256(key.encode()).hexdigest()}
    for role, key in [("admin", admin), ("operator", operator)]
]
values = {
    "VOICEGUARD_ENABLED": "false",
    "VOICEGUARD_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "VOICEGUARD_AUTH_KEYS": json.dumps(auth, separators=(",", ":")),
    "STAGING_ADMIN_KEY": admin,
    "STAGING_OPERATOR_KEY": operator,
    "STAGING_TENANT_ID": tenant,
}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    for key, value in values.items():
        f.write(key + "=" + value + "\n")
print("Generated .env (0600), disabled by default. Credentials were not printed.")
