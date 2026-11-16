"""Start Resume Tailor: python run.py  (add --no-browser to skip opening a tab)."""

from __future__ import annotations

import os
import sys
import threading
import webbrowser

import uvicorn

from resume_tailor import config


def main() -> None:
    url = f"http://{config.HOST}:{config.PORT}"
    if config.HOST in {"127.0.0.1", "localhost", "::1"}:
        # Only this computer can reach the app, so it runs as a single user with no sign-in
        # (unless ADMIN_EMAIL is set, which turns accounts on).
        os.environ.setdefault("RESUME_TAILOR_LOCAL", "1")
    print(f"\n  Resume Tailor running at {url}")
    from resume_tailor import llm

    print(f"  AI: {llm.provider_label()} ({llm.model_name()}) | "
          f"{'enabled' if llm.available() else 'DISABLED (set ANTHROPIC_API_KEY or GEMINI_API_KEY)'}\n")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("resume_tailor.server:app", host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
