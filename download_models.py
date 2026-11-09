"""Only downloads fixed upstream assets and verifies the committed SHA-256s."""

import hashlib
import urllib.request
from pathlib import Path

from inference import ASSETS

for name, (url, digest) in ASSETS.items():
    target = Path(name)
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
        print(f"{name}: verified")
        continue
    temporary = target.with_suffix(".partial")
    with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
        temporary.unlink()
        raise SystemExit(f"{name}: upstream checksum changed; refusing unreviewed model")
    temporary.replace(target)
    print(f"{name}: downloaded and verified")
