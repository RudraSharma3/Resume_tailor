"""Runtime configuration, read from environment variables (or a local .env file)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Minimal .env loader so users don't need python-dotenv."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

# Claude model used for every AI step. Override with RESUME_TAILOR_MODEL.
MODEL = os.environ.get("RESUME_TAILOR_MODEL", "claude-opus-5-5")

# Models that accept the server-side refusal fallback parameter.
FALLBACK_MODELS = {
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
    "claude-fable-5-2",
    "claude-fable-5-1",
}
USE_FALLBACKS = os.environ.get("RESUME_TAILOR_FALLBACKS", "1") != "0" and MODEL in FALLBACK_MODELS

# Score band the auto-tailor aims for. 82-85 reads as strong without looking keyword-stuffed.
TARGET_MIN = float(os.environ.get("RESUME_TAILOR_TARGET_MIN", "82"))
TARGET_MAX = float(os.environ.get("RESUME_TAILOR_TARGET_MAX", "85"))
MAX_AUTO_ITERATIONS = int(os.environ.get("RESUME_TAILOR_MAX_ITERATIONS", "4"))

DATA_DIR = Path(os.environ.get("RESUME_TAILOR_DATA_DIR", str(ROOT / "data")))
SESSIONS_DIR = DATA_DIR / "sessions"

HOST = os.environ.get("RESUME_TAILOR_HOST", "127.0.0.1")
PORT = int(os.environ.get("RESUME_TAILOR_PORT", "8000"))


def has_api_credentials() -> bool:
    """True when the Anthropic SDK can probably authenticate."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    # `ant auth login` profiles live here; the SDK picks them up automatically.
    profile_dir = Path.home() / ".config" / "anthropic"
    return profile_dir.exists() and any(profile_dir.iterdir())
