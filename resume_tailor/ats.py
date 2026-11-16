"""Deterministic ATS match scoring.

The same resume + JD always gets the same score, so the score changes you see between tailoring
iterations come from the resume changing, not from model randomness.

Score components (max points):
    keywords      55  weighted JD keyword coverage (in-context use counts more than a skills list)
    title          8  target job title appears in the resume
    impact        12  share of bullets with real numbers (target: 60%+)
    bullets        7  bullets start with action verbs and stay readable in length
    sections       8  contact info + standard sections an ATS parser expects
    requirements   6  years of experience and degree requirements
    readability    4  length, no first-person voice, clean text
    penalty      -10  keyword stuffing / copy-pasting the JD
"""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field

from .keywords import JDProfile, Keyword, count_keyword
from .lexicon import ACTION_VERBS, IRREGULAR_PRESENT, STOPWORDS, TITLE_NOISE, WEAK_STARTS
from .resume_struct import ResumeStructure, contact_info, has_degree, parse_resume, years_of_experience

CONTEXT_SECTIONS = {"summary", "experience", "projects", "achievements", "other", "header"}
SKILLS_ONLY_CREDIT = 0.85
QUANT_TARGET = 0.6

_YEAR_RE = re.compile(r"^(19[5-9]\d|20[0-4]\d)$")
_METRIC_WORDS = re.compile(r"\b(doubled|tripled|quadrupled|halved|tenfold|twofold|threefold)\b", re.I)
_NUM_TOKEN = re.compile(r"(?<![A-Za-z0-9.])([$€£₹]\s?)?(\d[\d,]*(?:\.\d+)?)(\s?(?:%|percent|x\b|k\b|m\b|mm\b|bn\b|b\b|\+|million|billion|thousand|lakhs?|crores?))?(?![A-Za-z0-9])", re.I)
_XYZ_CLAUSE = re.compile(r"\b(by|through|via|using|leveraging|utilizing|with|implementing|building|introducing|designing|automating)\b", re.I)
FIRST_PERSON = re.compile(r"\b(I|me|my|mine|myself)\b")


def has_metric(text: str) -> bool:
    if _METRIC_WORDS.search(text):
        return True
    for m in _NUM_TOKEN.finditer(text):
        currency, number, unit = m.group(1), m.group(2), m.group(3)
        if currency or unit:
            return True
        digits = number.replace(",", "")
        if _YEAR_RE.match(digits):
            continue
        try:
            value = float(digits)
        except ValueError:
            continue
        if value >= 2:
            return True
    return False


def starts_with_action_verb(text: str) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z-]*", text)
    if not words:
        return False
    w = words[0].lower()
    if w in ACTION_VERBS or IRREGULAR_PRESENT.get(w) in ACTION_VERBS:
        return True
    candidates = {w + "ed", w + "d", w[:-1] + "ied" if w.endswith("y") else w + "ed"}
    if w.endswith("s"):
        base = w[:-1]
        candidates |= {base + "ed", base + "d"}
        if w.endswith("ies"):
            candidates.add(w[:-3] + "ied")
        if w.endswith("es"):
            candidates.add(w[:-2] + "ed")
    if w.endswith("ing"):
        base = w[:-3]
        candidates |= {base + "ed", base + "ed", base + "d", base + "ied"}
    if len(w) > 2 and w[-1] == w[-2]:
        candidates.add(w + "ed")
    # e.g. plan -> planned
    candidates.add(w + w[-1] + "ed")
    return bool(candidates & ACTION_VERBS)


def weak_start(text: str) -> bool:
    low = text.strip().lower()
    return low.startswith(WEAK_STARTS)


@dataclass
class BulletInfo:
    text: str
    section: str
    words: int
    metric: bool
    action_verb: bool
    weak: bool
    xyz: bool

    @property
    def issues(self) -> list[str]:
        out = []
        if not self.metric:
            out.append("no measurable result")
        if self.weak:
            out.append("weak opening")
        elif not self.action_verb:
            out.append("doesn't start with an action verb")
        if self.words > 38:
            out.append("too long")
        if self.words < 6:
            out.append("too short")
        return out


@dataclass
class KeywordHit:
    term: str
    category: str
    importance: str
    weight: float
    count: int
    context_count: int
    skills_count: int
    status: str  # matched | skills_only | missing
    credit: float


@dataclass
class Features:
    keyword_hits: list[KeywordHit]
    title_score: float  # 0..1
    title_line: str
    bullets: list[BulletInfo]
    contact: dict[str, bool]
    sections: set[str]
    years: float | None
    degree: bool
    word_count: int
    first_person: int
    garbled_ratio: float
    stuffed_terms: list[tuple[str, int]]
    skills_items: int
    copied_phrases: list[str]


# --------------------------------------------------------------------------------------------


def _title_core_words(title: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z+#.-]*", title.lower())
    return [w for w in words if w not in TITLE_NOISE and w not in STOPWORDS]


def _title_match(structure: ResumeStructure, profile: JDProfile) -> tuple[float, str]:
    titles = [t for t in [profile.job_title, *profile.title_variants] if t]
    if not titles:
        return 1.0, ""
    best, best_line = 0.0, ""
    lines = [ln for ln in structure.text.split("\n") if ln.strip()]
    for title in titles:
        core = _title_core_words(title)
        if not core:
            continue
        for line in lines[:200]:
            low = line.lower()
            hits = sum(1 for w in core if re.search(rf"(?<![a-z]){re.escape(w)}", low))
            ratio = hits / len(core)
            if ratio > best:
                best, best_line = ratio, line.strip()
        if best >= 1.0:
            break
    return best, best_line


def _copied_phrases(resume: str, jd: str, n: int = 9) -> list[str]:
    """Long word sequences copied verbatim from the JD - recruiters spot these instantly."""
    def toks(s: str) -> list[str]:
        return re.findall(r"[a-z0-9+#]+", s.lower())

    jd_toks = toks(jd)
    if len(jd_toks) < n:
        return []
    jd_grams = {" ".join(jd_toks[i : i + n]) for i in range(len(jd_toks) - n + 1)}
    res_toks = toks(resume)
    found: list[str] = []
    i = 0
    while i <= len(res_toks) - n:
        gram = " ".join(res_toks[i : i + n])
        if gram in jd_grams:
            found.append(gram)
            i += n
        else:
            i += 1
    return found


def extract_features(resume_text: str, profile: JDProfile, jd_text: str = "") -> Features:
    structure = parse_resume(resume_text)
    context_text = structure.section_text(*CONTEXT_SECTIONS, "education", "certifications")
    skills_text = structure.section_text("skills")

    hits: list[KeywordHit] = []
    for kw in profile.keywords:
        ctx = count_keyword(context_text, kw)
        sk = count_keyword(skills_text, kw)
        if ctx:
            status, credit = "matched", 1.0
        elif sk:
            status, credit = "skills_only", SKILLS_ONLY_CREDIT
        else:
            status, credit = "missing", 0.0
        hits.append(
            KeywordHit(
                term=kw.term, category=kw.category, importance=kw.importance, weight=kw.weight,
                count=ctx + sk, context_count=ctx, skills_count=sk, status=status, credit=credit,
            )
        )

    bullets = [
        BulletInfo(
            text=b.text,
            section=b.section,
            words=len(b.text.split()),
            metric=has_metric(b.text),
            action_verb=starts_with_action_verb(b.text),
            weak=weak_start(b.text),
            xyz=has_metric(b.text) and bool(_XYZ_CLAUSE.search(b.text)),
        )
        for b in structure.bullets
        if b.section in {"experience", "projects", "achievements", "other", "summary"}
    ]

    skills_items = 0
    if skills_text.strip():
        skills_items = len([p for p in re.split(r"[,;|•\n]", skills_text) if p.strip() and len(p.strip()) < 60])

    stuffed = [(h.term, h.count) for h in hits if h.count > 6 and h.category not in {"soft_skill"}]
    printable = sum(1 for c in resume_text if c.isprintable() or c in "\n")
    garbled = 1 - printable / max(1, len(resume_text))
    odd = len(re.findall(r"[^\x00-\x7F•·–—’‘“”€£₹é]", resume_text)) / max(1, len(resume_text))

    title_score, title_line = _title_match(structure, profile)
    return Features(
        keyword_hits=hits,
        title_score=title_score,
        title_line=title_line,
        bullets=bullets,
        contact=contact_info(resume_text),
        sections=structure.kinds,
        years=years_of_experience(structure),
        degree=has_degree(structure),
        word_count=len(resume_text.split()),
        first_person=len(FIRST_PERSON.findall(resume_text)),
        garbled_ratio=max(garbled, odd),
        stuffed_terms=stuffed,
        skills_items=skills_items,
        copied_phrases=_copied_phrases(resume_text, jd_text) if jd_text else [],
    )


# --------------------------------------------------------------------------------------------


@dataclass
class Component:
    id: str
    label: str
    score: float
    max: float
    detail: str
    tips: list[str] = field(default_factory=list)


def compute_score(f: Features, profile: JDProfile) -> tuple[float, list[Component]]:
    comps: list[Component] = []

    # 1. Keywords (55)
    total_w = sum(h.weight for h in f.keyword_hits) or 1.0
    got_w = sum(h.weight * h.credit for h in f.keyword_hits)
    kw_score = 55.0 * got_w / total_w
    matched = sum(1 for h in f.keyword_hits if h.status == "matched")
    partial = sum(1 for h in f.keyword_hits if h.status == "skills_only")
    missing_req = [h.term for h in f.keyword_hits if h.status == "missing" and h.importance == "required"]
    comps.append(Component(
        "keywords", "Keyword match", kw_score, 55,
        f"{matched} matched in context, {partial} only in skills list, "
        f"{len(f.keyword_hits) - matched - partial} missing of {len(f.keyword_hits)} JD keywords",
        [f"Missing required: {', '.join(missing_req[:6])}"] if missing_req else [],
    ))

    # 2. Title (8)
    title_pts = 8.0 if f.title_score >= 0.999 else (5.0 if f.title_score >= 0.66 else (2.5 if f.title_score >= 0.5 else 0.0))
    comps.append(Component(
        "title", "Job title alignment", title_pts, 8,
        f"Best match: \"{f.title_line[:80]}\"" if f.title_line else "Target title not found in resume",
        [] if title_pts >= 8 else [f"Use the target title \"{profile.job_title}\" in your headline or summary (only if it honestly fits)."],
    ))

    # 3. Impact (12)
    n_b = len(f.bullets) or 1
    quant_ratio = sum(1 for b in f.bullets if b.metric) / n_b if f.bullets else 0.0
    impact_pts = 12.0 * min(1.0, quant_ratio / QUANT_TARGET) if f.bullets else 0.0
    xyz_ratio = sum(1 for b in f.bullets if b.xyz) / n_b if f.bullets else 0.0
    comps.append(Component(
        "impact", "Quantified impact (XYZ)", impact_pts, 12,
        f"{round(quant_ratio * 100)}% of bullets have numbers, {round(xyz_ratio * 100)}% follow the X-Y-Z pattern",
        [] if quant_ratio >= QUANT_TARGET else ["Add real numbers (%, $, time saved, users, volume) to more bullets."],
    ))

    # 4. Bullet quality (7)
    if f.bullets:
        verb_ratio = sum(1 for b in f.bullets if b.action_verb and not b.weak) / n_b
        length_ok = sum(1 for b in f.bullets if 6 <= b.words <= 38) / n_b
        bullet_pts = 7.0 * (0.6 * verb_ratio + 0.4 * length_ok)
    else:
        verb_ratio = length_ok = 0.0
        bullet_pts = 0.0
    comps.append(Component(
        "bullets", "Bullet strength", bullet_pts, 7,
        f"{round(verb_ratio * 100)}% start with a strong action verb, {round(length_ok * 100)}% are a readable length",
        [] if verb_ratio >= 0.85 else ["Start bullets with strong verbs (Led, Built, Reduced...) instead of 'Responsible for'."],
    ))

    # 5. Sections & contact (8)
    sec_pts = 0.0
    sec_pts += 2.0 if f.contact.get("email") else 0.0
    sec_pts += 1.0 if f.contact.get("phone") else 0.0
    sec_pts += 1.0 if f.contact.get("link") else 0.0
    sec_pts += 1.5 if ("experience" in f.sections or "projects" in f.sections) else 0.0
    sec_pts += 1.0 if "education" in f.sections else 0.0
    sec_pts += 1.5 if "skills" in f.sections else 0.0
    missing_bits = [name for name, ok in [
        ("email", f.contact.get("email")), ("phone", f.contact.get("phone")), ("LinkedIn/GitHub link", f.contact.get("link")),
        ("Experience section", "experience" in f.sections or "projects" in f.sections),
        ("Education section", "education" in f.sections), ("Skills section", "skills" in f.sections),
    ] if not ok]
    comps.append(Component(
        "sections", "Sections & contact info", sec_pts, 8,
        "All standard sections found" if not missing_bits else f"Missing: {', '.join(missing_bits)}",
        [f"Add: {', '.join(missing_bits)}"] if missing_bits else [],
    ))

    # 6. Requirements (6): years (4) + degree (2)
    req_pts = 0.0
    req_tips: list[str] = []
    if profile.min_years_experience:
        need = float(profile.min_years_experience)
        if f.years is None:
            req_pts += 2.0
            req_tips.append("Add clear start-end dates to each role so the ATS can count your experience.")
        elif f.years >= need:
            req_pts += 4.0
        elif f.years >= 0.75 * need:
            req_pts += 2.5
        elif f.years >= 0.5 * need:
            req_pts += 1.0
        years_detail = f"~{f.years if f.years is not None else '?'} yrs found vs {need:g}+ required"
    else:
        req_pts += 4.0
        years_detail = "No minimum experience stated"
    if profile.education_requirement:
        req_pts += 2.0 if f.degree else 0.0
        if not f.degree:
            req_tips.append("Make your degree easy to find in an Education section.")
        edu_detail = "degree found" if f.degree else "degree not found"
    else:
        req_pts += 2.0
        edu_detail = "no degree requirement"
    comps.append(Component("requirements", "Experience & education fit", req_pts, 6, f"{years_detail}; {edu_detail}", req_tips))

    # 7. Readability (4)
    read_pts = 0.0
    read_tips: list[str] = []
    if 300 <= f.word_count <= 1100:
        read_pts += 2.0
    elif 200 <= f.word_count <= 1400:
        read_pts += 1.0
        read_tips.append("Aim for 350-900 words (1-2 pages).")
    else:
        read_tips.append("Resume length is far from the 1-2 page norm.")
    read_pts += 1.0 if f.first_person == 0 else (0.5 if f.first_person <= 2 else 0.0)
    if f.first_person:
        read_tips.append("Drop first-person words (I, my) from bullets.")
    read_pts += 1.0 if f.garbled_ratio < 0.01 else 0.0
    if f.garbled_ratio >= 0.01:
        read_tips.append("Some characters look garbled - avoid icons, tables or text boxes that ATS parsers can't read.")
    comps.append(Component("readability", "Readability & parsing", read_pts, 4, f"{f.word_count} words", read_tips))

    # Penalty: stuffing / copy-paste (up to -10)
    penalty = 0.0
    pen_tips: list[str] = []
    for term, count in f.stuffed_terms:
        penalty += 0.75 * (count - 6)
    if f.stuffed_terms:
        pen_tips.append("Repeated too often: " + ", ".join(f"{t} ({c}x)" for t, c in f.stuffed_terms))
    if f.skills_items > 45:
        penalty += 2.0
        pen_tips.append(f"Skills list has {f.skills_items} items - trim to the 20-30 most relevant.")
    if f.copied_phrases:
        penalty += min(4.0, 1.5 * len(f.copied_phrases))
        pen_tips.append(f"{len(f.copied_phrases)} phrase(s) copied word-for-word from the JD - rephrase in your own words.")
    penalty = min(10.0, penalty)
    comps.append(Component(
        "penalty", "Stuffing / copy-paste penalty", -penalty, 0,
        "None - reads naturally" if penalty == 0 else f"-{penalty:.1f} points", pen_tips,
    ))

    total = sum(c.score for c in comps)
    return max(0.0, min(100.0, total)), comps


# --------------------------------------------------------------------------------------------


@dataclass
class ATSReport:
    score: float
    components: list[Component]
    features: Features

    def to_dict(self) -> dict:
        f = self.features
        hits = sorted(f.keyword_hits, key=lambda h: (-h.weight, h.term))
        return {
            "score": round(self.score, 1),
            "display_score": int(round(self.score)),
            "band": score_band(self.score),
            "components": [asdict(c) | {"score": round(c.score, 1)} for c in self.components],
            "keywords": {
                "matched": [asdict(h) for h in hits if h.status == "matched"],
                "skills_only": [asdict(h) for h in hits if h.status == "skills_only"],
                "missing": [asdict(h) for h in hits if h.status == "missing"],
            },
            "bullets": [asdict(b) | {"issues": b.issues} for b in f.bullets],
            "stats": {
                "bullet_count": len(f.bullets),
                "quantified": sum(1 for b in f.bullets if b.metric),
                "xyz": sum(1 for b in f.bullets if b.xyz),
                "years_experience": f.years,
                "word_count": f.word_count,
                "title_line": f.title_line,
            },
        }


def score_band(score: float) -> dict:
    if score < 60:
        return {"id": "low", "label": "Likely filtered out"}
    if score < 75:
        return {"id": "fair", "label": "Borderline"}
    if score < 82:
        return {"id": "good", "label": "Competitive"}
    if score <= 88:
        return {"id": "great", "label": "Good to go"}
    return {"id": "high", "label": "Very high - check it still reads naturally"}


def score_resume(resume_text: str, profile: JDProfile, jd_text: str = "") -> ATSReport:
    features = extract_features(resume_text, profile, jd_text)
    score, comps = compute_score(features, profile)
    return ATSReport(score=score, components=comps, features=features)


def rescore(features: Features, profile: JDProfile) -> float:
    return compute_score(features, profile)[0]


def clone_features(features: Features) -> Features:
    return copy.deepcopy(features)


def keyword_lookup(profile: JDProfile) -> dict[str, Keyword]:
    return {kw.term.lower(): kw for kw in profile.keywords}
