"""Generate private, disposable credentials and service configuration."""

import base64
import json
import os
from pathlib import Path
import secrets
import sys

root = Path(sys.argv[1])
repo = Path(sys.argv[2])
password = "Smoke1!" + secrets.token_hex(16)
client_secret = secrets.token_hex(32)
database_password = secrets.token_hex(32)
base = "http://127.0.0.1:18080"
(root / "password").write_text(password)
(root / "postgres.env").write_text(
    f"POSTGRES_PASSWORD={database_password}\nPOSTGRES_USER=smoke\nPOSTGRES_DB=smoke\n"
)
common = {
    "DATABASE_URL": f"postgresql://smoke:{database_password}@127.0.0.1:15432/smoke",
    "SESSION_SECRET": secrets.token_hex(32),
    "JWT_SECRET": secrets.token_hex(32),
    "S3_SECRET_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "SERVER_HTTPS_ENABLED": "false",
    "SESSION_HTTPS_ONLY": "false",
    "BACKEND_HOST": "127.0.0.1",
    "BACKEND_PORT": "18000",
    "KUBERNETES_ENABLED": "false",
    "OIDC_CONFIG_SOURCE": "env",
    "EMAIL_CONFIG_SOURCE": "env",
    "EMAIL_PUBLIC_APP_URL": base,
    "EMAIL_SMTP_HOST": "127.0.0.1",
    "EMAIL_SMTP_PORT": "11025",
    "EMAIL_SMTP_TLS_MODE": "none",
    "EMAIL_SMTP_ALLOW_INSECURE": "true",
    "EMAIL_SENDER_EMAIL": "triton@example.test",
    "OIDC_ISSUER": "http://127.0.0.1:18081/realms/smoke",
    "OIDC_CLIENT_ID": "triton-smoke",
    "OIDC_CLIENT_SECRET": client_secret,
    "OIDC_REDIRECT_URI": f"{base}/auth/callback",
    "FRONTEND_REDIRECT_URL": f"{base}/signin",
    "APP_BASE_URL": base,
    "OIDC_ADMIN_EMAILS": "admin@example.test",
}
for mode in ("smtp", "manual-link", "disabled", "oidc"):
    env = common | {
        "OIDC_ENABLED": str(mode == "oidc").lower(),
        "EMAIL_DELIVERY_MODE": mode if mode != "oidc" else "disabled",
    }
    (root / f"{mode}.env").write_text("".join(f"{key}={value}\n" for key, value in env.items()))

realm = {
    "realm": "smoke", "enabled": True, "sslRequired": "none",
    "registrationAllowed": False,
    "clients": [{
        "clientId": "triton-smoke", "secret": client_secret,
        "enabled": True, "publicClient": False, "standardFlowEnabled": True,
        "redirectUris": [f"{base}/auth/callback"], "webOrigins": [base],
        "defaultClientScopes": ["profile", "email"],
    }],
    "users": [{
        "username": name, "email": f"{name}@example.test", "emailVerified": True,
        "firstName": name.title(), "lastName": "Smoke", "enabled": True,
        "credentials": [{"type": "password", "value": password, "temporary": False}],
    } for name in ("admin", "member", "viewer", "pending")],
}
(root / "smoke-realm.json").write_text(json.dumps(realm))
# Preserve the production proxy configuration, changing only the listening ports.
nginx = (repo / "docker/nginx.conf").read_text()
(root / "nginx.conf").write_text(
    nginx.replace("listen 8080;", "listen 127.0.0.1:18080;")
    .replace("127.0.0.1:8000", "127.0.0.1:18000")
)
# Containers use non-root UIDs; docker cp copies these into their private filesystems.
for path in root.iterdir():
    os.chmod(path, 0o644 if path.suffix in {".json", ".conf"} else 0o600)
