"""JD keyword profile, keyword matching against resume text, and an offline JD keyword extractor."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from .lexicon import CERT_HINTS, SKILLS, SOFT_SKILLS, STOPWORDS, SYNONYMS

IMPORTANCE_WEIGHT = {"required": 3.0, "preferred": 2.0, "nice_to_have": 1.0}
CATEGORY_FACTOR = {
    "hard_skill": 1.0,
    "tool": 1.0,
    "certification": 1.0,
    "domain": 0.8,
    "responsibility": 0.6,
    "soft_skill": 0.5,
}
LOOSE_CATEGORIES = {"soft_skill", "responsibility", "domain"}


@dataclass
class Keyword:
    term: str
    category: str = "hard_skill"
    importance: str = "preferred"
    aliases: list[str] = field(default_factory=list)
    jd_mentions: int = 1

    @property
    def weight(self) -> float:
        return IMPORTANCE_WEIGHT.get(self.importance, 2.0) * CATEGORY_FACTOR.get(self.category, 1.0)


@dataclass
class JDProfile:
    job_title: str
    keywords: list[Keyword]
    title_variants: list[str] = field(default_factory=list)
    seniority: str = ""
    min_years_experience: float | None = None
    education_requirement: str | None = None
    responsibilities: list[str] = field(default_factory=list)
    company: str = ""
    source: str = "ai"  # "ai" or "heuristic"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "JDProfile":
        kws = [Keyword(**{k: v for k, v in kw.items() if k in Keyword.__dataclass_fields__}) for kw in data.get("keywords", [])]
        fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "keywords"}
        return cls(keywords=kws, **fields)


# --------------------------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------------------------

_SUFFIXES = ("ations", "ation", "ments", "ment", "ings", "ing", "ions", "ion", "ers", "er", "ed", "es", "ive", "ity", "ly", "s")


def _stem(word: str) -> str:
    w = word.lower()
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def _variants(term: str, aliases: tuple[str, ...]) -> set[str]:
    out: set[str] = set()
    for t in (term, *aliases):
        t = (t or "").strip()
        if not t:
            continue
        out.add(t)
        low = t.lower()
        if re.search(r"(?<=\w)[./-](?=\w)", t):
            out.add(re.sub(r"(?<=\w)[./-](?=\w)", "", t))
            out.add(re.sub(r"(?<=\w)[./-](?=\w)", " ", t))
        out.update(SYNONYMS.get(low, set()))
        # "Amazon Web Services (AWS)" style terms
        paren = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", t)
        if paren:
            out.add(paren.group(1).strip())
            out.add(paren.group(2).strip())
    return {v for v in out if v}


@lru_cache(maxsize=4096)
def _compile(variant: str) -> tuple[re.Pattern, bool]:
    """Compile a regex for one variant. Short acronyms (<=3 chars with capitals) match case-sensitively."""
    tokens = variant.split()
    parts = [re.escape(tok) for tok in tokens]
    last = tokens[-1]
    if last.isalpha() and len(last) > 3 and not last.lower().endswith("s"):
        parts[-1] += r"(?:s|es)?"
    body = r"[\s_/-]+".join(parts)
    case_sensitive = len(variant) <= 3 and any(c.isupper() for c in variant)
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(r"(?<![A-Za-z0-9+#])" + body + r"(?![A-Za-z0-9+#])", flags)
    return pattern, case_sensitive


def keyword_spans(text: str, kw: Keyword) -> list[tuple[int, int]]:
    """All non-overlapping character spans in `text` where the keyword (or a variant) appears."""
    spans: list[tuple[int, int]] = []
    for variant in sorted(_variants(kw.term, tuple(kw.aliases)), key=len, reverse=True):
        pattern, _ = _compile(variant)
        for m in pattern.finditer(text):
            if not any(s < m.end() and m.start() < e for s, e in spans):
                spans.append((m.start(), m.end()))
    return spans


def _loose_line_match(text: str, kw: Keyword) -> int:
    """For soft skills / responsibilities: count lines containing every content-word stem of the term."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z-]+", kw.term) if w.lower() not in STOPWORDS]
    if not words:
        return 0
    stems = [_stem(w) for w in words]
    count = 0
    for line in text.split("\n"):
        low = line.lower()
        if all(re.search(r"(?<![a-z])" + re.escape(st), low) for st in stems):
            count += 1
    return count


def count_keyword(text: str, kw: Keyword) -> int:
    n = len(keyword_spans(text, kw))
    if n == 0 and kw.category in LOOSE_CATEGORIES:
        n = _loose_line_match(text, kw)
    return n


# --------------------------------------------------------------------------------------------
# Offline JD keyword extraction (used when no API key is configured)
# --------------------------------------------------------------------------------------------

_REQ_HEAD = re.compile(r"(requirement|qualification|must have|must-have|what you.?ll need|what we.?re looking for|you have|you bring|who you are|skills|minimum|basic qualifications|required)", re.I)
_PREF_HEAD = re.compile(r"(preferred|nice to have|nice-to-have|bonus|a plus|good to have|desired|desirable|extra credit|ideally)", re.I)
_RESP_HEAD = re.compile(r"(responsibilit|what you.?ll do|the role|your role|day to day|you will|about the job|about the role)", re.I)
_TITLE_LINE = re.compile(r"(?:job title|position|role|title)\s*[:\-]\s*(.+)", re.I)
_ROLE_NOUNS = re.compile(
    r"\b(engineer|developer|manager|analyst|scientist|designer|architect|consultant|specialist|lead|director|"
    r"administrator|associate|coordinator|officer|intern|programmer|strategist|researcher|accountant|"
    r"recruiter|executive|representative|technician|writer|editor|marketer|owner)\b",
    re.I,
)
_ACRONYM_STOP = {
    "USA", "US", "UK", "EEO", "EOE", "AND", "OR", "THE", "IT", "NA", "TBD", "FAQ", "CEO", "CTO", "HR", "PTO",
    "LLC", "INC", "LTD", "ADA", "OK", "ID", "YOU", "WE", "OUR", "NEW", "ALL", "IN", "ON", "AT", "TO", "OF",
}


def _guess_title(jd: str) -> str:
    for line in jd.split("\n")[:40]:
        m = _TITLE_LINE.match(line.strip())
        if m and len(m.group(1)) < 80:
            return m.group(1).strip(" .")
    for line in jd.split("\n")[:15]:
        s = line.strip().strip("#*").strip()
        if 3 <= len(s) <= 80 and _ROLE_NOUNS.search(s) and len(s.split()) <= 10:
            return re.split(r"\s+[-|@–]\s+|\s+at\s+", s)[0].strip()
    m = _ROLE_NOUNS.search(jd)
    if m:
        start = max(0, m.start() - 30)
        words = jd[start : m.end()].split()[-4:]
        return " ".join(words)
    return ""


def heuristic_profile(jd: str) -> JDProfile:
    """Build a keyword profile from a JD without AI, using the built-in skills lexicon."""
    lines = jd.split("\n")
    mode = "general"
    found: dict[str, Keyword] = {}

    candidates = list(SKILLS)
    for acr in set(re.findall(r"\b[A-Z][A-Z0-9&/.+-]{1,7}\b", jd)):
        if acr not in _ACRONYM_STOP and not acr.isdigit() and acr.lower() not in {s.lower() for s in candidates}:
            candidates.append(acr)

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # "Flask, FastAPI or Django": any one satisfies the requirement, so none is individually required.
        alt_terms: set[str] = set()
        for segment in re.split(r"[().;:]", stripped):
            if re.search(r"\b(?:or|either)\b", segment, re.I):
                in_segment = [t for t in candidates if keyword_spans(segment, Keyword(term=t))]
                if len(in_segment) >= 2:
                    alt_terms.update(t.lower() for t in in_segment)
        if len(stripped) < 70:
            if _PREF_HEAD.search(stripped):
                mode = "preferred"
            elif _REQ_HEAD.search(stripped):
                mode = "required"
            elif _RESP_HEAD.search(stripped):
                mode = "responsibility"
        for term in candidates:
            kw_probe = Keyword(term=term)
            if not keyword_spans(stripped, kw_probe):
                continue
            key = term.lower()
            syn_key = next((k for k in found if k in SYNONYMS.get(key, set())), None)
            key = syn_key or key
            if _PREF_HEAD.search(stripped) or (term.lower() in alt_terms and mode == "required"):
                importance = "preferred"
            elif mode == "required":
                importance = "required"
            elif mode == "preferred":
                importance = "nice_to_have"
            else:
                importance = "preferred"
            category = "soft_skill" if term.lower() in SOFT_SKILLS else (
                "certification" if any(h in term.lower() for h in CERT_HINTS) else "hard_skill"
            )
            if key in found:
                found[key].jd_mentions += 1
                rank = {"required": 3, "preferred": 2, "nice_to_have": 1}
                if rank[importance] > rank[found[key].importance]:
                    found[key].importance = importance
            else:
                found[key] = Keyword(term=term, category=category, importance=importance)

    # Drop terms fully contained in a longer found term (e.g. "Spring" when "Spring Boot" was found).
    terms = list(found.values())
    pruned = [
        kw for kw in terms
        if not any(
            other is not kw and len(other.term) > len(kw.term) and re.search(rf"(?<![a-z]){re.escape(kw.term.lower())}(?![a-z])", other.term.lower())
            and other.jd_mentions >= kw.jd_mentions
            for other in terms
        )
    ]
    pruned.sort(key=lambda k: (-k.weight, -k.jd_mentions, k.term))

    years = None
    m = re.search(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years|yrs)", jd, re.I)
    if m:
        years = float(m.group(1))
    edu = None
    m = re.search(r"(\b(?:bachelor|master|ph\.?\s?d\b|b\.s\.|m\.s\.|b\.tech|m\.tech|degree\b)[^\n]{0,80})", jd, re.I)
    if m:
        edu = m.group(1).strip()

    title = _guess_title(jd)
    return JDProfile(
        job_title=title,
        keywords=pruned[:40],
        title_variants=[],
        min_years_experience=years,
        education_requirement=edu,
        source="heuristic",
    )
