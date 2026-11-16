"""Google Gemini provider (has a free tier). Plain REST calls, no extra dependencies.

Model choice: set GEMINI_MODEL to pin one. Otherwise the app asks Google which models your key
can use and picks the newest general-purpose Flash model, skipping any that have no free-tier
quota for your key. Model names change often, so this keeps working without code edits.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
FALLBACK_MODEL = "gemini-2.5-flash"  # used only if the model list can't be fetched
BLOCKED_FINISH = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "LANGUAGE", "IMAGE_SAFETY"}
_EXCLUDE = ("image", "tts", "audio", "live", "embedding", "native", "computer-use", "exp", "8b", "robotics", "aqa")

_lock = threading.Lock()
_models: list[str] | None = None
_unavailable: set[str] = set()
_working: str | None = None


class GeminiError(Exception):
    def __init__(self, status: int, message: str, reason: str = "", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.reason = reason
        self.retry_after = retry_after


def api_key() -> str:
    return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()


def configured_model() -> str:
    return os.environ.get("GEMINI_MODEL", "").strip().removeprefix("models/")


def current_model() -> str:
    return configured_model() or _working or "Gemini Flash (auto)"


def reset() -> None:
    global _models, _working
    with _lock:
        _models, _working = None, None
        _unavailable.clear()


# --- HTTP -----------------------------------------------------------------------------------------


def _parse_retry(details: list) -> float | None:
    for d in details or []:
        delay = d.get("retryDelay") if isinstance(d, dict) else None
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                return None
    return None


_ssl_ctx: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """Same certificate lookup as the Anthropic SDK (which works on Render): SSL_CERT_FILE/DIR if set,
    else the operating system's trust store, else certifi. Plain urllib defaults can point at an empty
    CA path on some hosted Pythons, which makes every HTTPS call fail."""
    global _ssl_ctx
    if _ssl_ctx is None:
        cafile, capath = os.environ.get("SSL_CERT_FILE"), os.environ.get("SSL_CERT_DIR")
        if cafile or capath:
            _ssl_ctx = ssl.create_default_context(cafile=cafile or None, capath=capath or None)
        else:
            try:
                import truststore

                _ssl_ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            except Exception:  # noqa: BLE001
                try:
                    import certifi

                    _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
                except Exception:  # noqa: BLE001
                    _ssl_ctx = ssl.create_default_context()
    return _ssl_ctx


def transport(method: str, path: str, body: dict | None = None, timeout: float = 300) -> dict:
    """One HTTP request to the Gemini API. Tests replace this function."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE_URL + path, data=data, method=method,
        headers={"x-goog-api-key": api_key(), "Content-Type": "application/json"},
    )
    try:
        context = ssl_context() if BASE_URL.startswith("https://") else None
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:  # noqa: S310 - fixed https endpoint
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read() or b"{}").get("error", {})
        except (ValueError, AttributeError):
            err = {}
        raise GeminiError(exc.code, err.get("message", str(exc)), err.get("status", ""), _parse_retry(err.get("details"))) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise GeminiError(0, f"{type(reason).__name__}: {reason}") from exc


# --- Model selection --------------------------------------------------------------------------------


def _rank(name: str) -> tuple | None:
    n = name.removeprefix("models/").lower()
    if "flash" not in n or any(x in n for x in _EXCLUDE):
        return None
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
    version = float(m.group(1)) if m else 0.0
    pinned = bool(re.search(r"-\d{3}$|-\d{2}-\d{4}$|-\d{2}-\d{2}$", n))
    # Lower sorts first: full Flash before Lite, newest version, stable before preview, alias before pinned.
    return ("lite" in n, -version, "preview" in n, pinned, n)


def _list_models() -> list[str]:
    names: list[str] = []
    token = ""
    for _ in range(10):
        data = transport("GET", "/models?pageSize=1000" + (f"&pageToken={token}" if token else ""), timeout=30)
        for m in data.get("models", []):
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                names.append(m["name"].removeprefix("models/"))
        token = data.get("nextPageToken", "")
        if not token:
            break
    return names


def candidates() -> list[str]:
    global _models
    pinned = configured_model()
    if pinned:
        return [pinned]
    with _lock:
        if _models is None:
            try:
                listed = _list_models()
            except GeminiError as exc:
                if exc.status in (400, 401, 403):
                    raise
                listed = []
            ranked = sorted((r, n) for n in listed if (r := _rank(n)) is not None)
            _models = [n for _, n in ranked] or [FALLBACK_MODEL]
        ordered = [n for n in _models if n not in _unavailable]
        if _working in ordered:
            ordered.remove(_working)
            ordered.insert(0, _working)
        return ordered


# --- Schema conversion (fallback for models without JSON-Schema support) ------------------------------


def to_openapi_schema(schema: dict) -> dict:
    if "anyOf" in schema:
        options = [o for o in schema["anyOf"] if o.get("type") != "null"]
        out = to_openapi_schema(options[0]) if options else {"type": "STRING"}
        out["nullable"] = True
        return out
    out: dict = {"type": str(schema.get("type", "string")).upper()}
    if "enum" in schema:
        out["enum"] = list(schema["enum"])
    if schema.get("type") == "object":
        out["properties"] = {k: to_openapi_schema(v) for k, v in schema.get("properties", {}).items()}
        out["required"] = list(schema.get("required", []))
    if schema.get("type") == "array":
        out["items"] = to_openapi_schema(schema.get("items", {"type": "string"}))
    return out


# --- Generation -------------------------------------------------------------------------------------


def _not_on_free_tier(exc: GeminiError) -> bool:
    msg = exc.message.lower()
    return exc.status == 429 and ("limit: 0" in msg or "limit:0" in msg)


def _generate(model: str, system: str, text: str, schema: dict | None) -> str:
    body: dict = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {},
    }
    if schema is not None:
        body["generationConfig"] = {"responseMimeType": "application/json", "responseJsonSchema": schema}
    use_openapi = False
    for attempt in range(4):
        try:
            data = transport("POST", f"/models/{model}:generateContent", body)
            break
        except GeminiError as exc:
            if exc.status == 400 and schema is not None and not use_openapi and "api key" not in exc.message.lower():
                # Older models only take the OpenAPI-style `responseSchema`.
                use_openapi = True
                body["generationConfig"] = {"responseMimeType": "application/json", "responseSchema": to_openapi_schema(schema)}
                continue
            retryable = exc.status in (500, 502, 503, 504) or (exc.status == 429 and not _not_on_free_tier(exc))
            if retryable and attempt < 3:
                time.sleep(min(30.0, exc.retry_after or (3 * (attempt + 1) ** 2)))
                continue
            raise
    else:  # pragma: no cover - loop always breaks or raises
        raise GeminiError(0, "No response")

    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise GeminiError(-1, f"blocked:{block}")
    cands = data.get("candidates") or []
    if not cands:
        raise GeminiError(-1, "blocked:empty")
    cand = cands[0]
    finish = cand.get("finishReason", "")
    parts = (cand.get("content") or {}).get("parts") or []
    out = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if finish == "MAX_TOKENS":
        raise GeminiError(-2, "truncated")
    if finish in BLOCKED_FINISH:
        raise GeminiError(-1, f"blocked:{finish}")
    return out


def generate(system: str, text: str, schema: dict | None = None) -> str:
    """Run one request, trying the next suitable model if one isn't available to this key."""
    global _working
    last: GeminiError | None = None
    for model in candidates():
        try:
            result = _generate(model, system, text, schema)
            _working = model
            return result
        except GeminiError as exc:
            if not configured_model() and (exc.status == 404 or _not_on_free_tier(exc)):
                with _lock:
                    _unavailable.add(model)
                last = exc
                continue
            raise
    raise last or GeminiError(404, "No Gemini model is available for this API key.")
