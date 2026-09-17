"""Short-lived, user-bound source download signatures (issue #540)."""
import hashlib
import hmac
import json
import time

from config import SESSION_SECRET_KEY


def sign_download(slug: str, filename: str, user_id: str, expires: int) -> str:
    if not SESSION_SECRET_KEY:
        raise RuntimeError("Session signing key is not configured")
    payload = json.dumps(["vod-source-v1", slug, filename, str(user_id), expires], separators=(",", ":"))
    return hmac.new(SESSION_SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_download(slug: str, filename: str, user_id: str, expires: int, token: str) -> bool:
    if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        return False
    now = int(time.time())
    if expires <= now or expires > now + 3600:
        return False
    return hmac.compare_digest(sign_download(slug, filename, user_id, expires), token)
