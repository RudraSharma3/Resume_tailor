"""FastAPI app: sign-in with admin approval, JSON + server-sent-event endpoints, and the UI pages."""

from __future__ import annotations

import contextvars
import json
import logging
import queue
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, auth, config, engine, llm
from .db import get_db
from .parsers import ParseError, extract_text

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 8 * 1024 * 1024
log = logging.getLogger("resume_tailor")


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
    db = get_db()
    auth.bootstrap_admin()
    problem = auth.config_problem()
    log.warning("Resume Tailor %s | storage: %s | %s", __version__, db.kind,
                "single-user local mode" if auth.local_mode() else (problem or f"admin: {auth.admin_email()}"))
    yield


app = FastAPI(title="Resume Tailor", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


@app.middleware("http")
async def security(request: Request, call_next):  # type: ignore[no-untyped-def]
    # CSRF: state-changing API calls must carry a header that cross-site forms can't set.
    if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.headers.get("x-requested-with") != "fetch":
            return JSONResponse({"error": "Missing request header."}, status_code=403)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# --- Auth helpers ------------------------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def optional_user(request: Request) -> dict | None:
    return auth.user_for_token(request.cookies.get(auth.COOKIE))


def require_user(request: Request) -> dict:
    problem = auth.config_problem()
    if problem:
        raise HTTPException(503, problem)
    user = optional_user(request)
    if not user:
        raise HTTPException(401, "Please sign in.")
    return user


def require_admin(user: dict = Depends(require_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(403, "Admins only.")
    return user


def _ai_guard(user: dict) -> Callable[[], None]:
    def guard() -> None:
        try:
            auth.count_ai_call(user)
        except auth.LimitError as exc:
            raise llm.LLMError(str(exc)) from exc
    return guard


def _session(sid: str, user: dict) -> engine.Session:
    try:
        session = engine.STORE.get(sid)
    except KeyError as exc:
        raise HTTPException(404, "Session not found - start a new analysis.") from exc
    if session.owner_id != user["id"]:
        raise HTTPException(404, "Session not found - start a new analysis.")
    return session


def _locked(sid: str) -> threading.Lock:
    lock = engine.STORE.lock(sid)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "Another step is still running for this resume. Wait for it to finish.")
    return lock


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream(sid: str, user: dict, work: Callable[[Callable[[dict], None]], Any]) -> StreamingResponse:
    """Run `work(emit)` in a thread and stream every emitted event as SSE, with keep-alives."""
    events: queue.Queue = queue.Queue()
    lock = engine.STORE.lock(sid)

    def runner() -> None:
        if not lock.acquire(blocking=False):
            events.put({"event": "error", "message": "Another step is still running for this resume. Wait for it to finish."})
            events.put(None)
            return
        llm.call_guard.set(_ai_guard(user))
        try:
            work(events.put)
        except (llm.LLMError, ValueError, ParseError) as exc:
            if isinstance(exc, llm.LLMError):
                log.warning("AI step failed: %s | cause: %r", exc, exc.__cause__)  # visible in the host's logs
            events.put({"event": "error", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001 - surface unexpected failures to the UI
            log.exception("Step failed")
            events.put({"event": "error", "message": f"Unexpected error: {exc}"})
        finally:
            lock.release()
            events.put(None)

    threading.Thread(target=contextvars.copy_context().run, args=(runner,), daemon=True).start()

    def gen() -> Iterator[str]:
        while True:
            try:
                item = events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if item is None:
                break
            yield _sse(item)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _read(upload: UploadFile | None) -> tuple[str | None, bytes | None]:
    if upload is None or not upload.filename:
        return None, None
    data = await upload.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File is larger than 8 MB.")
    return upload.filename, data


# --- Pages -------------------------------------------------------------------------------------------------


@app.get("/")
def index(request: Request) -> Response:
    if auth.config_problem() or not optional_user(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC / "index.html")


@app.get("/login")
def login_page(request: Request) -> Response:
    if auth.local_mode() or optional_user(request):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "login.html")


@app.get("/admin")
def admin_page(request: Request) -> Response:
    user = optional_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not user.get("is_admin"):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "admin.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# --- Auth ----------------------------------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: str = Field(max_length=320)
    name: str = Field(max_length=200)
    password: str = Field(max_length=200)
    note: str = Field("", max_length=1000)


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


@app.get("/api/auth/config")
def auth_config() -> dict:
    return {"problem": auth.config_problem(), "allow_signup": auth.allow_signup(), "local": auth.local_mode()}


@app.post("/api/auth/register")
def register(body: RegisterRequest, request: Request) -> dict:
    problem = auth.config_problem()
    if problem:
        raise HTTPException(503, problem)
    try:
        return auth.register(body.email, body.name, body.password, body.note, _client_ip(request))
    except auth.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@app.post("/api/auth/login")
def login(body: LoginRequest, request: Request) -> Response:
    problem = auth.config_problem()
    if problem:
        raise HTTPException(503, problem)
    try:
        user, token = auth.login(body.email, body.password, _client_ip(request))
    except auth.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    response = JSONResponse({"user": auth.public_user(user)})
    response.set_cookie(
        auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
        secure=request.url.scheme == "https", samesite="lax", path="/",
    )
    return response


@app.post("/api/auth/logout")
def logout(request: Request) -> Response:
    auth.logout(request.cookies.get(auth.COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


@app.get("/api/me")
def me(user: dict = Depends(require_user)) -> dict:
    limit = auth.ai_daily_limit()
    return {
        "user": auth.public_user(user),
        "local": auth.local_mode(),
        "ai_available": llm.available(),
        "model": llm.model_name(),
        "provider": llm.provider_label(),
        "privacy_note": llm.privacy_note(),
        "target": {"min": config.TARGET_MIN, "max": config.TARGET_MAX},
        "ai_calls_today": auth.usage_today(user) if user["id"] != "local" else 0,
        "ai_daily_limit": 0 if user.get("is_admin") else limit,
        "pending_requests": auth.pending_count() if user.get("is_admin") and not auth.local_mode() else 0,
    }


# --- Admin -----------------------------------------------------------------------------------------------------


class StatusRequest(BaseModel):
    status: str


@app.get("/api/admin/users")
def admin_users(_admin: dict = Depends(require_admin)) -> dict:
    return {"users": auth.list_users(), "ai_daily_limit": auth.ai_daily_limit(), "allow_signup": auth.allow_signup()}


@app.post("/api/admin/users/{user_id}/status")
def admin_set_status(user_id: str, body: StatusRequest, _admin: dict = Depends(require_admin)) -> dict:
    try:
        return {"user": auth.set_status(user_id, body.status)}
    except auth.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@app.post("/api/admin/ai-check")
def admin_ai_check(_admin: dict = Depends(require_admin)) -> dict:
    if not llm.available():
        return {"ok": False, "provider": llm.provider_label(), "model": llm.model_name(),
                "error": "No API key set for this provider (ANTHROPIC_API_KEY or GEMINI_API_KEY)."}
    return llm.check()


@app.delete("/api/admin/users/{user_id}")
def admin_delete(user_id: str, _admin: dict = Depends(require_admin)) -> dict:
    try:
        auth.delete_user(user_id)
    except auth.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return {"ok": True}


# --- Resume sessions -------------------------------------------------------------------------------------------


@app.get("/api/sessions")
def my_sessions(user: dict = Depends(require_user)) -> dict:
    return {"sessions": engine.STORE.list_for(user["id"])}


@app.post("/api/session")
async def create_session(
    resume_file: UploadFile | None = File(None),
    resume_text: str | None = Form(None),
    jd_file: UploadFile | None = File(None),
    jd_text: str | None = Form(None),
    user: dict = Depends(require_user),
) -> dict:
    r_name, r_bytes = await _read(resume_file)
    j_name, j_bytes = await _read(jd_file)
    try:
        session = engine.create_session(r_name, r_bytes, resume_text, j_name, j_bytes, jd_text, owner_id=user["id"])
    except (ParseError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": session.id, "resume_preview": session.resume_raw[:1500], "has_tex": bool(session.resume_tex)}


@app.get("/api/session/{sid}")
def get_session(sid: str, user: dict = Depends(require_user)) -> dict:
    session = _session(sid, user)
    state = engine.session_state(session)
    if session.versions:
        state["levers"] = engine.levers(session)
    return state


@app.delete("/api/session/{sid}")
def delete_session(sid: str, user: dict = Depends(require_user)) -> dict:
    _session(sid, user)
    lock = _locked(sid)
    try:
        engine.STORE.delete(sid)
    finally:
        lock.release()
    return {"ok": True}


@app.post("/api/session/{sid}/analyze")
def analyze(sid: str, user: dict = Depends(require_user)) -> StreamingResponse:
    session = _session(sid, user)

    def work(emit: Callable[[dict], None]) -> None:
        state = engine.analyze(session, lambda msg: emit({"event": "status", "message": msg}))
        state["levers"] = engine.levers(session)
        emit({"event": "done", "state": state})

    return _stream(sid, user, work)


class IterateRequest(BaseModel):
    mode: str = Field("single", pattern="^(single|auto)$")
    instructions: str = Field("", max_length=4000)
    answers: list[dict] = []
    note: str = Field("", max_length=4000)
    phase: str | None = None
    max_iterations: int = config.MAX_AUTO_ITERATIONS


@app.post("/api/session/{sid}/iterate")
def iterate(sid: str, body: IterateRequest, user: dict = Depends(require_user)) -> StreamingResponse:
    session = _session(sid, user)

    def work(emit: Callable[[dict], None]) -> None:
        for event in engine.tailor_events(
            session, mode=body.mode, instructions=body.instructions, answers=body.answers,
            note=body.note, phase=body.phase, max_iterations=body.max_iterations,
        ):
            emit(event)

    return _stream(sid, user, work)


@app.get("/api/session/{sid}/version/{n}")
def get_version(sid: str, n: int, compare: str = "prev", user: dict = Depends(require_user)) -> dict:
    session = _session(sid, user)
    try:
        return engine.public_version(session, n, compare)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class RevertRequest(BaseModel):
    version: int


@app.post("/api/session/{sid}/revert")
def revert(sid: str, body: RevertRequest, user: dict = Depends(require_user)) -> dict:
    session = _session(sid, user)
    lock = _locked(sid)
    try:
        state = engine.revert(session, body.version)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        lock.release()
    state["levers"] = engine.levers(session)
    return state


class EditRequest(BaseModel):
    text: str = Field(max_length=60000)


@app.post("/api/session/{sid}/edit")
def manual_edit(sid: str, body: EditRequest, user: dict = Depends(require_user)) -> dict:
    session = _session(sid, user)
    lock = _locked(sid)
    try:
        version = engine.manual_edit(session, body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        lock.release()
    return {"version": version, "timeline": engine.timeline(session), "levers": engine.levers(session)}


@app.post("/api/session/{sid}/recruiter")
def recruiter(sid: str, version: int | None = None, user: dict = Depends(require_user)) -> StreamingResponse:
    session = _session(sid, user)

    def work(emit: Callable[[dict], None]) -> None:
        emit({"event": "status", "message": "Senior recruiter is reviewing this version..."})
        review = engine.recruiter(session, version)
        emit({"event": "done", "review": review, "version": session.version(version)["n"], "timeline": engine.timeline(session)})

    return _stream(sid, user, work)


@app.get("/api/session/{sid}/levers")
def get_levers(sid: str, version: int | None = None, user: dict = Depends(require_user)) -> dict:
    return engine.levers(_session(sid, user), version)


class ConfirmRequest(BaseModel):
    items: list[dict]


@app.post("/api/session/{sid}/confirm")
def confirm(sid: str, body: ConfirmRequest, user: dict = Depends(require_user)) -> dict:
    session = _session(sid, user)
    lock = _locked(sid)
    try:
        result = engine.confirm(session, body.items)
    finally:
        lock.release()
    result["levers"] = engine.levers(session)
    return result


# --- LaTeX --------------------------------------------------------------------------------------------------


@app.post("/api/session/{sid}/tex")
async def tailor_tex(
    sid: str, tex_file: UploadFile | None = File(None), tex_text: str | None = Form(None), user: dict = Depends(require_user),
) -> StreamingResponse:
    session = _session(sid, user)
    name, data = await _read(tex_file)
    if data:
        if not (name or "").lower().endswith(".tex"):
            raise HTTPException(400, "Upload a .tex file.")
        tex = data.decode("utf-8", errors="replace")
    elif tex_text and tex_text.strip():
        tex = tex_text
    elif session.resume_tex:
        tex = session.resume_tex
    else:
        raise HTTPException(400, "Upload your .tex file or paste its contents.")

    def work(emit: Callable[[dict], None]) -> None:
        result = engine.tailor_tex(session, tex, lambda msg: emit({"event": "status", "message": msg}))
        emit({"event": "done", "tex": result})

    return _stream(sid, user, work)


@app.get("/api/session/{sid}/tex/download")
def download_tex(sid: str, user: dict = Depends(require_user)) -> Response:
    session = _session(sid, user)
    if not session.tex:
        raise HTTPException(404, "No tailored .tex yet.")
    return Response(
        session.tex["edited"], media_type="application/x-tex",
        headers={"Content-Disposition": 'attachment; filename="resume_tailored.tex"'},
    )


@app.get("/api/session/{sid}/resume/download")
def download_resume(sid: str, version: int | None = None, user: dict = Depends(require_user)) -> Response:
    session = _session(sid, user)
    try:
        v = session.version(version)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(
        v["text"], media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="resume_v{v["n"]}.md"'},
    )


@app.post("/api/extract")
async def extract(file: UploadFile = File(...), _user: dict = Depends(require_user)) -> dict:
    name, data = await _read(file)
    try:
        return {"text": extract_text(name or "", data or b"")}
    except ParseError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
