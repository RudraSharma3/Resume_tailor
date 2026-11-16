"""Gemini provider: model auto-pick, free-tier fallback, schema fallback, error mapping, real HTTP code."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from resume_tailor import gemini, llm


@pytest.fixture(autouse=True)
def gemini_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    gemini.reset()
    yield
    gemini.reset()


MODELS = {"models": [
    {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent", "countTokens"]},
    {"name": "models/gemini-3.8-flash-lite", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.8-flash-image", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.8-flash-preview-tts", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.5-pro", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-embedding-2", "supportedGenerationMethods": ["embedContent"]},
]}


def ok(text, finish="STOP", thought=None):
    parts = ([{"text": thought, "thought": True}] if thought else []) + [{"text": text}]
    return {"candidates": [{"content": {"parts": parts, "role": "model"}, "finishReason": finish}]}


class FakeTransport:
    def __init__(self, replies):
        self.replies = replies  # callables or dicts, consumed per generateContent call
        self.calls = []

    def __call__(self, method, path, body=None, timeout=300):
        self.calls.append((method, path, body))
        if path.startswith("/models?"):
            return MODELS
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_picks_newest_full_flash_model(monkeypatch):
    t = FakeTransport([ok("hello")])
    monkeypatch.setattr(gemini, "transport", t)
    assert gemini.candidates() == ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-3.8-flash-lite"]
    assert gemini.generate("sys", "user") == "hello"
    assert t.calls[-1][1] == "/models/gemini-3.8-flash:generateContent"
    assert gemini.current_model() == "gemini-3.8-flash"


def test_request_body_uses_json_schema_and_skips_thoughts(monkeypatch):
    t = FakeTransport([ok('{"a": 1}', thought="thinking...")])
    monkeypatch.setattr(gemini, "transport", t)
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"], "additionalProperties": False}
    assert gemini.generate("SYSTEM", "USER", schema) == '{"a": 1}'
    body = t.calls[-1][2]
    assert body["systemInstruction"] == {"parts": [{"text": "SYSTEM"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "USER"}]}]
    assert body["generationConfig"] == {"responseMimeType": "application/json", "responseJsonSchema": schema}


def test_falls_back_to_next_model_when_no_free_quota(monkeypatch):
    no_quota = gemini.GeminiError(429, "Quota exceeded for metric: generate_content_free_tier_requests, limit: 0, model: gemini-3.8-flash")
    t = FakeTransport([no_quota, ok("from 2.5")])
    monkeypatch.setattr(gemini, "transport", t)
    assert gemini.generate("s", "u") == "from 2.5"
    assert gemini.current_model() == "gemini-2.5-flash"
    t.replies.append(ok("again"))
    gemini.generate("s", "u")
    assert t.calls[-1][1] == "/models/gemini-2.5-flash:generateContent"  # remembered


def test_retries_rate_limit_then_reports_it(monkeypatch):
    busy = gemini.GeminiError(429, "Resource has been exhausted (e.g. check quota).", retry_after=1)
    t = FakeTransport([busy, ok("fine")])
    monkeypatch.setattr(gemini, "transport", t)
    assert gemini.generate("s", "u") == "fine"
    t.replies.extend([busy] * 4)
    with pytest.raises(llm.LLMError, match="free-tier limit"):
        llm._call("s", [{"type": "text", "text": "u"}])


def test_openapi_schema_fallback(monkeypatch):
    t = FakeTransport([gemini.GeminiError(400, "Invalid JSON payload: unknown name responseJsonSchema"), ok("{}")])
    monkeypatch.setattr(gemini, "transport", t)
    gemini.generate("s", "u", llm.JD_SCHEMA)
    cfg = t.calls[-1][2]["generationConfig"]
    assert "responseJsonSchema" not in cfg
    schema = cfg["responseSchema"]
    assert schema["type"] == "OBJECT"
    assert schema["properties"]["min_years_experience"] == {"type": "NUMBER", "nullable": True}
    assert schema["properties"]["keywords"]["items"]["properties"]["importance"]["enum"] == ["required", "preferred", "nice_to_have"]
    assert "additionalProperties" not in json.dumps(schema)


@pytest.mark.parametrize("reply,pattern", [
    (ok("", finish="SAFETY"), "declined"),
    ({"promptFeedback": {"blockReason": "OTHER"}}, "declined"),
    (ok("partial", finish="MAX_TOKENS"), "cut off"),
    (gemini.GeminiError(400, "API key not valid. Please pass a valid API key."), "key was rejected"),
    (gemini.GeminiError(400, "User location is not supported for the API use."), "region"),
])
def test_error_messages(monkeypatch, reply, pattern):
    monkeypatch.setattr(gemini, "transport", FakeTransport([reply, reply]))
    with pytest.raises(llm.LLMError, match=pattern):
        llm._call("s", [{"type": "text", "text": "u"}], schema=None)


def test_provider_selection(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(llm.config, "has_api_credentials", lambda: False)
    assert llm.provider() == "gemini" and llm.available()
    monkeypatch.setattr(llm.config, "has_api_credentials", lambda: True)
    assert llm.provider() == "anthropic"  # both keys: Claude unless told otherwise
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert llm.provider() == "gemini"
    assert "Google" in llm.privacy_note()


def test_jd_profile_end_to_end_via_gemini(monkeypatch):
    payload = {"job_title": "Backend Engineer", "title_variants": [], "company": "", "seniority": "", "min_years_experience": 3,
               "education_requirement": None, "responsibilities": [],
               "keywords": [{"term": "Python", "aliases": [], "category": "hard_skill", "importance": "required", "jd_mentions": 2}]}
    monkeypatch.setattr(gemini, "transport", FakeTransport([ok(json.dumps(payload))]))
    profile = llm.extract_jd_profile("JD text")
    assert profile.job_title == "Backend Engineer" and profile.keywords[0].term == "Python"


def test_real_transport_against_local_server(monkeypatch):
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen["path"], seen["key"] = self.path, self.headers.get("x-goog-api-key")
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "fail" in self.path:
                out = json.dumps({"error": {"code": 429, "message": "slow down", "status": "RESOURCE_EXHAUSTED",
                                            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]}}).encode()
                self.send_response(429)
            else:
                out = json.dumps(ok("pong")).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(gemini, "BASE_URL", f"http://127.0.0.1:{server.server_port}/v1beta")
    try:
        data = gemini.transport("POST", "/models/m:generateContent", {"x": 1})
        assert data["candidates"][0]["content"]["parts"][0]["text"] == "pong"
        assert seen == {"path": "/v1beta/models/m:generateContent", "key": "test-key", "body": {"x": 1}}
        with pytest.raises(gemini.GeminiError) as info:
            gemini.transport("POST", "/models/fail:generateContent", {})
        assert info.value.status == 429 and info.value.retry_after == 7.0 and info.value.reason == "RESOURCE_EXHAUSTED"
    finally:
        server.shutdown()
