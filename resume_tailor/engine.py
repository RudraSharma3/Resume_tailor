"""Workflow: sessions, analysis, tailoring iterations, score levers and LaTeX editing."""

from __future__ import annotations

import contextvars
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterator

from . import config, guards, llm
from .ats import score_resume
from .db import get_db
from .diffing import diff_stats, line_diff
from .keywords import JDProfile, heuristic_profile
from .parsers import clean_text, extract_text, latex_to_text
from .prompts import TAILOR_PHASES
from .resume_struct import parse_resume, years_of_experience
from .whatif import analyze as whatif_analyze

ProgressFn = Callable[[str], None]


@dataclass
class Session:
    id: str
    created_at: float
    resume_filename: str
    resume_raw: str
    jd_text: str
    resume_tex: str | None = None
    profile: dict | None = None
    versions: list[dict] = field(default_factory=list)
    current: int = 0
    facts: list[dict] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    declined: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)
    reviews: dict[str, dict] = field(default_factory=dict)
    phases_done: list[str] = field(default_factory=list)
    instructions: str = ""
    tex: dict | None = None
    notices: list[str] = field(default_factory=list)
    manual_sources: list[str] = field(default_factory=list)  # text the user wrote themselves
    owner_id: str = "local"

    # ------------------------------------------------------------------
    @property
    def jd_profile(self) -> JDProfile:
        if not self.profile:
            raise ValueError("Session has not been analyzed yet.")
        return JDProfile.from_dict(self.profile)

    def version(self, n: int | None = None) -> dict:
        if not self.versions:
            raise ValueError("No versions yet.")
        n = self.current if n is None else n
        if n < 0 or n >= len(self.versions):
            raise ValueError(f"Version {n} does not exist.")
        return self.versions[n]

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "Session":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# --- Store -----------------------------------------------------------------------------------------


class SessionStore:
    """Resume sessions in the database, one row per session, owned by a user."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def lock(self, sid: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(sid, threading.Lock())

    def get(self, sid: str) -> Session:
        if not re.fullmatch(r"[a-f0-9]{32}", sid or ""):
            raise KeyError(sid)
        row = get_db().one("SELECT data FROM resume_sessions WHERE id = ?", (sid,))
        if not row:
            raise KeyError(sid)
        return Session.from_json(json.loads(row["data"]))

    def save(self, session: Session) -> None:
        title = session.resume_filename
        if session.profile:
            title = " @ ".join(p for p in (session.profile.get("job_title"), session.profile.get("company")) if p) or title
        score = session.versions[session.current]["report"]["score"] if session.versions else None
        now = time.time()
        get_db().execute(
            "INSERT INTO resume_sessions (id, owner_id, created_at, updated_at, title, score, data) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET updated_at = excluded.updated_at, title = excluded.title, "
            "score = excluded.score, data = excluded.data",
            (session.id, session.owner_id, session.created_at, now, title[:200], score,
             json.dumps(session.to_json(), ensure_ascii=False)),
        )

    def create(self, **kwargs: Any) -> Session:
        session = Session(id=uuid.uuid4().hex, created_at=time.time(), **kwargs)
        self.save(session)
        return session

    def list_for(self, owner_id: str) -> list[dict]:
        return get_db().execute(
            "SELECT id, created_at, updated_at, title, score FROM resume_sessions WHERE owner_id = ? "
            "ORDER BY updated_at DESC LIMIT 100",
            (owner_id,),
        )

    def delete(self, sid: str) -> None:
        get_db().execute("DELETE FROM resume_sessions WHERE id = ?", (sid,))


STORE = SessionStore()


# --- Helpers ---------------------------------------------------------------------------------------


def create_session(
    resume_filename: str | None, resume_bytes: bytes | None, resume_text: str | None,
    jd_filename: str | None, jd_bytes: bytes | None, jd_text: str | None, owner_id: str = "local",
) -> Session:
    tex_source = None
    if resume_bytes:
        raw = extract_text(resume_filename or "resume.txt", resume_bytes)
        if (resume_filename or "").lower().endswith(".tex"):
            tex_source = resume_bytes.decode("utf-8", errors="replace")
    elif resume_text and resume_text.strip():
        pasted = resume_text.strip()
        if "\\begin{document}" in pasted:
            tex_source = pasted
            raw = clean_text(latex_to_text(pasted))
        else:
            raw = clean_text(pasted)
        resume_filename = resume_filename or "pasted-resume.txt"
    else:
        raise ValueError("Upload a resume file or paste your resume text.")

    if jd_bytes:
        jd = extract_text(jd_filename or "jd.txt", jd_bytes)
    elif jd_text and jd_text.strip():
        jd = clean_text(jd_text)
    else:
        raise ValueError("Paste the job description or upload it as a file.")
    if len(jd.split()) < 30:
        raise ValueError("The job description looks too short. Paste the full JD (responsibilities + requirements).")
    if len(raw.split()) < 50:
        raise ValueError("The resume text looks too short - check the file uploaded correctly.")
    return STORE.create(resume_filename=resume_filename or "resume", resume_raw=raw, jd_text=jd, resume_tex=tex_source, owner_id=owner_id)


def _sources(session: Session) -> list[str]:
    srcs = [session.resume_raw]
    if session.versions:
        srcs.append(session.versions[0]["text"])
    for f in session.facts:
        srcs.append(" ".join(str(f.get(k, "")) for k in ("question", "answer", "keyword")))
    srcs.extend(session.manual_sources)
    return srcs


def _extra_allowed_numbers(session: Session) -> set[float]:
    allowed: set[float] = set()
    if session.versions:
        years = years_of_experience(parse_resume(session.versions[0]["text"]))
        if years:
            allowed |= {float(int(years)), float(round(years)), round(years, 1)}
    if session.profile:
        for kw in session.profile.get("keywords", []):
            allowed |= guards.number_values(kw.get("term", ""))
    return allowed


def compute_flags(session: Session, text: str, extra_sources: list[str] | None = None) -> list[dict]:
    profile = session.jd_profile
    original = session.versions[0]["text"] if session.versions else session.resume_raw
    sources = _sources(session) + list(extra_sources or [])
    flags = guards.unverified_numbers(text, sources, _extra_allowed_numbers(session))
    flags += guards.skill_flags(
        text, "\n".join([original, session.resume_raw, *(extra_sources or [])]), profile, session.evidence, sources,
        set(session.confirmed), set(session.declined),
    )
    flags += guards.placeholder_flags(text)
    return [f.to_dict() for f in flags]


def score_text(session: Session, text: str) -> dict:
    return score_resume(text, session.jd_profile, session.jd_text).to_dict()


def public_version(session: Session, n: int, compare: str = "prev") -> dict:
    v = dict(session.version(n))
    base = None
    if compare == "prev" and n > 0:
        base = session.versions[n - 1]["text"]
    elif compare == "original" and n > 0:
        base = session.versions[0]["text"]
    if base is not None:
        rows = line_diff(base, v["text"])
        v["diff"] = rows
        v["diff_stats"] = diff_stats(rows)
    else:
        v["diff"] = None
        v["diff_stats"] = None
    v["review"] = session.reviews.get(str(n))
    return v


def timeline(session: Session) -> list[dict]:
    return [
        {"n": v["n"], "label": v["label"], "score": v["report"]["score"], "display_score": v["report"]["display_score"],
         "review_score": (session.reviews.get(str(v["n"])) or {}).get("match_score"), "flags": len(v.get("flags", []))}
        for v in session.versions
    ]


def session_state(session: Session) -> dict:
    return {
        "id": session.id,
        "resume_filename": session.resume_filename,
        "jd_text": session.jd_text,
        "resume_raw": session.resume_raw,
        "has_tex": bool(session.resume_tex),
        "resume_tex": session.resume_tex,
        "profile": session.profile,
        "current": session.current,
        "timeline": timeline(session),
        "version": public_version(session, session.current) if session.versions else None,
        "facts": session.facts,
        "confirmed": session.confirmed,
        "declined": session.declined,
        "phases_done": session.phases_done,
        "instructions": session.instructions,
        "tex": dict(session.tex, diff=line_diff(session.tex["original"], session.tex["edited"])) if session.tex else None,
        "reviews": session.reviews,
        "notices": session.notices,
        "ai_available": llm.available(),
        "target": {"min": config.TARGET_MIN, "max": config.TARGET_MAX},
        "model": config.MODEL,
    }


def _word_drift(raw: str, normalized: str) -> float:
    rw = set(re.findall(r"[a-z0-9]+", raw.lower()))
    nw = set(re.findall(r"[a-z0-9]+", normalized.lower()))
    if not rw:
        return 1.0
    return max(len(rw - nw) / len(rw), len(nw - rw) / len(rw))


# --- Analysis --------------------------------------------------------------------------------------


def analyze(session: Session, progress: ProgressFn) -> dict:
    ai = llm.available()
    if ai:
        progress("Reading the job description and cleaning up your resume text...")
        with ThreadPoolExecutor(max_workers=2) as pool:
            # copy_context keeps the per-user AI-usage guard active inside the worker threads
            fut_profile = pool.submit(contextvars.copy_context().run, llm.extract_jd_profile, session.jd_text)
            fut_norm = pool.submit(contextvars.copy_context().run, llm.normalize_resume, session.resume_raw)
            profile = fut_profile.result()
            try:
                normalized = fut_norm.result()
            except llm.LLMError:
                normalized = session.resume_raw
        drift = _word_drift(session.resume_raw, normalized)
        new_numbers = guards.unverified_numbers(normalized, [session.resume_raw])
        if drift > 0.08 or new_numbers:
            session.notices.append("Used your resume text as extracted (the cleanup pass changed too much).")
            normalized = session.resume_raw
    else:
        progress("No API key found - using the offline keyword extractor (AI tailoring disabled).")
        profile = heuristic_profile(session.jd_text)
        normalized = session.resume_raw

    session.profile = profile.to_dict()
    progress(f"Scoring your resume against {len(profile.keywords)} JD keywords...")
    report = score_text(session, normalized)
    session.versions = [{
        "n": 0, "label": "Original", "phase": "original", "text": normalized, "report": report,
        "changes": [], "keywords_added": [], "keywords_skipped": [], "questions": [], "flags": [],
        "summary": "Your resume as uploaded.", "instructions": "", "created_at": time.time(),
    }]
    session.current = 0
    session.phases_done = []
    STORE.save(session)

    if ai:
        progress("Senior recruiter review in progress...")
        try:
            session.reviews["0"] = llm.recruiter_review(normalized, session.jd_text)
        except llm.LLMError as exc:
            session.notices.append(f"Recruiter review failed: {exc}")
        STORE.save(session)
    return session_state(session)


def recruiter(session: Session, n: int | None = None) -> dict:
    v = session.version(n)
    review = llm.recruiter_review(v["text"], session.jd_text)
    session.reviews[str(v["n"])] = review
    STORE.save(session)
    return review


# --- Tailoring -------------------------------------------------------------------------------------


def record_answers(session: Session, answers: list[dict]) -> int:
    """Store answers to metric questions as verified facts. Returns how many were added."""
    questions = {q["id"]: q for v in session.versions for q in v.get("questions", [])}
    added = 0
    for item in answers or []:
        qid, answer = item.get("id"), (item.get("answer") or "").strip()
        if not answer or qid not in questions:
            continue
        q = questions[qid]
        q["answered"] = True
        no_data = answer.lower() in {"skip", "-", "n/a", "na", "none", "unknown", "don't know", "dont know", "no"}
        session.facts = [f for f in session.facts if f.get("qid") != qid]
        session.facts.append({
            "id": uuid.uuid4().hex[:8], "qid": qid,
            "kind": "no_metric" if no_data else "metric_answer",
            "question": q["question"], "bullet": q.get("bullet", ""),
            "answer": "Candidate has no reliable number for this - don't ask again, keep bullet without a number." if no_data else answer,
        })
        added += 1
    if added:
        STORE.save(session)
    return added


def add_note(session: Session, note: str) -> None:
    note = (note or "").strip()
    if note:
        session.facts.append({"id": uuid.uuid4().hex[:8], "kind": "note", "answer": note})
        STORE.save(session)


def next_phase(session: Session, instructions: str, requested: str | None = None) -> str:
    if requested and requested in TAILOR_PHASES:
        return requested
    if any(f["kind"] == "confirmed_skill" and not f.get("applied") for f in session.facts):
        return "apply_confirmed"
    for phase in ("alignment", "impact", "polish"):
        if phase not in session.phases_done:
            return phase
    return "custom" if instructions.strip() else "refine"


def _issues_for(session: Session, version: dict) -> list[str]:
    issues = []
    for f in version.get("flags", []):
        if f["kind"] == "unverified_number":
            issues.append(f"Unverified number {f['value']} in: \"{f['excerpt'][:120]}\" - remove it unless it's in verified_facts.")
        elif f["kind"] == "declined_skill":
            issues.append(f"Remove '{f['value']}' - the candidate declined it.")
        elif f["kind"] == "unverified_skill":
            issues.append(f"'{f['value']}' lacks evidence - keep it only if the original resume or verified facts support it (give the quote), otherwise remove.")
        elif f["kind"] == "placeholder":
            issues.append(f"Remove placeholder {f['value']} - ask a metric question instead.")
    pen = next((c for c in version["report"]["components"] if c["id"] == "penalty"), None)
    if pen and pen["score"] < 0:
        issues.extend(pen["tips"])
    return issues


def run_iteration(session: Session, instructions: str = "", phase: str | None = None) -> dict:
    if not session.versions:
        raise ValueError("Analyze the resume first.")
    if not llm.available():
        raise llm.LLMError("AI tailoring needs an Anthropic API key (set ANTHROPIC_API_KEY and restart).")
    cur = session.version()
    phase = next_phase(session, instructions, phase)
    data = llm.tailor_iteration(
        jd_text=session.jd_text,
        original_resume=session.versions[0]["text"],
        current_resume=cur["text"],
        profile=session.jd_profile,
        report=cur["report"],
        phase=phase,
        facts=session.facts,
        declined=session.declined,
        issues=_issues_for(session, cur),
        user_instructions=instructions,
        target_min=config.TARGET_MIN,
        target_max=config.TARGET_MAX,
    )
    for item in data.get("keywords_added", []):
        kw, quote = (item.get("keyword") or "").strip(), (item.get("evidence_quote") or "").strip()
        if kw and quote:
            session.evidence[kw.lower()] = quote

    text = data["resume_markdown"]
    report = score_text(session, text)
    n = session.current + 1  # later versions are dropped if the user reverted
    answered = {f.get("question", "").strip().lower() for f in session.facts if f.get("qid")}
    questions = []
    for i, q in enumerate(data.get("metric_questions", [])[:6]):
        if q.get("question", "").strip().lower() in answered:
            continue
        questions.append({"id": f"q{n}-{i}", "question": q.get("question", ""), "bullet": q.get("bullet", ""),
                          "example": q.get("example_answer", ""), "answered": False})

    version = {
        "n": n,
        "label": TAILOR_PHASES[phase][0],
        "phase": phase,
        "text": text,
        "report": report,
        "changes": data.get("changes", []),
        "keywords_added": data.get("keywords_added", []),
        "keywords_skipped": data.get("keywords_skipped", []),
        "questions": questions,
        "flags": [],
        "summary": data.get("phase_summary", ""),
        "instructions": instructions,
        "created_at": time.time(),
    }
    session.versions = session.versions[: session.current + 1]
    session.versions.append(version)
    session.current = version["n"]
    version["flags"] = compute_flags(session, text)
    if phase not in session.phases_done:
        session.phases_done.append(phase)
    if phase == "apply_confirmed":
        for f in session.facts:
            if f["kind"] == "confirmed_skill":
                f["applied"] = True
    if instructions.strip():
        session.instructions = instructions.strip()
    STORE.save(session)
    return public_version(session, version["n"])


def tailor_events(
    session: Session, *, mode: str, instructions: str, answers: list[dict], note: str,
    phase: str | None, max_iterations: int,
) -> Iterator[dict]:
    """Generator of progress events for single or auto iterations."""
    added = record_answers(session, answers)
    if added:
        yield {"event": "status", "message": f"Saved {added} answer(s) as verified facts."}
    add_note(session, note)

    target_min, target_max = config.TARGET_MIN, config.TARGET_MAX
    iterations = 1 if mode == "single" else max(1, min(max_iterations, 8))
    plateau = 0
    for i in range(iterations):
        cur = session.version()
        if mode == "auto" and cur["report"]["score"] >= target_min and not any(
            f["kind"] == "confirmed_skill" and not f.get("applied") for f in session.facts
        ) and not cur.get("flags"):
            yield {"event": "status", "message": f"Score {cur['report']['display_score']} is in the target band - stopping here so it stays natural."}
            break
        upcoming = next_phase(session, instructions, phase if i == 0 else None)
        yield {"event": "status", "message": f"Iteration {len(session.versions)}: {TAILOR_PHASES[upcoming][0]}...", "phase": upcoming}
        version = run_iteration(session, instructions, phase if i == 0 else None)
        yield {"event": "iteration", "version": version, "timeline": timeline(session)}
        delta = version["report"]["score"] - cur["report"]["score"]
        if mode == "auto":
            if version["report"]["score"] >= target_min and not version["flags"]:
                yield {"event": "status", "message": f"Reached {version['report']['display_score']} - inside the {target_min:g}-{target_max:g} band."}
                break
            plateau = plateau + 1 if delta < 1.0 else 0
            if plateau >= 2:
                yield {"event": "status", "message": "Score has plateaued. The remaining gaps need facts only you can give: answer the metric questions or review the score levers."}
                break
    final = session.version()
    if final["report"]["score"] > target_max + 5:
        yield {"event": "status", "message": "Score is well above the target band - skim it to make sure it still sounds like you."}
    yield {"event": "done", "state": session_state(session), "levers": levers(session)}


# --- Score levers & confirmations --------------------------------------------------------------------


def levers(session: Session, n: int | None = None) -> dict:
    v = session.version(n)
    report = score_resume(v["text"], session.jd_profile, session.jd_text)
    result = whatif_analyze(report, session.jd_profile, declined=set(session.declined),
                            targets=(config.TARGET_MIN, config.TARGET_MAX))
    return result.to_dict()


def confirm(session: Session, items: list[dict]) -> dict:
    """items: [{keyword, confirmed: bool, context: str}] from the score-levers step."""
    profile_terms = {kw["term"].lower(): kw["term"] for kw in (session.profile or {}).get("keywords", [])}
    changed = 0
    for item in items or []:
        raw = (item.get("keyword") or "").strip()
        if not raw:
            continue
        term = profile_terms.get(raw.lower(), raw)
        context = (item.get("context") or "").strip()
        session.confirmed = [c for c in session.confirmed if c.lower() != term.lower()]
        session.declined = [d for d in session.declined if d.lower() != term.lower()]
        session.facts = [f for f in session.facts if not (f["kind"] == "confirmed_skill" and f["keyword"].lower() == term.lower())]
        if item.get("confirmed"):
            session.confirmed.append(term)
            session.facts.append({
                "id": uuid.uuid4().hex[:8], "kind": "confirmed_skill", "keyword": term,
                "answer": context or f"Candidate confirms real experience with {term}.", "applied": False,
            })
        else:
            session.declined.append(term)
        changed += 1
    STORE.save(session)
    return {"changed": changed, "confirmed": session.confirmed, "declined": session.declined}


def revert(session: Session, n: int) -> dict:
    session.version(n)
    session.current = n
    STORE.save(session)
    return session_state(session)


def manual_edit(session: Session, text: str) -> dict:
    """User edited the preview text directly; score it as a new version."""
    text = text.strip()
    if len(text) < 100:
        raise ValueError("Edited resume is too short.")
    session.versions = session.versions[: session.current + 1]
    n = len(session.versions)
    version = {
        "n": n, "label": "Your edit", "phase": "manual", "text": text, "report": score_text(session, text),
        "changes": [], "keywords_added": [], "keywords_skipped": [], "questions": [], "flags": [],
        "summary": "Edited by you in the preview.", "instructions": "", "created_at": time.time(),
    }
    session.versions.append(version)
    session.current = n
    # Text the user typed themselves counts as a verified source for the honesty checks.
    session.manual_sources.append(text)
    version["flags"] = compute_flags(session, text)
    STORE.save(session)
    return public_version(session, n)


# --- LaTeX ----------------------------------------------------------------------------------------


def tailor_tex(session: Session, tex_text: str, progress: ProgressFn) -> dict:
    if "\\begin{document}" not in tex_text:
        raise ValueError("That doesn't look like a full .tex file (no \\begin{document}). Paste the whole file from Overleaf.")
    v = session.version()
    progress("Rewriting your .tex with the tailored content (keeping your template intact)...")
    edited, notes = llm.edit_latex(original_tex=tex_text, tailored_resume=v["text"], facts=session.facts)
    edited, restored = guards.restore_preamble(tex_text, edited)
    if restored:
        notes.insert(0, "The AI touched your preamble, so the original preamble was restored automatically.")
    progress("Validating LaTeX and re-scoring the result...")
    validation = guards.validate_latex(edited, tex_text)
    plain = latex_to_text(edited)
    report = score_text(session, plain)
    # Your own .tex is a trusted source too (it may hold details the uploaded resume didn't).
    flags = compute_flags(session, plain, extra_sources=[latex_to_text(tex_text)])
    diff = line_diff(tex_text, edited)
    session.tex = {
        "original": tex_text,
        "edited": edited,
        "notes": notes,
        "validation": validation,
        "report": report,
        "flags": flags,
        "diff_stats": diff_stats(diff),
        "version": v["n"],
        "markdown_score": v["report"]["score"],
        "created_at": time.time(),
    }
    STORE.save(session)
    return dict(session.tex, diff=diff)
