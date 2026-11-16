from resume_tailor.ats import score_resume
from resume_tailor.guards import (
    evidence_supported,
    restore_preamble,
    skill_flags,
    unverified_numbers,
    validate_latex,
)
from resume_tailor.keywords import heuristic_profile
from resume_tailor.whatif import analyze


def test_lever_projection_matches_real_rescore(resume_text, jd_text):
    profile = heuristic_profile(jd_text)
    report = score_resume(resume_text, profile, jd_text)
    result = analyze(report, profile)
    lever = next(lv for lv in result.levers if lv.keyword == "Kubernetes")
    edited = resume_text.replace(
        "• Created a Slack bot that answers stock-level questions from a Postgres database.",
        "• Created a Slack bot on Kubernetes that answers stock-level questions from a Postgres database.",
    )
    actual = score_resume(edited, profile, jd_text).score
    assert abs(actual - lever.projected) < 0.6, (actual, lever.projected)


def test_levers_sorted_and_bundles(resume_text, jd_text):
    profile = heuristic_profile(jd_text)
    result = analyze(score_resume(resume_text, profile, jd_text), profile).to_dict()
    deltas = [lv["delta"] for lv in result["levers"]]
    assert deltas == sorted(deltas, reverse=True)
    assert len(result["top5"]) == 5
    assert {b["target"] for b in result["bundles"]} == {82.0, 85.0}


def test_declined_keywords_are_not_suggested(resume_text, jd_text):
    profile = heuristic_profile(jd_text)
    result = analyze(score_resume(resume_text, profile, jd_text), profile, declined={"kubernetes"})
    assert all(lv.keyword != "Kubernetes" for lv in result.levers)


def test_unverified_numbers():
    src = ["Cut load time from 3.2s to 1.1s. Supported 10,000 users. Team of 4."]
    ok = "Cut page load 3.2s to 1.1s for 10k users with a team of 4 since 2021."
    assert unverified_numbers(ok, src) == []
    bad = unverified_numbers("Improved throughput by 47% for 10k users.", src)
    assert [f.value for f in bad] == ["47%"]
    # A dollar amount that happens to look like a year is still checked.
    assert [f.value for f in unverified_numbers("Saved $2,000 a month since 2019.", src)] == ["$2,000"]


def test_evidence_quote_check():
    sources = ["- Designed a PostgreSQL schema for the booking system."]
    assert evidence_supported("Designed a PostgreSQL schema for the booking system", sources)
    assert not evidence_supported("Managed Kubernetes clusters in production", sources)


def test_skill_flags_respect_confirmations(jd_text):
    profile = heuristic_profile(jd_text)
    original = "- Built Flask APIs"
    new = "- Built Flask APIs deployed on Kubernetes"
    flags = skill_flags(new, original, profile, {}, [original], set(), set())
    assert any(f.value == "Kubernetes" and f.kind == "unverified_skill" for f in flags)
    assert not skill_flags(new, original, profile, {}, [original], {"Kubernetes"}, set())
    declined = skill_flags(new, original, profile, {}, [original], set(), {"Kubernetes"})
    assert declined[0].kind == "declined_skill"


def test_latex_validation(tex_text):
    assert all(c["ok"] for c in validate_latex(tex_text, tex_text)), validate_latex(tex_text, tex_text)
    broken = tex_text.replace("reduced cost by 30\\%", "reduced cost by 30%").replace("\\end{itemize}\n\\end{document}", "\\end{document}")
    results = {c["check"]: c for c in validate_latex(broken, tex_text)}
    assert not results["Special characters escaped (% & $ # _)"]["ok"]
    assert not results["Matching \\begin/\\end environments"]["ok"]


def test_restore_preamble(tex_text):
    tampered = tex_text.replace("\\usepackage{titlesec}", "\\usepackage{titlesec}\\usepackage{fontawesome5}")
    fixed, restored = restore_preamble(tex_text, tampered)
    assert restored and fixed == tex_text
