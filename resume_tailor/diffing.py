"""Line + word level diffs between resume versions, for the live preview."""

from __future__ import annotations

import difflib
import re


def _words(s: str) -> list[str]:
    return re.findall(r"\s+|[^\s]+", s)


def word_diff(old: str, new: str) -> list[dict]:
    a, b = _words(old), _words(new)
    ops: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            ops.append({"op": "equal", "text": "".join(a[i1:i2])})
        else:
            if i2 > i1:
                ops.append({"op": "delete", "text": "".join(a[i1:i2])})
            if j2 > j1:
                ops.append({"op": "insert", "text": "".join(b[j1:j2])})
    return ops


def line_diff(old: str, new: str) -> list[dict]:
    """Rows of {type: equal|insert|delete|replace, text|old,new,words}."""
    a = old.split("\n")
    b = new.split("\n")
    rows: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            rows.extend({"type": "equal", "text": t} for t in a[i1:i2])
        elif tag == "delete":
            rows.extend({"type": "delete", "text": t} for t in a[i1:i2])
        elif tag == "insert":
            rows.extend({"type": "insert", "text": t} for t in b[j1:j2])
        else:
            old_block, new_block = a[i1:i2], b[j1:j2]
            # Pair up lines that are similar enough for a word-level diff.
            used = set()
            for nl in new_block:
                best, best_ratio = None, 0.0
                for idx, ol in enumerate(old_block):
                    if idx in used:
                        continue
                    r = difflib.SequenceMatcher(a=ol, b=nl, autojunk=False).ratio()
                    if r > best_ratio:
                        best, best_ratio = idx, r
                if best is not None and best_ratio >= 0.45:
                    for idx in range(len(old_block)):
                        if idx < best and idx not in used:
                            rows.append({"type": "delete", "text": old_block[idx]})
                            used.add(idx)
                    used.add(best)
                    rows.append({"type": "replace", "old": old_block[best], "new": nl, "words": word_diff(old_block[best], nl)})
                else:
                    rows.append({"type": "insert", "text": nl})
            for idx, ol in enumerate(old_block):
                if idx not in used:
                    rows.append({"type": "delete", "text": ol})
    return rows


def diff_stats(rows: list[dict]) -> dict:
    return {
        "changed": sum(1 for r in rows if r["type"] == "replace"),
        "added": sum(1 for r in rows if r["type"] == "insert" and r["text"].strip()),
        "removed": sum(1 for r in rows if r["type"] == "delete" and r["text"].strip()),
    }
