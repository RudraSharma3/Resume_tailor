import json

from fastapi.testclient import TestClient

from resume_tailor.server import app


def sse(response):
    events = []
    for chunk in response.text.split("\n\n"):
        for line in chunk.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def create(client, resume_text, jd_text):
    r = client.post("/api/session", data={"resume_text": resume_text, "jd_text": jd_text})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_full_flow(fake_llm, resume_text, jd_text, tex_text):
    client = TestClient(app)
    sid = create(client, resume_text, jd_text)

    events = sse(client.post(f"/api/session/{sid}/analyze"))
    done = events[-1]
    assert done["event"] == "done", events
    state = done["state"]
    v0 = state["version"]["report"]["score"]
    assert state["reviews"]["0"]["match_score"] == 58
    assert state["levers"]["top5"]

    # Auto-tailor: alignment then impact, each iteration scored and streamed.
    events = sse(client.post(f"/api/session/{sid}/iterate", json={"mode": "auto", "max_iterations": 2}))
    iterations = [e for e in events if e["event"] == "iteration"]
    assert [e["version"]["phase"] for e in iterations] == ["alignment", "impact"]
    scores = [v0] + [e["version"]["report"]["score"] for e in iterations]
    assert scores[1] > scores[0] and scores[2] >= scores[1], scores
    final = events[-1]
    assert final["event"] == "done"
    assert final["state"]["timeline"][-1]["score"] == scores[-1]
    v1 = iterations[0]["version"]
    assert v1["diff"] and v1["diff_stats"]["changed"] >= 1
    assert v1["questions"][0]["id"] == "q1-0"
    assert not iterations[1]["version"]["flags"], iterations[1]["version"]["flags"]

    # Answer a metric question; it becomes a verified fact used by the next pass.
    events = sse(client.post(f"/api/session/{sid}/iterate", json={"mode": "single", "answers": [{"id": "q1-0", "answer": "15 jobs"}]}))
    st = client.get(f"/api/session/{sid}").json()
    assert any(f["kind"] == "metric_answer" and f["answer"] == "15 jobs" for f in st["facts"])
    assert fake_llm["tailor"][-1] == "polish"

    # Confirm one skill, decline another, then apply.
    r = client.post(f"/api/session/{sid}/confirm", json={"items": [
        {"keyword": "Kubernetes", "confirmed": True, "context": "Ran our staging cluster at Brightline"},
        {"keyword": "Terraform", "confirmed": False},
    ]})
    assert r.status_code == 200
    assert all(lv["keyword"] != "Terraform" for lv in r.json()["levers"]["levers"])
    events = sse(client.post(f"/api/session/{sid}/iterate", json={"mode": "single"}))
    applied = [e for e in events if e["event"] == "iteration"][0]["version"]
    assert applied["phase"] == "apply_confirmed"
    assert not [f for f in applied["flags"] if f["value"] == "Kubernetes"]
    assert any(h["term"] == "Kubernetes" for h in applied["report"]["keywords"]["matched"])

    # LaTeX step
    events = sse(client.post(f"/api/session/{sid}/tex", data={"tex_text": tex_text}))
    tex = events[-1]["tex"]
    assert "Migrated cron jobs" in tex["edited"]
    assert all(c["ok"] for c in tex["validation"]), tex["validation"]
    r = client.get(f"/api/session/{sid}/tex/download")
    assert r.status_code == 200 and "\\begin{document}" in r.text
    assert "attachment" in r.headers["content-disposition"]


def test_unverified_number_is_flagged_and_fed_back(fake_llm, resume_text, jd_text, monkeypatch):
    from resume_tailor import llm

    original = llm.tailor_iteration
    monkeypatch.setattr(llm, "tailor_iteration", lambda **kw: original(**kw, inject_fake=True))
    client = TestClient(app)
    sid = create(client, resume_text, jd_text)
    sse(client.post(f"/api/session/{sid}/analyze"))
    events = sse(client.post(f"/api/session/{sid}/iterate", json={"mode": "single", "phase": "impact"}))
    version = [e for e in events if e["event"] == "iteration"][0]["version"]
    assert any(f["kind"] == "unverified_number" and f["value"] == "47%" for f in version["flags"])

    captured = {}

    def spy(**kw):
        captured.update(kw)
        return original(**kw)

    monkeypatch.setattr(llm, "tailor_iteration", spy)
    sse(client.post(f"/api/session/{sid}/iterate", json={"mode": "single"}))
    assert any("47%" in issue for issue in captured["issues"])


def test_revert_and_manual_edit(fake_llm, resume_text, jd_text):
    client = TestClient(app)
    sid = create(client, resume_text, jd_text)
    sse(client.post(f"/api/session/{sid}/analyze"))
    sse(client.post(f"/api/session/{sid}/iterate", json={"mode": "single"}))
    assert client.post(f"/api/session/{sid}/revert", json={"version": 0}).json()["current"] == 0
    text = client.get(f"/api/session/{sid}/version/0").json()["text"] + "\n- Mentored 2 interns on testing practices."
    r = client.post(f"/api/session/{sid}/edit", json={"text": text})
    assert r.status_code == 200
    v = r.json()["version"]
    assert v["n"] == 1 and v["label"] == "Your edit" and not v["flags"]
    assert len(r.json()["timeline"]) == 2


def test_offline_mode_scores_without_ai(resume_text, jd_text, monkeypatch):
    from resume_tailor import llm

    monkeypatch.setattr(llm, "available", lambda: False)
    client = TestClient(app)
    sid = create(client, resume_text, jd_text)
    done = sse(client.post(f"/api/session/{sid}/analyze"))[-1]
    assert done["state"]["profile"]["source"] == "heuristic"
    assert 0 < done["state"]["version"]["report"]["score"] < 100
    events = sse(client.post(f"/api/session/{sid}/iterate", json={"mode": "single"}))
    assert events[-1]["event"] == "error"
    assert "API key" in events[-1]["message"]


def test_bad_inputs():
    client = TestClient(app)
    r = client.post("/api/session", data={"resume_text": "too short", "jd_text": "also short"})
    assert r.status_code == 400 and "error" in r.json()
    assert client.get("/api/session/" + "0" * 32).status_code == 404
    assert client.get("/api/session/not-an-id").status_code == 404
