"""What-if analysis: which specific changes would move the ATS score, and by exactly how much.

Each lever is simulated on a copy of the resume's extracted features and re-scored with the same
engine, so "add Kubernetes + Terraform -> 85" is the real score you would get, not a guess.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .ats import QUANT_TARGET, ATSReport, Features, clone_features, rescore
from .keywords import JDProfile


@dataclass
class Lever:
    id: str
    kind: str  # keyword | keyword_context | title | impact | bullets | sections | penalty
    title: str
    description: str
    delta: float
    projected: float
    needs_confirmation: bool  # True when the change is a claim only you can verify
    keyword: str | None = None
    importance: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["delta"] = round(self.delta, 1)
        d["projected"] = round(self.projected, 1)
        return d


@dataclass
class Bundle:
    target: float
    reachable: bool
    lever_ids: list[str]
    projected: float
    summary: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["projected"] = round(self.projected, 1)
        return d


@dataclass
class WhatIf:
    current: float
    levers: list[Lever] = field(default_factory=list)
    bundles: list[Bundle] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "current": round(self.current, 1),
            "levers": [lv.to_dict() for lv in self.levers],
            "top5": [lv.to_dict() for lv in self.levers[:5]],
            "bundles": [b.to_dict() for b in self.bundles],
        }


# --- Mutations ------------------------------------------------------------------------------


def _apply(features: Features, lever: Lever) -> None:
    """Mutate features in place as if the lever had been applied."""
    if lever.kind in {"keyword", "keyword_context"} and lever.keyword:
        for h in features.keyword_hits:
            if h.term == lever.keyword:
                h.status, h.credit = "matched", 1.0
                h.context_count = max(1, h.context_count)
                h.count = max(1, h.count)
    elif lever.kind == "title":
        features.title_score = 1.0
    elif lever.kind == "impact":
        need = int(round(QUANT_TARGET * len(features.bullets) + 0.4999))
        have = sum(1 for b in features.bullets if b.metric)
        for b in features.bullets:
            if have >= need:
                break
            if not b.metric:
                b.metric = True
                have += 1
    elif lever.kind == "bullets":
        for b in features.bullets:
            b.action_verb, b.weak = True, False
            b.words = min(max(b.words, 6), 38)
    elif lever.kind == "sections":
        features.contact = {"email": True, "phone": True, "link": True}
        features.sections = set(features.sections) | {"experience", "education", "skills"}
    elif lever.kind == "penalty":
        features.stuffed_terms = []
        features.copied_phrases = []
        features.skills_items = min(features.skills_items, 40)


def _simulate(base: Features, profile: JDProfile, levers: list[Lever]) -> float:
    f = clone_features(base)
    for lv in levers:
        _apply(f, lv)
    return rescore(f, profile)


# --- Candidate levers ----------------------------------------------------------------------------


def _candidates(report: ATSReport, profile: JDProfile, declined: set[str]) -> list[Lever]:
    f = report.features
    comps = {c.id: c for c in report.components}
    out: list[Lever] = []

    for h in f.keyword_hits:
        if h.term.lower() in declined:
            continue
        label = h.importance.replace("_", " ")
        if h.status == "missing":
            out.append(Lever(
                id=f"kw:{h.term}", kind="keyword", keyword=h.term, importance=h.importance,
                title=f"Add “{h.term}” ({label})",
                description=(
                    f"The JD lists {h.term} as {label}. Your resume doesn't show it. Only add it if you've "
                    f"genuinely used it - tell me where, and I'll write an honest bullet."
                ),
                delta=0, projected=0, needs_confirmation=True,
            ))
        elif h.status == "skills_only":
            out.append(Lever(
                id=f"ctx:{h.term}", kind="keyword_context", keyword=h.term, importance=h.importance,
                title=f"Show “{h.term}” in an experience bullet",
                description=(
                    f"{h.term} is only in your skills list. Mentioning where you actually used it in a bullet "
                    f"makes it count fully for ATS and recruiters."
                ),
                delta=0, projected=0, needs_confirmation=False,
            ))

    if comps["title"].score < comps["title"].max and profile.job_title:
        out.append(Lever(
            id="title", kind="title",
            title=f"Use the title “{profile.job_title}” in your headline",
            description="ATS and recruiters look for the exact job title. Use it in your summary/headline if it honestly describes your work.",
            delta=0, projected=0, needs_confirmation=True,
        ))

    if comps["impact"].score < comps["impact"].max - 0.05 and f.bullets:
        need = int(round(QUANT_TARGET * len(f.bullets) + 0.4999)) - sum(1 for b in f.bullets if b.metric)
        if need > 0:
            out.append(Lever(
                id="impact", kind="impact",
                title=f"Add real numbers to {need} more bullet{'s' if need != 1 else ''}",
                description="Answer the metric questions (team size, % improvement, users, time saved, $). Real numbers only.",
                delta=0, projected=0, needs_confirmation=True,
            ))

    if comps["bullets"].score < comps["bullets"].max - 0.3:
        out.append(Lever(
            id="bullets", kind="bullets", title="Open every bullet with a strong action verb",
            description="Replace openings like “Responsible for” or “Worked on” with verbs like Led, Built, Reduced.",
            delta=0, projected=0, needs_confirmation=False,
        ))

    if comps["sections"].score < comps["sections"].max:
        out.append(Lever(
            id="sections", kind="sections", title="Complete contact info and standard sections",
            description=comps["sections"].detail,
            delta=0, projected=0, needs_confirmation=False,
        ))

    if comps["penalty"].score < 0:
        out.append(Lever(
            id="penalty", kind="penalty", title="Remove keyword stuffing / copied JD phrases",
            description="; ".join(comps["penalty"].tips) or comps["penalty"].detail,
            delta=0, projected=0, needs_confirmation=False,
        ))
    return out


def analyze(report: ATSReport, profile: JDProfile, declined: set[str] | None = None,
            targets: tuple[float, ...] = (82.0, 85.0)) -> WhatIf:
    declined = {d.lower() for d in (declined or set())}
    base = report.features
    current = report.score
    levers = _candidates(report, profile, declined)
    for lv in levers:
        lv.projected = _simulate(base, profile, [lv])
        lv.delta = lv.projected - current
    levers = [lv for lv in levers if lv.delta > 0.05]
    levers.sort(key=lambda lv: (-lv.delta, lv.needs_confirmation, lv.id))

    bundles: list[Bundle] = []
    for target in targets:
        if current >= target:
            bundles.append(Bundle(target, True, [], current, f"Already at {current:.0f} - no changes needed for {target:.0f}."))
            continue
        chosen: list[Lever] = []
        remaining = list(levers)
        score = current
        # Greedy: prefer changes that don't need new claims, then biggest gain.
        while remaining and score < target:
            best = max(remaining, key=lambda lv: (_simulate(base, profile, chosen + [lv]) - score) * (1.0 if not lv.needs_confirmation else 0.92))
            chosen.append(best)
            remaining.remove(best)
            score = _simulate(base, profile, chosen)
        reachable = score >= target
        names = " + ".join(lv.keyword or lv.title for lv in chosen)
        summary = (
            f"{names} → {score:.0f}" if reachable
            else f"Even with every lever ({len(chosen)} changes) the score reaches {score:.0f}, not {target:.0f}."
        )
        bundles.append(Bundle(target, reachable, [lv.id for lv in chosen], score, summary))

    return WhatIf(current=current, levers=levers, bundles=bundles)
