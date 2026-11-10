"""Numeric grounding check against trusted project context and tool results."""

from __future__ import annotations

import re
from dataclasses import dataclass

NUMBER = re.compile(r"(?<![\w.,])[-+−]?\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?(?![\w])|(?<![\w.,])[-+−]?\d+(?:[.,]\d+)?(?![\w])")


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
