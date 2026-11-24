"""Authenticated storage and one-time migration of S3 credentials."""
import base64
import hashlib
import os
from typing import cast

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Engine, Table, inspect, select, text, update


def _s3_cipher() -> Fernet:
    raw = os.environ.get("S3_SECRET_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise ValueError("S3_SECRET_ENCRYPTION_KEY is required to store or read S3 credentials")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def encrypt_secret(secret: str) -> str:
    return _s3_cipher().encrypt(secret.encode()).decode("ascii") if secret else ""


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _s3_cipher().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored S3 credential cannot be decrypted") from exc


def migrate_legacy_credentials(engine: Engine) -> None:
    """Mark and rewrite legacy plaintext together, once across application replicas."""
    from app.db.entities import S3ProfileEntity, TritonInstanceEntity
    from app.db.schema import SCHEMA_LOCK_ID

    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SCHEMA_LOCK_ID})
        conn.execute(text("CREATE TABLE IF NOT EXISTS data_migrations (name VARCHAR PRIMARY KEY)"))
        claimed = conn.execute(text(
            "INSERT INTO data_migrations (name) VALUES ('encrypt-s3-credentials-v1') "
            "ON CONFLICT DO NOTHING RETURNING name"
        )).scalar()
        if claimed is None:
            return
        for entity, field in ((S3ProfileEntity, "secret_key_enc"), (TritonInstanceEntity, "s3_secret_key_enc")):
            table = cast(Table, inspect(entity).local_table)
            column = table.c[field]
            for row_id, secret in conn.execute(select(table.c.id, column).where(column != "")):
                conn.execute(update(table).where(table.c.id == row_id).values({column: encrypt_secret(secret)}))
