"""Deterministic guard against hallucinated financial detail (no third-party imports).

The LLM may only report positions, support/resistance and target prices that the source text
actually states. Anything whose numbers do not appear in the text is dropped and noted;
evidence must be a verbatim excerpt of the source.
"""
from __future__ import annotations

import re

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUM.findall(text or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            continue
    return out


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def number_grounded(value: float | None, text_numbers: list[float]) -> bool:
    return value is not None and any(abs(value - n) <= max(1e-9, abs(n) * 1e-9) for n in text_numbers)


def apply_grounding(data: dict, source_text: str) -> tuple[dict, list[str]]:
    """Return (cleaned_data, notes). ``data`` is a validated extraction as a plain dict."""
    notes: list[str] = []
    out = dict(data)
    nums = numbers_in(source_text)
    squashed = _squash(source_text)

    def keep_levels(key: str) -> list[float]:
        kept = [v for v in out.get(key) or [] if number_grounded(v, nums)]
        dropped = [v for v in out.get(key) or [] if v not in kept]
        if dropped:
            notes.append(f"dropped ungrounded {key}: {dropped}")
        return kept

    out["support_levels"] = keep_levels("support_levels")
    out["resistance_levels"] = keep_levels("resistance_levels")

    if out.get("target_price") is not None and not number_grounded(out["target_price"], nums):
        notes.append(f"dropped ungrounded target_price: {out['target_price']}")
        out["target_price"] = None

    if out.get("evidence_text") and _squash(out["evidence_text"]) not in squashed:
        notes.append("evidence_text is not a verbatim excerpt of the source; cleared")
        out["evidence_text"] = None

    pos = out.get("position")
    if pos and pos.get("type"):
        ev = pos.get("evidence_text")
        if not ev or _squash(ev) not in squashed:
            notes.append("position has no verbatim evidence in source; cleared")
            out["position"] = None
        else:
            pos = dict(pos)
            for k in ("quantity", "entry_price"):
                if pos.get(k) is not None and not number_grounded(pos[k], nums):
                    notes.append(f"dropped ungrounded position.{k}: {pos[k]}")
                    pos[k] = None
            out["position"] = pos
    elif pos is not None:
        out["position"] = None
    return out, notes
