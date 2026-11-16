"""Honesty and safety checks on AI output.

- Every number in a tailored resume must trace back to your original resume or an answer you gave.
- Every new JD keyword must come with evidence from your resume, or your explicit confirmation.
- Edited LaTeX must keep the preamble, balanced braces and matching environments.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .keywords import JDProfile, count_keyword
from .parsers import latex_body, strip_latex_comments

_NUM = re.compile(
    r"(?<![A-Za-z0-9.])([$€£₹]\s?)?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?"
    r"(%|percent|x(?![A-Za-z])|k(?![A-Za-z])|mm(?![A-Za-z])|m(?![A-Za-z])|bn(?![A-Za-z])|b(?![A-Za-z])|million|billion|thousand|lakhs?|crores?|\+)?",
    re.I,
)
_MULT = {
    "k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9,
    "lakh": 1e5, "lakhs": 1e5, "crore": 1e7, "crores": 1e7,
}
_PLACEHOLDER = re.compile(r"\[(?:[^\]\n]{0,25})(?:x|X|#|n|N|\?|number|metric|tbd|TBD|%|amount)(?:[^\]\n]{0,25})\]")


@dataclass
class Flag:
    kind: str  # unverified_number | unverified_skill | declined_skill | placeholder
    severity: str  # high | medium
    message: str
    excerpt: str
    value: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _values(match: re.Match) -> set[float]:
    raw = match.group(2).replace(",", "")
    try:
        base = float(raw)
    except ValueError:
        return set()
    unit = (match.group(3) or "").lower()
    vals = {round(base, 3)}
    if unit in _MULT:
        vals.add(round(base * _MULT[unit], 3))
    return vals


def number_values(text: str) -> set[float]:
    out: set[float] = set()
    for m in _NUM.finditer(text):
        out |= _values(m)
    return out


def _line_of(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start : end if end != -1 else len(text)].strip()


def unverified_numbers(new_text: str, sources: list[str], extra_allowed: set[float] | None = None) -> list[Flag]:
    allowed: set[float] = set(extra_allowed or set())
    for s in sources:
        allowed |= number_values(s)
    flags: list[Flag] = []
    seen: set[str] = set()
    for m in _NUM.finditer(new_text):
        vals = _values(m)
        if not vals:
            continue
        base = next(iter(sorted(vals)))
        looks_like_year = not m.group(1) and not m.group(3) and "," not in m.group(2)
        if looks_like_year and 1950 <= base <= 2045 and float(base).is_integer():
            continue  # years
        if base in {0, 1} and not m.group(3):
            continue
        if vals & allowed:
            continue
        token = m.group(0).strip()
        line = _line_of(new_text, m.start())
        key = f"{token}|{line}"
        if key in seen:
            continue
        seen.add(key)
        flags.append(Flag(
            kind="unverified_number", severity="high",
            message=f"\u201c{token}\u201d isn't in your original resume or your answers. Remove it or confirm the real number.",
            excerpt=line[:220], value=token,
        ))
    return flags


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9+#%$ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def evidence_supported(quote: str, sources: list[str]) -> bool:
    """True if `quote` appears (near-verbatim) in one of the sources."""
    q = _norm(quote)
    if len(q) < 3:
        return False
    for src in sources:
        n = _norm(src)
        if q in n:
            return True
        q_words = [w for w in q.split() if len(w) > 2]
        if not q_words:
            continue
        for line in src.split("\n"):
            lw = set(_norm(line).split())
            if lw and sum(1 for w in q_words if w in lw) / len(q_words) >= 0.8:
                return True
    return False


def skill_flags(
    new_text: str,
    original_text: str,
    profile: JDProfile,
    evidence: dict[str, str],
    sources: list[str],
    confirmed: set[str],
    declined: set[str],
) -> list[Flag]:
    """Flag JD keywords that newly appear in the resume without verifiable support."""
    flags: list[Flag] = []
    confirmed_l = {c.lower() for c in confirmed}
    declined_l = {d.lower() for d in declined}
    evidence_l = {k.lower(): v for k, v in evidence.items()}
    for kw in profile.keywords:
        if count_keyword(original_text, kw) > 0:
            continue
        if count_keyword(new_text, kw) == 0:
            continue
        term = kw.term.lower()
        line = next((ln.strip() for ln in new_text.split("\n") if count_keyword(ln, kw)), "")
        if term in declined_l:
            flags.append(Flag(
                kind="declined_skill", severity="high",
                message=f"You said you can't claim \u201c{kw.term}\u201d, but it appears in this version. It will be removed next iteration.",
                excerpt=line[:220], value=kw.term,
            ))
            continue
        if term in confirmed_l:
            continue
        quote = evidence_l.get(term, "")
        if quote and evidence_supported(quote, sources):
            continue
        flags.append(Flag(
            kind="unverified_skill", severity="medium",
            message=f"\u201c{kw.term}\u201d was added without clear evidence in your resume. Keep it only if it's true.",
            excerpt=line[:220], value=kw.term,
        ))
    return flags


def placeholder_flags(text: str) -> list[Flag]:
    out = []
    for m in _PLACEHOLDER.finditer(text):
        out.append(Flag(
            kind="placeholder", severity="high",
            message=f"Placeholder {m.group(0)} left in the text.",
            excerpt=_line_of(text, m.start())[:220], value=m.group(0),
        ))
    return out


# --------------------------------------------------------------------------------------------
# LaTeX validation
# --------------------------------------------------------------------------------------------


def latex_preamble(tex: str) -> str:
    idx = tex.find("\\begin{document}")
    return tex[:idx] if idx != -1 else ""


def restore_preamble(original: str, edited: str) -> tuple[str, bool]:
    """If the AI changed the preamble, put the original back. Returns (tex, was_restored)."""
    orig_pre, new_pre = latex_preamble(original), latex_preamble(edited)
    if not orig_pre or not new_pre or orig_pre.strip() == new_pre.strip():
        return edited, False
    return orig_pre + edited[len(new_pre):], True


def validate_latex(tex: str, original: str | None = None) -> list[dict]:
    """Structural checks. Returns a list of {check, ok, detail}."""
    results: list[dict] = []
    body = strip_latex_comments(tex)

    depth = 0
    min_depth = 0
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            min_depth = min(min_depth, depth)
        i += 1
    results.append({
        "check": "Balanced braces",
        "ok": depth == 0 and min_depth >= 0,
        "detail": "OK" if depth == 0 and min_depth >= 0 else f"Unbalanced by {depth:+d}",
    })

    stack: list[str] = []
    env_ok, env_detail = True, "OK"
    for m in re.finditer(r"\\(begin|end)\{([^}]+)\}", body):
        kind, env = m.group(1), m.group(2)
        if kind == "begin":
            stack.append(env)
        elif not stack or stack[-1] != env:
            env_ok, env_detail = False, f"\\end{{{env}}} doesn't match \\begin{{{stack[-1] if stack else 'nothing'}}}"
            break
        else:
            stack.pop()
    if env_ok and stack:
        env_ok, env_detail = False, f"\\begin{{{stack[-1]}}} is never closed"
    results.append({"check": "Matching \\begin/\\end environments", "ok": env_ok, "detail": env_detail})

    has_doc = "\\begin{document}" in tex and "\\end{document}" in tex
    results.append({"check": "Document begins and ends", "ok": has_doc, "detail": "OK" if has_doc else "Missing \\begin{document} or \\end{document}"})

    if original is not None:
        same = latex_preamble(original).strip() == latex_preamble(tex).strip()
        results.append({"check": "Preamble unchanged (template intact)", "ok": same, "detail": "OK" if same else "Preamble differs from your original"})
        orig_cmds = set(re.findall(r"\\newcommand\{?\\([A-Za-z]+)", original))
        used_new = set(re.findall(r"\\([A-Za-z]+)", latex_body(tex)))
        defined = orig_cmds | set(re.findall(r"\\newcommand\{?\\([A-Za-z]+)", tex))
        custom_like = {c for c in used_new if c.startswith("resume") or c.startswith("cv")}
        undefined = sorted(c for c in custom_like if c not in defined and c not in set(re.findall(r"\\([A-Za-z]+)", latex_body(original))))
        results.append({
            "check": "No undefined template macros",
            "ok": not undefined,
            "detail": "OK" if not undefined else "Possibly undefined: " + ", ".join("\\" + u for u in undefined),
        })

    bad_lines = []
    doc_start = tex[: tex.find("\\begin{document}")].count("\n") + 1 if "\\begin{document}" in tex else 1
    in_table = 0
    for offset, line in enumerate(latex_body(tex).split("\n")):
        ln_no = doc_start + offset
        if re.search(r"\\begin\{(tabular\*?|tabularx|array|align\*?|longtable)\}", line):
            in_table += 1
        if re.search(r"(?<=\d)(?<!\\)%", line):
            bad_lines.append(f"line {ln_no}: unescaped % after a number (LaTeX treats the rest as a comment)")
        code = re.sub(r"(?<!\\)%.*", "", line)
        if not in_table and re.search(r"(?<!\\)&", code):
            bad_lines.append(f"line {ln_no}: unescaped &")
        if len(re.findall(r"(?<!\\)\$", code)) % 2:
            bad_lines.append(f"line {ln_no}: unescaped $ (opens math mode)")
        if "\\href" not in code and "\\url" not in code and re.search(r"(?<!\\)#", code):
            bad_lines.append(f"line {ln_no}: unescaped #")
        if re.search(r"\\end\{(tabular\*?|tabularx|array|align\*?|longtable)\}", line):
            in_table = max(0, in_table - 1)
    results.append({
        "check": "Special characters escaped (% & $ # _)",
        "ok": not bad_lines,
        "detail": "OK" if not bad_lines else "; ".join(bad_lines[:5]),
    })
    return results
