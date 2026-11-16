"""Accounts with admin approval.

- The admin account comes from ADMIN_EMAIL / ADMIN_PASSWORD (environment variables).
- Anyone can request access; they can sign in only after the admin approves them.
- Passwords are hashed with scrypt; sign-in uses a random server-side session token in an
  HttpOnly cookie, so revoking a user signs them out everywhere immediately.
- Failed sign-ins are rate limited per email and per IP.
- Each non-admin user gets a daily cap on AI calls, so nobody can run up your API bill.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone

from .db import get_db

log = logging.getLogger("resume_tailor.auth")

COOKIE = "rt_session"
SESSION_DAYS = 30
MIN_PASSWORD = 10
MIN_ADMIN_PASSWORD = 12
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$")
LOCAL_USER = {"id": "local", "email": "local", "name": "You", "status": "approved", "is_admin": True}


class AuthError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class LimitError(ValueError):
    """Raised when a user hits their daily AI-call limit."""


# --- Settings (read at call time so they can change without code edits) ----------------------------


def admin_email() -> str:
    return os.environ.get("ADMIN_EMAIL", "").strip().lower()


def admin_password() -> str:
    return os.environ.get("ADMIN_PASSWORD", "")


def local_mode() -> bool:
    """Single-user mode for `python run.py` on your own machine, when no admin account is configured."""
    return os.environ.get("RESUME_TAILOR_LOCAL") == "1" and not admin_email()


def configured() -> bool:
    return bool(admin_email()) and len(admin_password()) >= MIN_ADMIN_PASSWORD


def config_problem() -> str | None:
    if local_mode() or configured():
        return None
    if not admin_email() or not admin_password():
        return "This app isn't set up yet: the owner must set ADMIN_EMAIL and ADMIN_PASSWORD."
    return f"ADMIN_PASSWORD must be at least {MIN_ADMIN_PASSWORD} characters."


def allow_signup() -> bool:
    return os.environ.get("ALLOW_SIGNUP", "1") != "0"


def ai_daily_limit() -> int:
    try:
        return max(0, int(os.environ.get("AI_DAILY_LIMIT", "40")))
    except ValueError:
        return 40


# --- Passwords -------------------------------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, n, r, p, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


# --- Rate limiting (in memory; the app runs as one process) ------------------------------------------------

_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()


def _hits(key: str, window: float) -> int:
    now = time.time()
    with _attempts_lock:
        recent = [t for t in _attempts.get(key, []) if now - t < window]
        _attempts[key] = recent
        return len(recent)


def _record(key: str) -> None:
    with _attempts_lock:
        _attempts.setdefault(key, []).append(time.time())


def _clear(key: str) -> None:
    with _attempts_lock:
        _attempts.pop(key, None)


def reset_rate_limits() -> None:
    with _attempts_lock:
        _attempts.clear()


# --- Users ------------------------------------------------------------------------------------------------


def public_user(user: dict) -> dict:
    return {k: user.get(k) for k in ("id", "email", "name", "status", "is_admin", "note", "created_at", "decided_at", "last_login")} | {
        "is_admin": bool(user.get("is_admin"))
    }


def bootstrap_admin() -> None:
    """Create or update the admin account from the environment. Safe to call on every start."""
    if not configured():
        return
    db = get_db()
    email = admin_email()
    existing = db.one("SELECT * FROM users WHERE email = ?", (email,))
    if existing is None:
        db.execute(
            "INSERT INTO users (id, email, name, password_hash, status, is_admin, note, created_at, decided_at) "
            "VALUES (?, ?, ?, ?, 'approved', 1, '', ?, ?)",
            (uuid.uuid4().hex, email, "Admin", hash_password(admin_password()), time.time(), time.time()),
        )
    else:
        if not verify_password(admin_password(), existing["password_hash"]):
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(admin_password()), existing["id"]))
            db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (existing["id"],))
        db.execute("UPDATE users SET is_admin = 1, status = 'approved' WHERE id = ?", (existing["id"],))
    # Only the configured email is admin.
    db.execute("UPDATE users SET is_admin = 0 WHERE email <> ?", (email,))


def register(email: str, name: str, password: str, note: str, ip: str) -> dict:
    if not allow_signup():
        raise AuthError("New access requests are closed.", 403)
    email = (email or "").strip().lower()
    name = (name or "").strip()[:80]
    note = (note or "").strip()[:500]
    if _hits(f"reg:{ip}", 3600) >= 5:
        raise AuthError("Too many requests from this network. Try again later.", 429)
    if not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.")
    if not name:
        raise AuthError("Enter your name.")
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"Use a password of at least {MIN_PASSWORD} characters.")
    if email == admin_email():
        raise AuthError("This email is already registered. Sign in instead.")
    _record(f"reg:{ip}")
    db = get_db()
    existing = db.one("SELECT id, status FROM users WHERE email = ?", (email,))
    if existing:
        if existing["status"] == "pending":
            raise AuthError("You already requested access. The admin hasn't approved it yet.", 409)
        raise AuthError("This email is already registered. Sign in instead.", 409)
    user_id = uuid.uuid4().hex
    db.execute(
        "INSERT INTO users (id, email, name, password_hash, status, is_admin, note, created_at) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)",
        (user_id, email, name, hash_password(password), note, time.time()),
    )
    notify_admin(f"Resume Tailor: {name} <{email}> requested access." + (f" Note: {note}" if note else ""))
    return {"status": "pending"}


def login(email: str, password: str, ip: str) -> tuple[dict, str]:
    email = (email or "").strip().lower()
    if _hits(f"ip:{ip}", 900) >= 20 or _hits(f"email:{email}", 900) >= 6:
        raise AuthError("Too many failed attempts. Wait 15 minutes and try again.", 429)
    db = get_db()
    user = db.one("SELECT * FROM users WHERE email = ?", (email,))
    ok = verify_password(password or "", user["password_hash"] if user else _DUMMY_HASH)
    if not user or not ok:
        _record(f"ip:{ip}")
        _record(f"email:{email}")
        raise AuthError("Wrong email or password.", 401)
    if user["status"] == "pending":
        raise AuthError("Your access request is waiting for the admin's approval.", 403)
    if user["status"] != "approved":
        raise AuthError("Your access to this tool has been turned off by the admin.", 403)
    _clear(f"email:{email}")
    token = secrets.token_urlsafe(32)
    now = time.time()
    db.execute(
        "INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (_token_hash(token), user["id"], now, now + SESSION_DAYS * 86400),
    )
    db.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user["id"]))
    db.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now,))
    return user, token


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def user_for_token(token: str | None) -> dict | None:
    if local_mode():
        return dict(LOCAL_USER)
    if not token or not configured():
        return None
    row = get_db().one(
        "SELECT u.* FROM auth_sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?",
        (_token_hash(token), time.time()),
    )
    if not row or row["status"] != "approved":
        return None
    return row


def logout(token: str | None) -> None:
    if token:
        get_db().execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_token_hash(token),))


# --- Admin --------------------------------------------------------------------------------------------------


def list_users() -> list[dict]:
    db = get_db()
    today = _today()
    users = db.execute("SELECT * FROM users ORDER BY created_at DESC")
    usage = {r["user_id"]: r["calls"] for r in db.execute("SELECT user_id, calls FROM ai_usage WHERE day = ?", (today,))}
    counts = {r["owner_id"]: r["n"] for r in db.execute("SELECT owner_id, COUNT(*) AS n FROM resume_sessions GROUP BY owner_id")}
    return [public_user(u) | {"ai_calls_today": usage.get(u["id"], 0), "resumes": counts.get(u["id"], 0)} for u in users]


def set_status(user_id: str, status: str) -> dict:
    if status not in {"approved", "rejected", "blocked"}:
        raise AuthError("Unknown status.")
    db = get_db()
    user = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        raise AuthError("User not found.", 404)
    if user["is_admin"]:
        raise AuthError("The admin account can't be changed here.", 400)
    db.execute("UPDATE users SET status = ?, decided_at = ? WHERE id = ?", (status, time.time(), user_id))
    if status != "approved":
        db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    return public_user(db.one("SELECT * FROM users WHERE id = ?", (user_id,)) or {})


def delete_user(user_id: str) -> None:
    db = get_db()
    user = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        raise AuthError("User not found.", 404)
    if user["is_admin"]:
        raise AuthError("The admin account can't be deleted.", 400)
    for table, col in (("auth_sessions", "user_id"), ("resume_sessions", "owner_id"), ("ai_usage", "user_id"), ("users", "id")):
        db.execute(f"DELETE FROM {table} WHERE {col} = ?", (user_id,))


def pending_count() -> int:
    row = get_db().one("SELECT COUNT(*) AS n FROM users WHERE status = 'pending'")
    return int(row["n"]) if row else 0


# --- AI usage cap ---------------------------------------------------------------------------------------------


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def usage_today(user: dict) -> int:
    row = get_db().one("SELECT calls FROM ai_usage WHERE user_id = ? AND day = ?", (user["id"], _today()))
    return int(row["calls"]) if row else 0


def count_ai_call(user: dict) -> None:
    """Record one AI call for `user`, refusing it if they're over today's limit."""
    if user.get("id") == "local":
        return
    limit = ai_daily_limit()
    if not user.get("is_admin") and limit and usage_today(user) >= limit:
        raise LimitError(
            f"You've used today's {limit} AI steps. The limit resets at midnight UTC - ask the admin if you need more."
        )
    get_db().execute(
        "INSERT INTO ai_usage (user_id, day, calls) VALUES (?, ?, 1) "
        "ON CONFLICT (user_id, day) DO UPDATE SET calls = ai_usage.calls + 1",
        (user["id"], _today()),
    )


# --- Notifications ------------------------------------------------------------------------------------------


def notify_admin(message: str) -> None:
    """Optional push to the admin (ntfy.sh topic, Discord or Slack webhook) via NOTIFY_WEBHOOK_URL."""
    url = os.environ.get("NOTIFY_WEBHOOK_URL", "").strip()
    if not url.startswith("https://"):
        return

    def send() -> None:
        try:
            if "ntfy" in url:
                req = urllib.request.Request(url, data=message.encode(), headers={"Title": "Access request"})
            else:
                body = json.dumps({"content": message, "text": message}).encode()
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=8).close()  # noqa: S310 - admin-configured https URL
        except Exception as exc:  # noqa: BLE001
            log.warning("Admin notification failed: %s", exc)

    threading.Thread(target=send, daemon=True).start()
