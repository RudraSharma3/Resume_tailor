# Resume Tailor

A local web app that tailors your resume to a job description **without inventing anything**, shows the ATS score after every iteration, and writes the result back into your Overleaf `.tex`.

## What it does

| # | Feature | Where |
|---|---------|-------|
| 1 | Tailors your resume to the JD. Input can be **PDF, DOCX, TXT, Markdown or LaTeX**. | Step 3 |
| 2 | **ATS match score** (0-100) of your resume against the JD, with a breakdown | Step 2 |
| 3 | Adds missing JD keywords **only where your resume shows evidence**, and rewrites bullets in Google's **X-Y-Z** form (*Accomplished X as measured by Y by doing Z*) using **only real numbers**. When a bullet needs a number it doesn't have, it asks you. | Step 3 |
| 4 | **Senior recruiter review** (10+ yrs persona): match score /100, verdict, the **top 5 missing keywords**, strengths, concerns, quick wins | Step 2 (re-run any time) |
| 5 | **Iterative tailoring with a live preview**: every pass is scored, so you see e.g. `56 → 66 → 74 → 83`. Auto mode stops inside the **82-85** band so the resume still reads naturally. | Step 3 |
| 6 | Keep prompting: give instructions, answer metric questions, add true facts, edit the text yourself, or roll back to any version | Step 3 |
| 7 | **5 things that move your score**, each with its exact projected score (*"add Kubernetes + Terraform → 85"*). You confirm what you can honestly claim; it applies only those. Then it asks for your **.tex**, edits it and gives you a file to paste into Overleaf. | Steps 4-5 |

## How it stays honest

- **The ATS score is computed, not guessed.** A deterministic engine (`resume_tailor/ats.py`) scores keyword coverage (weighted by required/preferred), job-title match, quantified bullets, action verbs, sections and contact info, years of experience and degree, and readability. It **subtracts points for keyword stuffing and for sentences copied from the JD**. The same resume always gets the same score, so score changes between iterations come only from edits to the resume.
- **Every number is checked.** After each iteration, any number that isn't in your original resume or one of your answers is flagged, and the next iteration is told to fix it.
- **Every new keyword needs evidence.** The AI must quote the line from your resume that supports it. Quotes are verified, and anything unsupported is flagged. Skills with no evidence go to the score-levers step, where you decide.
- **"I don't" means never.** Keywords you decline are never suggested or added again.
- **Your LaTeX template is protected.** The preamble is restored if the AI touches it, and the output is checked for balanced braces, matching environments, undefined macros and unescaped `% & $ #`.

> The score estimates how keyword-based ATS filters (Workday, Greenhouse, Lever, iCIMS, Taleo) and recruiters screen. No company publishes its exact formula, so use it to compare versions of your resume, not as a guarantee.

## Setup

Requires Python 3.10+ and an [Anthropic API key](https://console.anthropic.com/).

```bash
git clone https://github.com/RudraSharma3/Resume_tailor.git
cd Resume_tailor
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # then put your key in .env
python run.py
```

It opens http://127.0.0.1:8000. Without a key it still gives you the ATS score and score levers (using an offline keyword extractor). Tailoring, the recruiter review and LaTeX editing need the key.

## Using it

1. **Upload** your resume (any format) and paste the JD → **Analyze match**.
2. Read the **ATS score** and the **recruiter's top 5 missing keywords**.
3. **Auto-tailor to 82-85** (or run one iteration at a time). Each iteration has a focus:
   *Alignment & keywords → Impact (X-Y-Z bullets) → Polish & naturalness → your instructions.*
   Answer the **"Questions for real numbers"** between iterations to make bullets measurable. Use **Changes / vs original / Clean** to review edits, and click any version chip to go back.
4. In **Score levers**, mark each claim as *I have this* (say where you used it) or *I don't*, then **Apply my confirmations**.
5. In **LaTeX**, paste your Overleaf `main.tex` (or upload it) → **Generate tailored .tex** → **Copy** → paste into Overleaf → Recompile.

## Configuration (`.env`)

| Variable | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` | - | required for AI steps |
| `RESUME_TAILOR_MODEL` | `claude-opus-5-5` | any Claude model ID you have access to |
| `RESUME_TAILOR_TARGET_MIN` / `_MAX` | `82` / `85` | auto-tailor target band |
| `RESUME_TAILOR_MAX_ITERATIONS` | `4` | cap for one auto-tailor run |
| `RESUME_TAILOR_PORT` | `8000` | |

Cost: each AI step is one Claude request. A rough estimate for a full session with the default model (analysis, 3-4 iterations, a recruiter re-check and the LaTeX step) is $1-2; `RESUME_TAILOR_MODEL=claude-sonnet-5-5` costs about half. Sessions are saved under `data/` (git-ignored), so a page reload keeps your work.

## Project layout

```
run.py                     start the app
resume_tailor/
  ats.py                   deterministic ATS scoring engine
  whatif.py                score levers: simulate each change and re-score it
  guards.py                honesty checks (numbers, keyword evidence) + LaTeX validation
  keywords.py              keyword matching (synonyms, plurals, acronyms) + offline JD extractor
  resume_struct.py         sections, bullets, contact info, years of experience
  parsers.py               PDF / DOCX / TXT / TEX -> text
  llm.py, prompts.py       Claude calls (structured JSON outputs, prompt caching, refusal fallback)
  engine.py                sessions, iterations, confirmations, LaTeX step
  server.py                FastAPI + server-sent events
  static/                  the UI (plain HTML/CSS/JS)
tests/                     pytest suite (AI calls are mocked; no key needed)
```

Run the tests with `pip install pytest httpx && python -m pytest`.
