"""Single-instance SQLite pilot. Vectors are encrypted with distinct tenant-derived keys."""

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class Store:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.c = sqlite3.connect(settings.db_path, check_same_thread=False, isolation_level=None)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA secure_delete=ON")
        self.c.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS config(
          tenant TEXT, key TEXT, body TEXT NOT NULL, PRIMARY KEY(tenant,key));
        CREATE TABLE IF NOT EXISTS consent(
          tenant TEXT, id TEXT, subject TEXT, campaign TEXT, body TEXT NOT NULL,
          expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(tenant,id));
        CREATE TABLE IF NOT EXISTS embedding(
          tenant TEXT, subject TEXT, campaign TEXT, consent TEXT, model TEXT,
          vector BLOB NOT NULL, expires REAL NOT NULL, created REAL NOT NULL,
          PRIMARY KEY(tenant,subject,campaign));
        CREATE TABLE IF NOT EXISTS audit(
          id TEXT PRIMARY KEY, tenant TEXT, actor TEXT, event TEXT, reason TEXT, at REAL);
        CREATE INDEX IF NOT EXISTS embedding_scope ON embedding(tenant,campaign,model,expires);
        """)
        verifier = hmac.new(
            settings.master_key, b"voiceguard-database-key-v1", hashlib.sha256
        ).hexdigest()
        with self.tx() as c:
            existing = c.execute("SELECT value FROM meta WHERE key='key_verifier'").fetchone()
            if existing and not hmac.compare_digest(existing[0], verifier):
                raise ValueError("database encryption key mismatch; refusing startup")
            c.execute("INSERT OR IGNORE INTO meta VALUES('key_verifier',?)", (verifier,))
        if settings.db_path != ":memory:":
            os.chmod(settings.db_path, 0o600)

    @contextmanager
    def tx(self):
        with self.lock:
            self.c.execute("BEGIN IMMEDIATE")
            try:
                yield self.c
                self.c.execute("COMMIT")
            except BaseException:
                self.c.execute("ROLLBACK")
                raise

    def cipher(self, tenant):
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"voiceguard/tenant/v1/" + tenant.encode(),
        ).derive(self.settings.master_key)
        return Fernet(base64.urlsafe_b64encode(key))

    @staticmethod
    def config(c, tenant, key):
        row = c.execute(
            "SELECT body FROM config WHERE tenant=? AND key=?", (tenant, key)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    @staticmethod
    def audit(c, actor, event, reason):
        c.execute(
            "INSERT INTO audit VALUES(?,?,?,?,?,?)",
            (str(uuid4()), actor.tenant, actor.key_id, event, reason, time.time()),
        )

    def set_config(self, actor, key, body):
        with self.tx() as c:
            c.execute(
                "INSERT INTO config VALUES(?,?,?) ON CONFLICT(tenant,key) "
                "DO UPDATE SET body=excluded.body",
                (actor.tenant, key, json.dumps(body)),
            )
            self.audit(c, actor, "policy_changed", key)

    def purge(self):
        """Runs regardless of processing flags. Ciphertext deletion is not backup erasure."""
        now = time.time()
        with self.tx() as c:
            # A stricter policy shortens existing TTLs; relaxing it never extends them.
            rows = c.execute(
                "SELECT e.tenant,e.subject,e.campaign,e.created,e.expires,s.body "
                "FROM embedding e JOIN consent s ON s.tenant=e.tenant AND s.id=e.consent"
            ).fetchall()
            for row in rows:
                consent = json.loads(row["body"])
                days = [
                    self.config(c, row["tenant"], "state:" + state).get("retention_days", 90)
                    for state in consent["states"]
                ]
                expiry = min(row["expires"], row["created"] + min(days, default=90) * 86400)
                if expiry < row["expires"]:
                    c.execute(
                        "UPDATE embedding SET expires=? WHERE tenant=? AND subject=? AND campaign=?",
                        (expiry, row["tenant"], row["subject"], row["campaign"]),
                    )
            count = c.execute(
                "DELETE FROM embedding WHERE expires<=? OR NOT EXISTS "
                "(SELECT 1 FROM consent s WHERE s.tenant=embedding.tenant AND s.id=embedding.consent "
                "AND s.revoked=0 AND s.expires>?)",
                (now, now),
            ).rowcount
            c.execute(
                "DELETE FROM consent WHERE expires<=? AND NOT EXISTS "
                "(SELECT 1 FROM embedding e WHERE e.tenant=consent.tenant AND e.consent=consent.id)",
                (now,),
            )
            c.execute("DELETE FROM audit WHERE at<?", (now - 90 * 86400,))
        # Secure-delete + checkpoint bounds live DB remnants; operators must separately expire backups.
        with self.lock:
            self.c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return count

    def close(self):
        self.c.close()
