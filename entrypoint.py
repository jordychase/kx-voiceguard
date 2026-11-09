"""Prepare the root-owned Railway mount, then drop privileges before loading secrets or API."""

import os

if os.geteuid() == 0:
    os.makedirs("/data", mode=0o700, exist_ok=True)
    os.chown("/data", 10001, 10001)
    os.chmod("/data", 0o700)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
os.umask(0o077)

import uvicorn  # noqa: E402

uvicorn.run(
    "main:app",
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8080")),
    workers=1,
    access_log=False,
    limit_concurrency=16,
    timeout_keep_alive=5,
    ws_max_size=65536,
    ws_max_queue=4,
)
