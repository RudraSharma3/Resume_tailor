from resume_tailor.ats import has_metric, score_resume, starts_with_action_verb
from resume_tailor.keywords import JDProfile, Keyword, count_keyword, heuristic_profile


def test_score_is_deterministic_and_plausible(resume_text, jd_text):
    profile = heuristic_profile(jd_text)
    a = score_resume(resume_text, profile, jd_text)
    b = score_resume(resume_text, profile, jd_text)
    assert a.score == b.score
    assert 40 <= a.score <= 70, a.score  # untailored resume should not look "done"
    assert sum(c.max for c in a.components) == 100


def test_adding_evidence_backed_keywords_raises_score(resume_text, jd_text):
    profile = heuristic_profile(jd_text)
    base = score_resume(resume_text, profile, jd_text).score
    improved = resume_text.replace(
        "• Designed a PostgreSQL schema for the booking system.",
        "• Designed a PostgreSQL schema and tuned SQL queries for the booking system's REST APIs.",
    )
    assert score_resume(improved, profile, jd_text).score > base


def test_keyword_stuffing_is_penalized(resume_text, jd_text):
    profile = heuristic_profile(jd_text)
    stuffed = resume_text + "\n" + "Kubernetes " * 12
    report = score_resume(stuffed, profile, jd_text)
    penalty = next(c for c in report.components if c.id == "penalty")
    assert penalty.score < 0


def test_copying_jd_sentences_is_penalized(resume_text, jd_text):
    profile = heuristic_profile(jd_text)
    copied = resume_text + "\n• Design, build and operate high-throughput REST APIs and microservices in Python."
    report = score_resume(copied, profile, jd_text)
    penalty = next(c for c in report.components if c.id == "penalty")
    assert penalty.score < 0


def test_skills_only_keyword_gets_partial_credit():
    profile = JDProfile(job_title="Engineer", keywords=[Keyword("Docker", importance="required")])
    resume_ctx = "# A\na@b.co\n## Experience\n### Eng | Co | 2020 - 2022\n- Packaged services with Docker for 3 teams\n## Skills\nPython"
    resume_sk = "# A\na@b.co\n## Experience\n### Eng | Co | 2020 - 2022\n- Packaged services for 3 teams\n## Skills\nDocker"
    ctx = score_resume(resume_ctx, profile).to_dict()["keywords"]
    sk = score_resume(resume_sk, profile).to_dict()["keywords"]
    assert ctx["matched"] and not ctx["skills_only"]
    assert sk["skills_only"] and not sk["matched"]


def test_keyword_variants():
    assert count_keyword("Deployed on k8s clusters", Keyword("Kubernetes")) == 1
    assert count_keyword("Built NodeJS services", Keyword("Node.js")) == 1
    assert count_keyword("CI/CD pipelines", Keyword("CI/CD")) == 1
    assert count_keyword("Wrote C++ and C# code", Keyword("C#")) == 1
    assert count_keyword("JavaScript developer", Keyword("Java")) == 0
    assert count_keyword("go to market", Keyword("Go")) == 0  # short terms are case-sensitive
    assert count_keyword("Communicated results to execs", Keyword("Communication", category="soft_skill")) == 1


def test_metric_and_verb_detection():
    assert has_metric("Cut latency 40% by adding caching")
    assert has_metric("Saved $12k per month")
    assert has_metric("Served 3 regions")
    assert not has_metric("Joined the team in 2021")
    assert starts_with_action_verb("Led migration to AWS")
    assert starts_with_action_verb("Optimizing queries")
    assert starts_with_action_verb("Build and maintain APIs")
    assert not starts_with_action_verb("Responsible for APIs")
