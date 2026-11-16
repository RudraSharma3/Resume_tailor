"""AI calls (Claude or Google Gemini): JD profiling, resume normalization, recruiter review, tailoring, LaTeX."""

from __future__ import annotations

import json
import os
import re
from contextvars import ContextVar
from typing import Any, Callable

import anthropic

from . import config, gemini
from .keywords import JDProfile
from .prompts import (
    JD_PROFILE_SYSTEM,
    NORMALIZE_SYSTEM,
    RECRUITER_SYSTEM,
    TAILOR_PHASES,
    TAILOR_SYSTEM,
    TEX_SYSTEM,
)


class LLMError(RuntimeError):
    """A user-presentable failure from the AI step."""


_client: anthropic.Anthropic | None = None
_fallbacks_enabled = config.USE_FALLBACKS

# Called before every API request; the server sets it to enforce each user's daily AI limit.
call_guard: ContextVar[Callable[[], None] | None] = ContextVar("call_guard", default=None)


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=3)
    return _client


def provider() -> str:
    """'anthropic' or 'gemini'. LLM_PROVIDER picks explicitly; otherwise whichever key is set (Claude first)."""
    choice = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if choice in {"gemini", "google"}:
        return "gemini"
    if choice in {"anthropic", "claude"}:
        return "anthropic"
    if not config.has_api_credentials() and gemini.api_key():
        return "gemini"
    return "anthropic"


def available() -> bool:
    return bool(gemini.api_key()) if provider() == "gemini" else config.has_api_credentials()


def model_name() -> str:
    return gemini.current_model() if provider() == "gemini" else config.MODEL


def provider_label() -> str:
    return "Gemini" if provider() == "gemini" else "Claude"


def privacy_note() -> str:
    if provider() == "gemini":
        return ("AI provider: Google Gemini. On Google's free tier, Google may use what you submit "
                "(including resume text) to improve its products.")
    return ""


# --- JSON schema helpers ------------------------------------------------------------------------


def _obj(props: dict[str, Any]) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


_STR = {"type": "string"}
_NUM_OR_NULL = {"anyOf": [{"type": "number"}, {"type": "null"}]}
_STR_OR_NULL = {"anyOf": [{"type": "string"}, {"type": "null"}]}

JD_SCHEMA = _obj({
    "job_title": _STR,
    "title_variants": _arr(_STR),
    "company": _STR,
    "seniority": _STR,
    "min_years_experience": _NUM_OR_NULL,
    "education_requirement": _STR_OR_NULL,
    "responsibilities": _arr(_STR),
    "keywords": _arr(_obj({
        "term": _STR,
        "aliases": _arr(_STR),
        "category": {"type": "string", "enum": ["hard_skill", "tool", "certification", "domain", "responsibility", "soft_skill"]},
        "importance": {"type": "string", "enum": ["required", "preferred", "nice_to_have"]},
        "jd_mentions": {"type": "integer"},
    })),
})

RECRUITER_SCHEMA = _obj({
    "match_score": {"type": "integer"},
    "verdict": _STR,
    "would_shortlist": {"type": "boolean"},
    "summary": _STR,
    "top_missing_keywords": _arr(_obj({
        "keyword": _STR,
        "why_it_matters": _STR,
        "where_to_add": _STR,
        "evidence_in_resume": _STR,
    })),
    "strengths": _arr(_STR),
    "concerns": _arr(_STR),
    "quick_wins": _arr(_STR),
    "seniority_fit": _STR,
})

TAILOR_SCHEMA = _obj({
    "resume_markdown": _STR,
    "phase_summary": _STR,
    "changes": _arr(_obj({"section": _STR, "before": _STR, "after": _STR, "reason": _STR})),
    "keywords_added": _arr(_obj({"keyword": _STR, "evidence_quote": _STR})),
    "keywords_skipped": _arr(_obj({"keyword": _STR, "reason": _STR})),
    "metric_questions": _arr(_obj({"bullet": _STR, "question": _STR, "example_answer": _STR})),
})


# --- Core call ----------------------------------------------------------------------------------


def _call(system: str, content: list[dict], *, schema: dict | None = None, effort: str = "high",
          max_tokens: int = 32000) -> str:
    """One AI request. Returns the response text (JSON text when `schema` is given)."""
    guard = call_guard.get()
    if guard is not None:
        guard()
    if provider() == "gemini":
        return _call_gemini(system, content, schema)
    return _call_anthropic(system, content, schema=schema, effort=effort, max_tokens=max_tokens)


def _call_gemini(system: str, content: list[dict], schema: dict | None) -> str:
    if not gemini.api_key():
        raise LLMError("Set GEMINI_API_KEY to use Gemini.")
    text = "\n\n".join(block["text"] for block in content if block.get("type") == "text")
    try:
        return gemini.generate(system, text, schema)
    except gemini.GeminiError as exc:
        msg = exc.message.lower()
        if exc.status == -1:
            raise LLMError("Gemini declined this request (safety filter). Try rephrasing your instructions.") from exc
        if exc.status == -2:
            raise LLMError("Gemini's response was cut off (too long). Try a shorter resume or JD.") from exc
        if "api key" in msg or exc.status == 401:
            raise LLMError("Your Gemini API key was rejected. Check GEMINI_API_KEY.") from exc
        if "location is not supported" in msg:
            raise LLMError("Gemini isn't available from your server's region. Run the server in a US region.") from exc
        if exc.status == 404:
            raise LLMError(f"Gemini model '{gemini.current_model()}' wasn't found for this key. Remove GEMINI_MODEL to auto-pick one.") from exc
        if exc.status == 429:
            raise LLMError("Gemini's free-tier limit was reached. Wait a minute (per-minute limit) or until tomorrow (daily limit).") from exc
        if exc.status == 403:
            raise LLMError("This Gemini API key isn't allowed to use the API. Create a new key in Google AI Studio.") from exc
        if exc.status == 0:
            raise LLMError(f"Couldn't reach the Gemini API ({exc.message[:160]}). Try again shortly.") from exc
        raise LLMError(f"Gemini API error {exc.status}: {exc.message[:200]}") from exc


def _call_anthropic(system: str, content: list[dict], *, schema: dict | None, effort: str, max_tokens: int) -> str:
    """One streamed Messages request. Returns the concatenated text of the final message."""
    global _fallbacks_enabled
    output_config: dict[str, Any] = {"effort": effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    kwargs: dict[str, Any] = dict(
        model=config.MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system}],
        messages=[{"role": "user", "content": content}],
        thinking={"type": "adaptive"},
        output_config=output_config,
    )
    client = get_client()
    try:
        try:
            if _fallbacks_enabled:
                # Server-side refusal fallback: a declined request is retried on Anthropic's recommended model.
                with client.beta.messages.stream(
                    **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
                ) as stream:
                    message = stream.get_final_message()
            else:
                with client.messages.stream(**kwargs) as stream:
                    message = stream.get_final_message()
        except anthropic.BadRequestError as exc:
            if _fallbacks_enabled and "fallback" in str(exc).lower():
                _fallbacks_enabled = False  # this account/platform doesn't support it; retry plainly
                with client.messages.stream(**kwargs) as stream:
                    message = stream.get_final_message()
            else:
                raise
    except anthropic.AuthenticationError as exc:
        raise LLMError("Your Anthropic API key was rejected. Check ANTHROPIC_API_KEY.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise LLMError(f"Your API key can't use {config.MODEL}. Set RESUME_TAILOR_MODEL to a model you have access to.") from exc
    except anthropic.NotFoundError as exc:
        raise LLMError(f"Model {config.MODEL} wasn't found. Check RESUME_TAILOR_MODEL.") from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("Rate limited by the Anthropic API. Wait a minute and try again.") from exc
    except anthropic.BadRequestError as exc:
        if "credit balance" in str(exc).lower():
            raise LLMError(
                "Your Anthropic account is out of API credits. Add credits at console.anthropic.com (Billing), "
                "or switch to the free Gemini option (LLM_PROVIDER=gemini)."
            ) from exc
        raise LLMError(f"The API rejected the request: {getattr(exc, 'message', exc)}") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Anthropic API error {exc.status_code}. Try again shortly.") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Couldn't reach the Anthropic API. Check your internet connection.") from exc

    if message.stop_reason == "refusal":
        raise LLMError("The model declined this request. Try rephrasing your instructions.")
    if message.stop_reason == "max_tokens":
        raise LLMError("The response was cut off (too long). Try a shorter resume or JD.")
    return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")


def check() -> dict:
    """Admin diagnostic: one tiny request through the configured provider."""
    import time as _time

    start = _time.time()
    try:
        reply = _call("Reply with exactly: OK", [{"type": "text", "text": "Connection test."}], effort="low", max_tokens=2000)
        return {"ok": True, "provider": provider_label(), "model": model_name(), "reply": reply.strip()[:40],
                "seconds": round(_time.time() - start, 1)}
    except LLMError as exc:
        cause = exc.__cause__
        detail = f"{type(cause).__name__}: {cause}" if cause else ""
        return {"ok": False, "provider": provider_label(), "model": model_name(), "error": str(exc), "detail": detail[:500]}


def _json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    raise LLMError("The AI returned malformed output. Please try again.")


def _stable_block(jd_text: str, original_resume: str) -> dict:
    """JD + original resume: identical across iterations, so it is cached."""
    return {
        "type": "text",
        "text": f"<job_description>\n{jd_text}\n</job_description>\n\n<original_resume>\n{original_resume}\n</original_resume>",
        "cache_control": {"type": "ephemeral"},
    }


# --- Tasks --------------------------------------------------------------------------------------


def extract_jd_profile(jd_text: str) -> JDProfile:
    text = _call(
        JD_PROFILE_SYSTEM,
        [{"type": "text", "text": f"<job_description>\n{jd_text}\n</job_description>"}],
        schema=JD_SCHEMA,
        effort="medium",
        max_tokens=16000,
    )
    data = _json(text)
    seen: set[str] = set()
    keywords = []
    for kw in data.get("keywords", []):
        term = (kw.get("term") or "").strip()
        if not term or term.lower() in seen:
            continue
        seen.add(term.lower())
        keywords.append({
            "term": term,
            "aliases": [a for a in kw.get("aliases", []) if a and a.lower() != term.lower()],
            "category": kw.get("category", "hard_skill"),
            "importance": kw.get("importance", "preferred"),
            "jd_mentions": max(1, int(kw.get("jd_mentions") or 1)),
        })
    data["keywords"] = keywords
    data["source"] = "ai"
    return JDProfile.from_dict(data)


def normalize_resume(raw_text: str) -> str:
    text = _call(
        NORMALIZE_SYSTEM,
        [{"type": "text", "text": f"<raw_resume>\n{raw_text}\n</raw_resume>"}],
        effort="low",
        max_tokens=16000,
    )
    text = re.sub(r"^```[a-z]*\n|\n```$", "", text.strip())
    return text.strip()


def recruiter_review(resume_text: str, jd_text: str) -> dict:
    text = _call(
        RECRUITER_SYSTEM,
        [{"type": "text", "text": f"<job_description>\n{jd_text}\n</job_description>\n\n<resume>\n{resume_text}\n</resume>"}],
        schema=RECRUITER_SCHEMA,
        effort="medium",
        max_tokens=16000,
    )
    data = _json(text)
    data["match_score"] = max(0, min(100, int(data.get("match_score", 0))))
    data["top_missing_keywords"] = data.get("top_missing_keywords", [])[:5]
    return data


def _facts_block(facts: list[dict]) -> str:
    if not facts:
        return "(none yet)"
    lines = []
    for f in facts:
        if f.get("kind") == "confirmed_skill":
            lines.append(f"- [confirmed_skill] {f['keyword']}: {f.get('answer') or 'confirmed by candidate'}")
        elif f.get("kind") == "metric_answer":
            lines.append(f"- [metric_answer] Q: {f.get('question','')} | bullet: {f.get('bullet','')} | A: {f.get('answer','')}")
        else:
            lines.append(f"- [note] {f.get('answer','')}")
    return "\n".join(lines)


def tailor_iteration(
    *,
    jd_text: str,
    original_resume: str,
    current_resume: str,
    profile: JDProfile,
    report: dict,
    phase: str,
    facts: list[dict],
    declined: list[str],
    issues: list[str],
    user_instructions: str,
    target_min: float,
    target_max: float,
) -> dict:
    phase_name, phase_focus = TAILOR_PHASES.get(phase, TAILOR_PHASES["refine"])
    kw = report["keywords"]

    def fmt(items: list[dict]) -> str:
        return ", ".join(f"{h['term']} ({h['importance']})" for h in items) or "(none)"

    weak_bullets = [b for b in report["bullets"] if b["issues"]][:12]
    bullet_notes = "\n".join(f"- \"{b['text'][:140]}\" -> {', '.join(b['issues'])}" for b in weak_bullets) or "(none)"
    comps = "\n".join(f"- {c['label']}: {c['score']}/{c['max']} - {c['detail']}" for c in report["components"])

    volatile = f"""<jd_keywords>
{json.dumps([{"term": k.term, "aliases": k.aliases, "importance": k.importance, "category": k.category} for k in profile.keywords], ensure_ascii=False)}
</jd_keywords>

<verified_facts>
{_facts_block(facts)}
</verified_facts>

<declined_keywords>
{", ".join(declined) or "(none)"}
</declined_keywords>

<current_resume>
{current_resume}
</current_resume>

<ats_scorecard>
Current ATS score: {report['score']}/100 (target band {target_min:g}-{target_max:g}).
{comps}
Missing keywords: {fmt(kw['missing'])}
Only in skills list (not shown in experience): {fmt(kw['skills_only'])}
Bullets to improve:
{bullet_notes}
</ats_scorecard>

<issues_from_last_pass>
{chr(10).join('- ' + i for i in issues) or '(none)'}
</issues_from_last_pass>

<phase>
{phase_name}. {phase_focus}
</phase>

<user_instructions>
{user_instructions.strip() or '(none)'}
</user_instructions>

Target job title: {profile.job_title or 'see JD'}.
Produce the next version of the resume for this phase. The score should move toward {target_min:g}-{target_max:g} through honest, natural edits; if the remaining gaps need facts you don't have, ask for them in metric_questions instead of inventing."""

    text = _call(
        TAILOR_SYSTEM,
        [_stable_block(jd_text, original_resume), {"type": "text", "text": volatile}],
        schema=TAILOR_SCHEMA,
        effort="high",
        max_tokens=48000,
    )
    data = _json(text)
    resume = (data.get("resume_markdown") or "").strip()
    if len(resume) < 100:
        raise LLMError("The AI returned an empty resume. Please run the iteration again.")
    data["resume_markdown"] = resume
    return data


def edit_latex(*, original_tex: str, tailored_resume: str, facts: list[dict]) -> tuple[str, list[str]]:
    content = [
        {"type": "text", "text": f"<original_tex>\n{original_tex}\n</original_tex>"},
        {"type": "text", "text": f"<tailored_resume>\n{tailored_resume}\n</tailored_resume>\n\n<verified_facts>\n{_facts_block(facts)}\n</verified_facts>\n\nRewrite the .tex so it contains the tailored resume content."},
    ]
    text = _call(TEX_SYSTEM, content, effort="high", max_tokens=64000)
    match = re.search(r"<tex_file>\s*(.*?)\s*</tex_file>", text, re.S)
    if not match:
        # Model may have skipped the wrapper; accept a bare document.
        if "\\begin{document}" in text:
            tex = text.strip()
        else:
            raise LLMError("The AI didn't return a LaTeX file. Please try again.")
    else:
        tex = match.group(1)
    tex = re.sub(r"^```(?:latex|tex)?\n|\n```$", "", tex.strip())
    notes_match = re.search(r"<notes>\s*(.*?)\s*</notes>", text, re.S)
    notes = []
    if notes_match:
        notes = [re.sub(r"^[-*•]\s*", "", ln).strip() for ln in notes_match.group(1).split("\n") if ln.strip()]
    return tex + "\n", notes
