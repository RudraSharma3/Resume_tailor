"""Exercise the real SDK request path against a mock HTTP transport (no network, no API key)."""

import json

import anthropic
import httpx2
import pytest

from resume_tailor import llm


def _sse_body(text: str, stop_reason: str = "end_turn") -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": {"output_tokens": 5}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


@pytest.fixture()
def wire(monkeypatch):
    captured = []
    replies = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append({"url": str(request.url), "headers": dict(request.headers), "body": json.loads(request.content)})
        text, stop = replies.pop(0)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse_body(text, stop))

    client = anthropic.Anthropic(api_key="test-key", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)), max_retries=0)
    monkeypatch.setattr(llm, "_client", client)
    monkeypatch.setattr(llm, "_fallbacks_enabled", True)
    return captured, replies


def test_jd_profile_request_shape(wire):
    captured, replies = wire
    replies.append((json.dumps({
        "job_title": "Backend Engineer", "title_variants": ["Software Engineer"], "company": "Northwind", "seniority": "mid",
        "min_years_experience": 3, "education_requirement": None, "responsibilities": ["Build APIs"],
        "keywords": [{"term": "Python", "aliases": ["python3"], "category": "hard_skill", "importance": "required", "jd_mentions": 3},
                     {"term": "python", "aliases": [], "category": "hard_skill", "importance": "required", "jd_mentions": 1}],
    }), "end_turn"))
    profile = llm.extract_jd_profile("Backend Engineer. Requirements: Python.")
    assert profile.job_title == "Backend Engineer" and [k.term for k in profile.keywords] == ["Python"]  # deduped

    req = captured[0]
    body = req["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in req["headers"].get("anthropic-beta", "")
    assert body["stream"] is True


def test_tailor_caches_stable_block(wire):
    from resume_tailor.ats import score_resume
    from resume_tailor.keywords import JDProfile, Keyword

    captured, replies = wire
    resume = "# A\n## Experience\n- Built Flask APIs used by 3 teams\n" + "filler words here " * 20
    replies.append((json.dumps({
        "resume_markdown": resume, "phase_summary": "ok", "changes": [], "keywords_added": [],
        "keywords_skipped": [], "metric_questions": [],
    }), "end_turn"))
    profile = JDProfile(job_title="Engineer", keywords=[Keyword("Python")])
    report = score_resume(resume, profile).to_dict()
    out = llm.tailor_iteration(jd_text="JD", original_resume=resume, current_resume=resume, profile=profile, report=report,
                               phase="alignment", facts=[], declined=[], issues=[], user_instructions="", target_min=82, target_max=85)
    assert out["resume_markdown"] == resume.strip()
    content = captured[0]["body"]["messages"][0]["content"]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert "<original_resume>" in content[0]["text"] and "<current_resume>" in content[1]["text"]


def test_refusal_and_truncation_raise(wire):
    _, replies = wire
    replies.append(("", "refusal"))
    with pytest.raises(llm.LLMError, match="declined"):
        llm.normalize_resume("text")
    replies.append(("partial", "max_tokens"))
    with pytest.raises(llm.LLMError, match="cut off"):
        llm.normalize_resume("text")


def test_latex_output_parsing(wire):
    _, replies = wire
    replies.append(("<tex_file>\n\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}\n</tex_file>\n<notes>\n- Changed summary\n- Check page count\n</notes>", "end_turn"))
    tex, notes = llm.edit_latex(original_tex="x", tailored_resume="y", facts=[])
    assert tex.startswith("\\documentclass") and tex.endswith("\\end{document}\n")
    assert notes == ["Changed summary", "Check page count"]
