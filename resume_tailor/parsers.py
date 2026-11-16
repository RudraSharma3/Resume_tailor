"""Turn uploaded files (PDF, DOCX, TXT, MD, TEX) into plain text, and LaTeX resumes into readable text."""

from __future__ import annotations

import io
import re
import unicodedata
from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".markdown", ".tex", ".rtf", ".text"}

_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")
_QUOTES = {ord("\u2018"): "'", ord("\u2019"): "'", ord("\u201c"): '"', ord("\u201d"): '"', ord("\u00a0"): " "}


class ParseError(ValueError):
    """Raised when a file can't be turned into text."""


def clean_text(text: str) -> str:
    """Normalize unicode, dashes, quotes and whitespace while keeping line structure."""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_DASHES).translate(_QUOTES)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = re.sub(r"[ \f\v]+", " ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(filename: str, data: bytes) -> str:
    """Extract text from an uploaded file based on its extension."""
    ext = Path(filename or "").suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ParseError(
            f"Unsupported file type '{ext or 'unknown'}'. Use PDF, DOCX, TXT, MD or TEX."
        )
    if not data:
        raise ParseError("The uploaded file is empty.")
    if ext == ".pdf":
        text = _pdf_to_text(data)
    elif ext == ".docx":
        text = _docx_to_text(data)
    elif ext == ".tex":
        text = latex_to_text(_decode(data))
    elif ext == ".rtf":
        text = _rtf_to_text(_decode(data))
    else:
        text = _decode(data)
    text = clean_text(text)
    if len(text) < 30:
        raise ParseError(
            "Could not read enough text from this file. If it's a scanned PDF (an image), "
            "export it as a text-based PDF or upload DOCX/TXT instead."
        )
    return text


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _pdf_to_text(data: bytes) -> str:
    text = ""
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [page.extract_text(x_tolerance=1.5, y_tolerance=3) or "" for page in pdf.pages]
        text = "\n".join(pages)
    except Exception:  # noqa: BLE001 - fall back to pypdf on any pdfplumber failure
        text = ""
    if len(text.strip()) < 30:
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:  # noqa: BLE001
            raise ParseError(f"Could not read this PDF: {exc}") from exc
    return text


def _docx_to_text(data: bytes) -> str:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover
        raise ParseError("python-docx is not installed.") from exc
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Could not read this DOCX: {exc}") from exc

    lines: list[str] = []
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = docx.text.paragraph.Paragraph(child, document)
            text = para.text.strip()
            if not text:
                lines.append("")
                continue
            style = (para.style.name or "").lower() if para.style is not None else ""
            is_list = "list" in style or child.find(".//{*}numPr") is not None
            lines.append(f"- {text}" if is_list and not text.startswith(("-", "•")) else text)
        elif tag == "tbl":
            table = docx.table.Table(child, document)
            for row in table.rows:
                cells = []
                for cell in row.cells:
                    cell_text = cell.text.strip()
                    if cell_text and cell_text not in cells:
                        cells.append(cell_text)
                if cells:
                    lines.append(" | ".join(cells))
    return "\n".join(lines)


def _rtf_to_text(rtf: str) -> str:
    text = re.sub(r"\\par[d]?", "\n", rtf)
    text = re.sub(r"\{\\\*[^{}]*\}", "", text)
    text = re.sub(r"\\[a-z]+-?\d* ?", "", text)
    return text.replace("{", "").replace("}", "")


# --------------------------------------------------------------------------------------------
# LaTeX -> text
# --------------------------------------------------------------------------------------------

# Commands whose arguments are dropped entirely.
_DROP_COMMANDS = {
    "documentclass", "usepackage", "setlength", "addtolength", "pagestyle", "thispagestyle", "fancyhf",
    "fancyfoot", "fancyhead", "renewcommand", "newcommand", "providecommand", "titleformat", "titlespacing",
    "definecolor", "color", "pdfgentounicode", "input", "include", "vspace", "hspace", "label", "ref",
    "setcounter", "geometry", "hypersetup", "raggedright", "raggedleft", "centering", "newenvironment",
    "renewenvironment", "def", "let", "linespread", "fontsize", "selectfont", "urlstyle", "faicon",
    "includegraphics", "rule", "titlerule", "setlist", "newgeometry", "enlargethispage", "columnbreak",
}

# Commands rendered as a section heading.
_SECTION_COMMANDS = {"section", "section*", "subsection", "subsection*", "cvsection", "resumesection"}

_ESCAPES = {
    r"\%": "%", r"\&": "&", r"\$": "$", r"\#": "#", r"\_": "_", r"\{": "{", r"\}": "}",
    r"\textasciitilde": "~", r"\textasciicircum": "^", r"\textbackslash": "\\", r"\ldots": "...",
    r"\textbar": "|", r"\textendash": "-", r"\textemdash": "-", r"\textbullet": "•", r"\cdot": "·",
    r"\LaTeX": "LaTeX", r"\TeX": "TeX", "~": " ", "--": "-", "---": "-", "``": '"', "''": '"',
}


def latex_body(tex: str) -> str:
    """Return the text between \\begin{document} and \\end{document} (or everything if absent)."""
    match = re.search(r"\\begin\{document\}(.*?)(\\end\{document\}|$)", tex, re.S)
    return match.group(1) if match else tex


def strip_latex_comments(tex: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", tex)


def _read_group(text: str, start: int, open_ch: str = "{", close_ch: str = "}") -> tuple[str, int] | None:
    """Read a balanced {...} group starting at text[start] == open_ch. Returns (inner, end_index)."""
    if start >= len(text) or text[start] != open_ch:
        return None
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    return text[start + 1 :], len(text)


def _convert(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            # Line break \\ (optionally followed by [len])
            if i + 1 < n and text[i + 1] == "\\":
                out.append("\n")
                i += 2
                if i < n and text[i] == "[":
                    grp = _read_group(text, i, "[", "]")
                    i = grp[1] if grp else i
                continue
            # Escaped special character
            if i + 1 < n and text[i + 1] in "%&$#_{} ,;:!":
                out.append(" " if text[i + 1] in " ,;:!" else text[i + 1])
                i += 2
                continue
            match = re.match(r"\\([A-Za-z@]+\*?)", text[i:])
            if not match:
                i += 1
                continue
            cmd = match.group(1)
            i += len(match.group(0))
            if cmd in {"begin", "end"}:
                grp = _read_group(text, i)
                env = grp[0] if grp else ""
                i = grp[1] if grp else i
                # Skip environment options/column specs: [..] always, {..} for table-like envs.
                skip_braces = 2 if re.match(r"(tabular|minipage|multicols|adjustwidth|longtable|array)", env) else 0
                while cmd == "begin" and i < n:
                    j = i
                    while j < n and text[j] in " \t":
                        j += 1
                    if j < n and text[j] == "[":
                        grp = _read_group(text, j, "[", "]")
                    elif j < n and text[j] == "{" and skip_braces > 0:
                        grp = _read_group(text, j)
                        skip_braces -= 1
                    else:
                        break
                    if not grp:
                        break
                    i = grp[1]
                if env in {"itemize", "enumerate", "description"} or env.lower().endswith("list"):
                    out.append("\n")
                elif env.startswith(("tabular", "center", "minipage")):
                    out.append("\n")
                continue
            # optional [..] args
            while i < n and text[i] == "[":
                grp = _read_group(text, i, "[", "]")
                if not grp:
                    break
                i = grp[1]
            args: list[str] = []
            while True:
                j = i
                while j < n and text[j] in " \t\n" and cmd not in {"item"}:
                    j += 1
                if j < n and text[j] == "{":
                    grp = _read_group(text, j)
                    if not grp:
                        break
                    args.append(grp[0])
                    i = grp[1]
                    # many resume macros take 4-6 args; stop at newline gaps
                else:
                    break
            base = cmd.rstrip("*").lower()
            if cmd in _ESCAPES or "\\" + cmd in _ESCAPES:
                out.append(_ESCAPES.get("\\" + cmd, ""))
                out.extend(_convert(a) for a in args)
                continue
            if base in _DROP_COMMANDS or cmd in _DROP_COMMANDS:
                continue
            if cmd.lower() in _SECTION_COMMANDS:
                title = _convert(args[0]) if args else ""
                out.append(f"\n\n## {title.strip()}\n")
                out.extend(_convert(a) for a in args[1:])
                continue
            if base == "item":
                out.append("\n- ")
                out.extend(_convert(a) for a in args)
                continue
            if base == "href" and len(args) >= 2:
                label = _convert(args[1]).strip()
                url = args[0].strip()
                bare = re.sub(r"^(mailto:|tel:|https?://)(www\.)?", "", url).rstrip("/")
                out.append(label if (bare in label or not label) else f"{label} ({url})")
                continue
            if base == "url" and args:
                out.append(args[0])
                continue
            if base in {"newline", "linebreak", "par", "bigskip", "medskip", "smallskip"}:
                out.append("\n")
                continue
            if base in {"hfill", "quad", "qquad", "enspace", "space"}:
                out.append(" ")
                continue
            if not args:
                continue
            converted = [_convert(a).strip() for a in args]
            converted = [c for c in converted if c]
            lowered = base.lower()
            if "item" in lowered and len(converted) == 1:
                out.append("\n- " + converted[0])
            elif "item" in lowered and len(converted) > 1:
                out.append("\n- " + ": ".join(converted))
            elif any(k in lowered for k in ("heading", "subheading", "entry", "project", "job", "position", "event")) or len(converted) >= 3:
                out.append("\n### " + " | ".join(converted) + "\n")
            else:
                out.append(" ".join(converted))
            continue
        if ch in "{}":
            i += 1
            continue
        if ch == "$":
            # inline math: keep content (e.g. $\sim$30\%)
            end = text.find("$", i + 1)
            if end == -1:
                i += 1
                continue
            inner = text[i + 1 : end]
            inner = inner.replace("\\sim", "~").replace("\\times", "x").replace("\\%", "%")
            inner = inner.replace("\\cdot", "·").replace("\\mid", "|").replace("\\approx", "~")
            inner = re.sub(r"\\[A-Za-z]+", "", inner).replace("^", "").replace("{", "").replace("}", "")
            out.append(inner)
            i = end + 1
            continue
        if ch == "&":
            out.append(" | ")
            i += 1
            continue
        if ch == "~":
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def latex_to_text(tex: str) -> str:
    """Convert a LaTeX resume into structured plain text (## sections, ### entries, - bullets)."""
    body = strip_latex_comments(latex_body(tex))
    text = _convert(body)
    text = text.replace("--", "-")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        line = re.sub(r"(\s*\|\s*)+$", "", line)
        line = re.sub(r"^(\s*\|\s*)+", "", line)
        if line in {"-", "|"}:
            continue
        lines.append(line)
    # Drop blank lines between consecutive bullets / entry headers.
    tidy: list[str] = []
    for idx, line in enumerate(lines):
        if not line:
            prev = next((ln for ln in reversed(tidy) if ln), "")
            nxt = next((ln for ln in lines[idx + 1 :] if ln), "")
            if nxt.startswith("- ") and (prev.startswith("- ") or prev.startswith("### ")):
                continue
        tidy.append(re.sub(r"\s+([:;,.])", r"\1", line))
    result = "\n".join(tidy)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()
