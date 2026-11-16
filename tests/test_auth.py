"""Accounts, admin approval, isolation between users, CSRF, rate limits and the daily AI cap."""

import json

import pytest
from fastapi.testclient import TestClient

from resume_tailor import auth
from resume_tailor.server import app

HEADERS = {"X-Requested-With": "fetch"}
ADMIN = ("owner@example.com", "a-strong-admin-pass")


@pytest.fixture()
def accounts(monkeypatch):
    monkeypatch.delenv("RESUME_TAILOR_LOCAL", raising=False)
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN[0])
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN[1])
    auth.bootstrap_admin()


def client():
    return TestClient(app, headers=HEADERS)


def login(c, email, password):
    return c.post("/api/auth/login", json={"email": email, "password": password})


def admin_client():
    c = client()
    assert login(c, *ADMIN).status_code == 200
    return c


def sse(response):
    return [json.loads(line[6:]) for chunk in response.text.split("\n\n") for line in chunk.split("\n") if line.startswith("data: ")]


def register_and_approve(admin, email, name="Friend", password="friend-password-1"):
    c = client()
    assert c.post("/api/auth/register", json={"email": email, "name": name, "password": password, "note": "hi"}).status_code == 200
    uid = next(u["id"] for u in admin.get("/api/admin/users").json()["users"] if u["email"] == email)
    assert admin.post(f"/api/admin/users/{uid}/status", json={"status": "approved"}).status_code == 200
    assert login(c, email, password).status_code == 200
    return c, uid


def test_everything_requires_sign_in(accounts):
    c = client()
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/sessions").status_code == 401
    assert c.post("/api/session", data={"resume_text": "x", "jd_text": "y"}).status_code == 401
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/api/health").json() == {"ok": True}


def test_new_users_wait_for_approval(accounts):
    admin = admin_client()
    c = client()
    r = c.post("/api/auth/register", json={"email": "Friend@Example.com", "name": "Friend", "password": "friend-password-1"})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    r = login(c, "friend@example.com", "friend-password-1")
    assert r.status_code == 403 and "waiting" in r.json()["error"]
    assert admin.get("/api/me").json()["pending_requests"] == 1

    users = admin.get("/api/admin/users").json()["users"]
    uid = next(u["id"] for u in users if u["email"] == "friend@example.com")
    admin.post(f"/api/admin/users/{uid}/status", json={"status": "approved"})
    assert login(c, "friend@example.com", "friend-password-1").status_code == 200
    me = c.get("/api/me").json()
    assert me["user"]["email"] == "friend@example.com" and not me["user"]["is_admin"]
    assert c.get("/api/admin/users").status_code == 403
    assert c.get("/admin", follow_redirects=False).headers["location"] == "/"


def test_turning_off_access_signs_user_out(accounts):
    admin = admin_client()
    c, uid = register_and_approve(admin, "friend@example.com")
    assert c.get("/api/me").status_code == 200
    admin.post(f"/api/admin/users/{uid}/status", json={"status": "blocked"})
    assert c.get("/api/me").status_code == 401
    r = login(c, "friend@example.com", "friend-password-1")
    assert r.status_code == 403 and "turned off" in r.json()["error"]


def test_users_cannot_see_each_others_resumes(accounts, fake_llm, resume_text, jd_text):
    admin = admin_client()
    alice, _ = register_and_approve(admin, "alice@example.com")
    bob, _ = register_and_approve(admin, "bob@example.com")
    sid = alice.post("/api/session", data={"resume_text": resume_text, "jd_text": jd_text}).json()["id"]
    assert alice.get(f"/api/session/{sid}").status_code == 200
    assert [s["id"] for s in alice.get("/api/sessions").json()["sessions"]] == [sid]
    for c in (bob, admin):
        assert c.get(f"/api/session/{sid}").status_code == 404
        assert c.post(f"/api/session/{sid}/analyze").status_code == 404
        assert c.get(f"/api/session/{sid}/resume/download").status_code == 404
        assert c.get("/api/sessions").json()["sessions"] == []


def test_csrf_header_required(accounts):
    c = TestClient(app)  # no X-Requested-With header
    r = c.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 403


def test_wrong_password_rate_limited(accounts):
    c = client()
    for _ in range(6):
        assert login(c, ADMIN[0], "wrong-password").status_code == 401
    r = login(c, ADMIN[0], ADMIN[1])
    assert r.status_code == 429


def test_daily_ai_limit(accounts, fake_llm, resume_text, jd_text, monkeypatch):
    from resume_tailor import llm

    monkeypatch.setenv("AI_DAILY_LIMIT", "2")

    # Route the fake LLM through llm._call's guard so the limit is exercised.
    def guarded(**kw):
        guard = llm.call_guard.get()
        if guard:
            guard()
        return fake_llm_tailor(**kw)

    fake_llm_tailor = llm.tailor_iteration
    monkeypatch.setattr(llm, "tailor_iteration", guarded)
    admin = admin_client()
    c, _ = register_and_approve(admin, "friend@example.com")
    sid = c.post("/api/session", data={"resume_text": resume_text, "jd_text": jd_text}).json()["id"]
    sse(c.post(f"/api/session/{sid}/analyze"))
    for _ in range(2):
        assert sse(c.post(f"/api/session/{sid}/iterate", json={"mode": "single"}))[-1]["event"] == "done"
    last = sse(c.post(f"/api/session/{sid}/iterate", json={"mode": "single"}))[-1]
    assert last["event"] == "error" and "AI steps" in last["message"]
    assert c.get("/api/me").json()["ai_calls_today"] == 2
    # The admin is not limited.
    sid2 = admin.post("/api/session", data={"resume_text": resume_text, "jd_text": jd_text}).json()["id"]
    sse(admin.post(f"/api/session/{sid2}/analyze"))
    for _ in range(3):
        assert sse(admin.post(f"/api/session/{sid2}/iterate", json={"mode": "single"}))[-1]["event"] == "done"


def test_fails_closed_without_admin_config(monkeypatch):
    monkeypatch.delenv("RESUME_TAILOR_LOCAL", raising=False)
    c = client()
    r = c.get("/api/me")
    assert r.status_code == 503 and "ADMIN_EMAIL" in r.json()["error"]
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"
    monkeypatch.setenv("ADMIN_EMAIL", "a@b.co")
    monkeypatch.setenv("ADMIN_PASSWORD", "short")
    assert "12 characters" in c.get("/api/me").json()["error"]


def test_admin_password_change_and_delete_user(accounts, monkeypatch):
    admin = admin_client()
    _c, uid = register_and_approve(admin, "friend@example.com")
    assert admin.delete(f"/api/admin/users/{uid}").status_code == 200
    assert all(u["id"] != uid for u in admin.get("/api/admin/users").json()["users"])
    monkeypatch.setenv("ADMIN_PASSWORD", "a-new-admin-password")
    auth.bootstrap_admin()
    assert admin.get("/api/me").status_code == 401  # old sessions revoked
    assert login(client(), ADMIN[0], ADMIN[1]).status_code == 401
    assert login(client(), ADMIN[0], "a-new-admin-password").status_code == 200


def test_password_hashing():
    h = auth.hash_password("correct horse battery")
    assert h.startswith("scrypt$") and "correct" not in h
    assert auth.verify_password("correct horse battery", h)
    assert not auth.verify_password("wrong", h)
