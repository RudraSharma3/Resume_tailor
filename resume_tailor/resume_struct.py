"""Split resume text into sections, entries and bullets; detect contact info and years of experience."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .lexicon import HEADING_LOOKUP

BULLET_RE = re.compile(r"^\s*(?:[-*•●▪◦‣∙·○■□➢➤►▶✓✔]|\d{1,2}[.)])\s+")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,5}\)?[\s.-]?)?\d{3,5}[\s.-]?\d{3,5}")
LINK_RE = re.compile(r"(linkedin\.com|github\.com|gitlab\.com|behance\.net|dribbble\.com|portfolio|https?://|www\.)", re.I)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_PAT = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_PAT = rf"(?:{_MONTH_PAT}\s*'?\d{{2,4}}|\d{{1,2}}\s*/\s*\d{{2,4}}|(?:19|20)\d{{2}})"
_END_PAT = rf"(?:{_DATE_PAT}|present|current|now|till date|to date|today|ongoing)"
DATE_RANGE_RE = re.compile(rf"({_DATE_PAT})\s*(?:-|to|until|–|—)\s*({_END_PAT})", re.I)


@dataclass
class Section:
    kind: str  # header | summary | experience | projects | skills | education | certifications | achievements | other
    title: str
    lines: list[str] = field(default_factory=list)


@dataclass
class Bullet:
    text: str
    section: str


@dataclass
class ResumeStructure:
    sections: list[Section]
    bullets: list[Bullet]
    text: str

    def section_text(self, *kinds: str) -> str:
        return "\n".join(
            "\n".join([s.title, *s.lines]) for s in self.sections if s.kind in kinds
        )

    @property
    def kinds(self) -> set[str]:
        return {s.kind for s in self.sections}


def heading_kind(line: str) -> str | None:
    """Return the section kind if `line` looks like a section heading."""
    raw = line.strip()
    if not raw or len(raw) > 60:
        return None
    is_md_heading = raw.startswith("## ") or raw.startswith("# ")
    cleaned = re.sub(r"^#+\s*", "", raw)
    cleaned = re.sub(r"[*_:|]+", " ", cleaned)
    cleaned = cleaned.replace("&", " and ").lower()
    cleaned = re.sub(r"[^a-z ]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned in HEADING_LOOKUP:
        return HEADING_LOOKUP[cleaned]
    if raw.startswith("## "):
        # Unknown markdown heading: still a section boundary.
        for name, kind in HEADING_LOOKUP.items():
            if cleaned.startswith(name + " ") or cleaned.endswith(" " + name):
                return kind
        return "other"
    if is_md_heading:
        return None
    return None


def parse_resume(text: str) -> ResumeStructure:
    lines = text.split("\n")
    sections: list[Section] = [Section(kind="header", title="")]
    for line in lines:
        kind = heading_kind(line)
        if kind and not line.strip().startswith("# "):
            sections.append(Section(kind=kind, title=re.sub(r"^#+\s*", "", line.strip())))
        else:
            sections[-1].lines.append(line)

    bullets: list[Bullet] = []
    for section in sections:
        if section.kind in {"header", "skills"}:
            continue
        section_bullets = _bullets_in(section)
        bullets.extend(section_bullets)
    return ResumeStructure(sections=sections, bullets=bullets, text=text)


def _looks_like_entry_header(line: str) -> bool:
    s = line.strip()
    if s.startswith("###"):
        return True
    if DATE_RANGE_RE.search(s):
        return True
    if "|" in s and len(s.split()) <= 16:
        return True
    words = s.split()
    if 0 < len(words) <= 7 and not s.endswith("."):
        caps = sum(1 for w in words if w[:1].isupper())
        if caps >= max(1, len(words) - 1):
            return True
    return False


def _bullets_in(section: Section) -> list[Bullet]:
    out: list[Bullet] = []
    marked = [ln for ln in section.lines if BULLET_RE.match(ln)]
    if marked:
        current: str | None = None
        for line in section.lines:
            s = line.strip()
            if BULLET_RE.match(line):
                if current:
                    out.append(Bullet(current, section.kind))
                current = BULLET_RE.sub("", line).strip()
            elif current and s and not _looks_like_entry_header(s) and (s[:1].islower() or not current.endswith(".")):
                current = f"{current} {s}"  # wrapped continuation line from PDF extraction
            else:
                if current:
                    out.append(Bullet(current, section.kind))
                current = None
        if current:
            out.append(Bullet(current, section.kind))
    elif section.kind in {"experience", "projects", "achievements", "other"}:
        for line in section.lines:
            s = line.strip()
            if len(s.split()) >= 8 and not _looks_like_entry_header(s):
                out.append(Bullet(s, section.kind))
    return [b for b in out if len(b.text.split()) >= 3]


# --------------------------------------------------------------------------------------------
# Contact info, dates, education
# --------------------------------------------------------------------------------------------


def contact_info(text: str) -> dict[str, bool]:
    head = "\n".join(text.split("\n")[:15])
    phone = any(len(re.sub(r"\D", "", m)) >= 10 for m in PHONE_RE.findall(head))
    return {
        "email": bool(EMAIL_RE.search(text)),
        "phone": phone,
        "link": bool(LINK_RE.search(head)),
    }


def _parse_date(token: str, is_end: bool) -> tuple[int, int] | None:
    t = token.strip().lower()
    today = date.today()
    if t in {"present", "current", "now", "till date", "to date", "today", "ongoing"}:
        return today.year, today.month
    m = re.match(rf"({_MONTH_PAT})\s*'?(\d{{2,4}})", t)
    if m:
        month = _MONTHS.get(m.group(1)[:4].rstrip("."), None) or _MONTHS.get(m.group(1)[:3], 1)
        year = int(m.group(2))
        year = year + 2000 if year < 100 else year
        return year, month
    m = re.match(r"(\d{1,2})\s*/\s*(\d{2,4})", t)
    if m:
        year = int(m.group(2))
        year = year + 2000 if year < 100 else year
        return year, max(1, min(12, int(m.group(1))))
    m = re.match(r"((?:19|20)\d{2})", t)
    if m:
        return int(m.group(1)), 12 if is_end else 1
    return None


def years_of_experience(structure: ResumeStructure) -> float | None:
    """Estimate total professional experience from date ranges in the experience section."""
    text = structure.section_text("experience")
    if not text.strip():
        return None
    intervals: list[tuple[int, int]] = []
    for match in DATE_RANGE_RE.finditer(text):
        start = _parse_date(match.group(1), is_end=False)
        end = _parse_date(match.group(2), is_end=True)
        if not start or not end:
            continue
        s = start[0] * 12 + start[1]
        e = end[0] * 12 + end[1]
        if e < s or (e - s) > 12 * 45:
            continue
        intervals.append((s, e))
    if not intervals:
        return None
    intervals.sort()
    merged = [list(intervals[0])]
    for s, e in intervals[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    months = sum(e - s + 1 for s, e in merged)
    return round(months / 12, 1)


DEGREE_RE = re.compile(
    r"\b(bachelor'?s?|master'?s?|ph\.?\s?d|doctorate|mba|b\.?\s?tech|m\.?\s?tech|b\.?\s?e\b|m\.?\s?e\b|"
    r"b\.?\s?sc?|m\.?\s?sc?|b\.?\s?a\b|m\.?\s?a\b|bca|mca|b\.?\s?com|associate'?s? degree|degree|diploma|"
    r"university|college|institute of technology)",
    re.I,
)


def has_degree(structure: ResumeStructure) -> bool:
    edu = structure.section_text("education") or structure.text
    return bool(DEGREE_RE.search(edu))
