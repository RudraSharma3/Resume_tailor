"""Start Resume Tailor: python run.py  (add --no-browser to skip opening a tab)."""

from __future__ import annotations

import sys
import threading
import webbrowser

import uvicorn

from resume_tailor import config


def main() -> None:
    url = f"http://{config.HOST}:{config.PORT}"
    print(f"\n  Resume Tailor running at {url}")
    print(f"  Model: {config.MODEL} | AI {'enabled' if config.has_api_credentials() else 'DISABLED (set ANTHROPIC_API_KEY)'}\n")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("resume_tailor.server:app", host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
