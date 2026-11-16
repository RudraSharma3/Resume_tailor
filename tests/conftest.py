"""Shared fixtures. The `fake_llm` fixture replaces every Claude call with deterministic stand-ins."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def resume_text() -> str:
    return (FIXTURES / "sample_resume.txt").read_text()


@pytest.fixture()
def jd_text() -> str:
    return (FIXTURES / "sample_jd.txt").read_text()


@pytest.fixture()
def tex_text() -> str:
    return (FIXTURES / "sample_resume.tex").read_text()


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    from resume_tailor import config, engine

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    (tmp_path / "sessions").mkdir()
    monkeypatch.setattr(engine, "STORE", engine.SessionStore())
    yield


def _to_markdown(raw: str) -> str:
    out = []
    for i, line in enumerate(raw.split("\n")):
        s = line.strip()
        if i == 0:
            out.append(f"# {s}")
        elif s.isupper() and len(s) < 30:
            out.append(f"## {s.title()}")
        elif "|" in s and re.search(r"\d{4}", s) and not s.startswith("•"):
            out.append(f"### {s}")
        elif s.startswith("•"):
            out.append("- " + s[1:].strip())
        else:
            out.append(s)
    return "\n".join(out)


@pytest.fixture()
def fake_llm(monkeypatch):
    from resume_tailor import llm
    from resume_tailor.keywords import heuristic_profile

    calls = {"tailor": [], "tex": 0, "review": 0}

    def extract_jd_profile(jd):
        p = heuristic_profile(jd)
        p.source = "ai"
        return p

    def normalize_resume(raw):
        return _to_markdown(raw)

    def recruiter_review(resume, jd):
        calls["review"] += 1
        return {
            "match_score": 58 + 6 * (calls["review"] - 1), "verdict": "Borderline", "would_shortlist": False,
            "summary": "Solid Python base, gaps in containers and streaming.",
            "top_missing_keywords": [
                {"keyword": k, "why_it_matters": "Listed in requirements", "where_to_add": "Experience", "evidence_in_resume": "none"}
                for k in ["Kubernetes", "Kafka", "CI/CD", "Microservices", "Terraform"]
            ],
            "strengths": ["Python/Flask"], "concerns": ["No Kubernetes"], "quick_wins": ["Quantify bullets"], "seniority_fit": "Mid-level fits",
        }

    def tailor_iteration(**kw):
        calls["tailor"].append(kw["phase"])
        text = kw["current_resume"]
        added, questions = [], []
        if kw["phase"] == "alignment":
            text = text.replace(
                "Software engineer with experience building web applications and internal tools.",
                "Backend Software Engineer with experience building Python REST APIs, SQL data models and internal tools.",
            )
            text = text.replace("- Worked on migrating cron jobs to AWS Lambda.", "- Migrated cron jobs to AWS Lambda, moving scheduled workloads to serverless.")
            text = text.replace("- Responsible for maintaining", "- Maintained")
            added = [{"keyword": "SQL", "evidence_quote": "Designed a PostgreSQL schema for the booking system."},
                     {"keyword": "Backend", "evidence_quote": "shipment tracking service written in Python and Flask"}]
            questions = [{"bullet": "Migrated cron jobs to AWS Lambda", "question": "How many cron jobs did you migrate?", "example_answer": "15 jobs"}]
        elif kw["phase"] == "impact":
            text = text.replace("- Helped onboard 4 new engineers", "- Onboarded 4 new engineers")
            text = text.replace("- Assisted with deployments", "- Ran deployments")
            text = text.replace("- Wrote unit tests with PyTest and set up GitHub Actions for automated test runs.",
                                "- Set up CI/CD with GitHub Actions and PyTest so every pull request runs the full test suite.")
            added = [{"keyword": "CI/CD", "evidence_quote": "set up GitHub Actions for automated test runs"}]
            if kw.get("inject_fake"):
                text += "\n- Improved throughput by 47%."
        elif kw["phase"] == "apply_confirmed":
            text = text.replace("- Created a Slack bot", "- Deployed with Docker and Kubernetes, created a Slack bot")
        else:
            text = text.replace("Comfortable across the stack", "Owns services end to end")
        return {
            "resume_markdown": text, "phase_summary": f"{kw['phase']} pass", "changes": [{"section": "x", "before": "a", "after": "b", "reason": "c"}],
            "keywords_added": added, "keywords_skipped": [{"keyword": "Kubernetes", "reason": "no evidence"}], "metric_questions": questions,
        }

    def edit_latex(*, original_tex, tailored_resume, facts):
        calls["tex"] += 1
        tex = original_tex.replace("Responsible for maintaining", "Maintained")
        tex = tex.replace("Worked on migrating cron jobs to AWS Lambda", "Migrated cron jobs to AWS Lambda")
        return tex, ["Updated two bullets"]

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "extract_jd_profile", extract_jd_profile)
    monkeypatch.setattr(llm, "normalize_resume", normalize_resume)
    monkeypatch.setattr(llm, "recruiter_review", recruiter_review)
    monkeypatch.setattr(llm, "tailor_iteration", tailor_iteration)
    monkeypatch.setattr(llm, "edit_latex", edit_latex)
    return calls
