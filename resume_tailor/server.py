"""FastAPI app: JSON + server-sent-event endpoints, and the single-page UI."""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any, Callable, Iterator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, config, engine, llm
from .parsers import ParseError, extract_text

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 8 * 1024 * 1024

app = FastAPI(title="Resume Tailor", version=__version__)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _session(sid: str) -> engine.Session:
    try:
        return engine.STORE.get(sid)
    except KeyError as exc:
        raise HTTPException(404, "Session not found - start a new analysis.") from exc


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream(sid: str, work: Callable[[Callable[[dict], None]], Any]) -> StreamingResponse:
    """Run `work(emit)` in a thread and stream every emitted event as SSE, with keep-alives."""
    events: queue.Queue = queue.Queue()
    lock = engine.STORE.lock(sid)

    def runner() -> None:
        if not lock.acquire(blocking=False):
            events.put({"event": "error", "message": "Another step is still running for this session. Wait for it to finish."})
            events.put(None)
            return
        try:
            work(events.put)
        except (llm.LLMError, ValueError, ParseError) as exc:
            events.put({"event": "error", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001 - surface unexpected failures to the UI
            events.put({"event": "error", "message": f"Unexpected error: {exc}"})
        finally:
            lock.release()
            events.put(None)

    threading.Thread(target=runner, daemon=True).start()

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


# --- Pages -------------------------------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "ai_available": llm.available(), "model": config.MODEL, "version": __version__,
            "target": {"min": config.TARGET_MIN, "max": config.TARGET_MAX}}


# --- Session lifecycle -------------------------------------------------------------------------------


@app.post("/api/session")
async def create_session(
    resume_file: UploadFile | None = File(None),
    resume_text: str | None = Form(None),
    jd_file: UploadFile | None = File(None),
    jd_text: str | None = Form(None),
) -> dict:
    r_name, r_bytes = await _read(resume_file)
    j_name, j_bytes = await _read(jd_file)
    try:
        session = engine.create_session(r_name, r_bytes, resume_text, j_name, j_bytes, jd_text)
    except (ParseError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": session.id, "resume_preview": session.resume_raw[:1500], "has_tex": bool(session.resume_tex)}


@app.get("/api/session/{sid}")
def get_session(sid: str) -> dict:
    session = _session(sid)
    state = engine.session_state(session)
    if session.versions:
        state["levers"] = engine.levers(session)
    return state


@app.post("/api/session/{sid}/analyze")
def analyze(sid: str) -> StreamingResponse:
    session = _session(sid)

    def work(emit: Callable[[dict], None]) -> None:
        state = engine.analyze(session, lambda msg: emit({"event": "status", "message": msg}))
        state["levers"] = engine.levers(session)
        emit({"event": "done", "state": state})

    return _stream(sid, work)


class IterateRequest(BaseModel):
    mode: str = Field("single", pattern="^(single|auto)$")
    instructions: str = ""
    answers: list[dict] = []
    note: str = ""
    phase: str | None = None
    max_iterations: int = config.MAX_AUTO_ITERATIONS


@app.post("/api/session/{sid}/iterate")
def iterate(sid: str, body: IterateRequest) -> StreamingResponse:
    session = _session(sid)

    def work(emit: Callable[[dict], None]) -> None:
        for event in engine.tailor_events(
            session, mode=body.mode, instructions=body.instructions, answers=body.answers,
            note=body.note, phase=body.phase, max_iterations=body.max_iterations,
        ):
            emit(event)

    return _stream(sid, work)


@app.get("/api/session/{sid}/version/{n}")
def get_version(sid: str, n: int, compare: str = "prev") -> dict:
    session = _session(sid)
    try:
        return engine.public_version(session, n, compare)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class RevertRequest(BaseModel):
    version: int


@app.post("/api/session/{sid}/revert")
def revert(sid: str, body: RevertRequest) -> dict:
    session = _session(sid)
    try:
        state = engine.revert(session, body.version)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    state["levers"] = engine.levers(session)
    return state


class EditRequest(BaseModel):
    text: str


@app.post("/api/session/{sid}/edit")
def manual_edit(sid: str, body: EditRequest) -> dict:
    session = _session(sid)
    try:
        version = engine.manual_edit(session, body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"version": version, "timeline": engine.timeline(session), "levers": engine.levers(session)}


@app.post("/api/session/{sid}/recruiter")
def recruiter(sid: str, version: int | None = None) -> StreamingResponse:
    session = _session(sid)

    def work(emit: Callable[[dict], None]) -> None:
        emit({"event": "status", "message": "Senior recruiter is reviewing this version..."})
        review = engine.recruiter(session, version)
        emit({"event": "done", "review": review, "version": session.version(version)["n"], "timeline": engine.timeline(session)})

    return _stream(sid, work)


@app.get("/api/session/{sid}/levers")
def get_levers(sid: str, version: int | None = None) -> dict:
    return engine.levers(_session(sid), version)


class ConfirmRequest(BaseModel):
    items: list[dict]


@app.post("/api/session/{sid}/confirm")
def confirm(sid: str, body: ConfirmRequest) -> dict:
    session = _session(sid)
    result = engine.confirm(session, body.items)
    result["levers"] = engine.levers(session)
    return result


# --- LaTeX -----------------------------------------------------------------------------------------


@app.post("/api/session/{sid}/tex")
async def tailor_tex(sid: str, tex_file: UploadFile | None = File(None), tex_text: str | None = Form(None)) -> StreamingResponse:
    session = _session(sid)
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

    return _stream(sid, work)


@app.get("/api/session/{sid}/tex/download")
def download_tex(sid: str) -> Response:
    session = _session(sid)
    if not session.tex:
        raise HTTPException(404, "No tailored .tex yet.")
    return Response(
        session.tex["edited"], media_type="application/x-tex",
        headers={"Content-Disposition": 'attachment; filename="resume_tailored.tex"'},
    )


@app.get("/api/session/{sid}/resume/download")
def download_resume(sid: str, version: int | None = None) -> Response:
    session = _session(sid)
    v = session.version(version)
    return Response(
        v["text"], media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="resume_v{v["n"]}.md"'},
    )


# --- Utility ---------------------------------------------------------------------------------------


@app.post("/api/extract")
async def extract(file: UploadFile = File(...)) -> dict:
    name, data = await _read(file)
    try:
        return {"text": extract_text(name or "", data or b"")}
    except ParseError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.exception_handler(HTTPException)
async def http_error(_request, exc: HTTPException) -> JSONResponse:  # type: ignore[no-untyped-def]
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
