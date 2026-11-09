import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class Actor:
    tenant: str
    role: str
    key_id: str


@dataclass
class Settings:
    db_path: str
    master_key: bytes
    actors: dict[str, Actor]
    enabled: bool = False
    speaker_path: str = "speaker.onnx"
    vad_path: str = "vad.onnx"
    max_candidates: int = 1000

    @classmethod
    def from_env(cls):
        key = base64.urlsafe_b64decode(os.environ["VOICEGUARD_ENCRYPTION_KEY"])
        if len(key) != 32:
            raise ValueError("encryption key must be 32 random bytes, base64url encoded")
        actors = {}
        for entry in json.loads(os.environ["VOICEGUARD_AUTH_KEYS"]):
            digest = entry["sha256"]
            if len(digest) != 64 or any(x not in "0123456789abcdef" for x in digest):
                raise ValueError("invalid API key digest")
            if entry["role"] not in {"admin", "operator"}:
                raise ValueError("invalid role")
            if digest in actors:
                raise ValueError("duplicate API key digest")
            actors[digest] = Actor(str(UUID(entry["tenant_id"])), entry["role"], digest[:12])
        if not actors:
            raise ValueError("at least one API key is required")
        flag = os.getenv("VOICEGUARD_ENABLED", "false")
        if flag not in {"true", "false"}:
            raise ValueError("VOICEGUARD_ENABLED must be true or false")
        path = os.getenv("VOICEGUARD_DB", "data/voiceguard.sqlite")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        return cls(
            path,
            key,
            actors,
            flag == "true",
            os.getenv("VOICEGUARD_SPEAKER_MODEL", "speaker.onnx"),
            os.getenv("VOICEGUARD_VAD_MODEL", "vad.onnx"),
        )

    def actor(self, authorization: str | None) -> Actor | None:
        if not authorization or not authorization.startswith("Bearer "):
            return None
        token = authorization[7:]
        if len(token) < 32 or len(token) > 256:
            return None
        return self.actors.get(hashlib.sha256(token.encode()).hexdigest())
