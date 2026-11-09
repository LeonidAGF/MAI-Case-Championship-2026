"""Numeric grounding check: every number in the answer must appear in tool results or the
user's own messages (stage 7 DoD: "ни одной выдуманной цифры")."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Numbers not glued to letters (skips codes like F05, S1, W10, RU-DA).
NUMBER = re.compile(r"(?<![\w.,])[-+−]?\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?(?![\w])|(?<![\w.,])[-+−]?\d+(?:[.,]\d+)?(?![\w])")
TRIVIAL_MAX = 10  # small integers ("топ-3", "2 территории") are not claims worth checking


def parse_numbers(text: str) -> list[tuple[str, float]]:
    out = []
    for m in NUMBER.finditer(text):
        raw = m.group(0)
        s = raw.replace(" ", "").replace(" ", "").replace("−", "-").replace(",", ".")
        try:
            out.append((raw, float(s)))
        except ValueError:
            continue
    return out


@dataclass
class GroundingReport:
    checked: int
    ungrounded: list[str]

    @property
    def ok(self) -> bool:
        return not self.ungrounded

    def as_dict(self) -> dict:
        return {"ok": self.ok, "checked": self.checked, "ungrounded": self.ungrounded}


def check_grounding(answer: str, evidence: list[str]) -> GroundingReport:
    allowed = [v for blob in evidence for _, v in parse_numbers(blob)]
    checked = 0
    bad: list[str] = []
    for raw, value in parse_numbers(answer):
        if value.is_integer() and 0 <= value <= TRIVIAL_MAX:
            continue
        if 1900 <= value <= 2100 and value.is_integer():  # years
            continue
        checked += 1
        if not any(_close(value, a) for a in allowed):
            bad.append(raw)
    return GroundingReport(checked=checked, ungrounded=sorted(set(bad)))


def _close(value: float, ref: float) -> bool:
    if abs(value - ref) <= 0.051:
        return True
    if abs(abs(value) - abs(ref)) <= 0.051:  # sign written as words ("снизился на 2,3")
        return True
    return round(ref) == value or round(ref, 1) == value
