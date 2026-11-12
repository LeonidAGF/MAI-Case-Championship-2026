"""
Name normalization and municipality matching (cross-check of names / OKTMO, BE2 stage 1).

Statistics files name municipalities in many ways: «Курский район», «Курский муниципальный
округ», «МО "Курский муниципальный округ"», «г. Ставрополь», «городской округ город
Махачкала». We reduce them to a core name + kind (city okrug vs district) and match within
the region; OKTMO wins when both sides have it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

QUOTES = re.compile(r"[«»\"'“”„`]")
SPACES = re.compile(r"\s+")

CITY_PATTERNS = [
    r"городской округ с внутригородским делением",
    r"городского округа",
    r"городской округ",
    r"\bг\.\s*о\.",
    r"\bго\b",
    r"\bгород\b",
    r"\bгорода\b",
    r"\bгор\.",
    r"\bг\.",
]
DISTRICT_PATTERNS = [
    r"муниципальный район",
    r"муниципального района",
    r"муниципальный округ",
    r"муниципального округа",
    r"\bм\.\s*р\.",
    r"\bмр\b",
    r"\bр-н\b",
    r"\bрайон\b",
    r"\bрайона\b",
]
GENERIC_PATTERNS = [r"муниципальное образование", r"\bмо\b", r"\bм\.\s*о\."]

REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "RU-DA": ("дагестан",),
    "RU-IN": ("ингушетия",),
    "RU-KB": ("кабардино-балкар", "кбр"),
    "RU-KC": ("карачаево-черкес", "кчр"),
    "RU-SE": ("северная осетия", "алания", "рсо"),
    "RU-CE": ("чеченская", "чечня", "чеченск"),
    "RU-STA": ("ставропольский", "ставрополье"),
}
# First two OKTMO/OKATO digits of SKFO subjects.
OKTMO_REGION = {"82": "RU-DA", "26": "RU-IN", "83": "RU-KB", "91": "RU-KC", "90": "RU-SE", "96": "RU-CE", "07": "RU-STA"}

KIND_OF_MUNICIPALITY = {"city_okrug": "city", "municipal_district": "district", "municipal_okrug": "district"}


def normalize_search(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower().replace("ё", "е")
    s = QUOTES.sub("", s)
    return SPACES.sub(" ", s).strip()


def normalize_municipality(name: str) -> tuple[str, str | None]:
    """-> (core name, kind hint 'city' | 'district' | None)."""
    s = normalize_search(name)
    s = re.sub(r"\(.*?\)", " ", s)
    kind = None
    for pat in CITY_PATTERNS:
        if re.search(pat, s):
            kind = "city"
            s = re.sub(pat, " ", s)
    for pat in DISTRICT_PATTERNS:
        if re.search(pat, s):
            kind = kind or "district"
            s = re.sub(pat, " ", s)
    for pat in GENERIC_PATTERNS:
        s = re.sub(pat, " ", s)
    s = re.sub(r"[^\w\- ]", " ", s)
    return SPACES.sub(" ", s).strip(" -"), kind


def digits(value: object) -> str | None:
    if value is None:
        return None
    d = re.sub(r"\D", "", str(value).split(".")[0] if isinstance(value, float) else str(value))
    return d or None


def region_code_from(name: str | None = None, oktmo: str | None = None) -> str | None:
    code = digits(oktmo)
    if code and len(code) >= 2:
        code = code.zfill(8) if len(code) in (7, 10) else code
        if code[:2] in OKTMO_REGION:
            return OKTMO_REGION[code[:2]]
    if name:
        n = normalize_search(name)
        if n.upper() in OKTMO_REGION.values():
            return n.upper()
        for rc, aliases in REGION_ALIASES.items():
            if any(a in n for a in aliases):
                return rc
    return None


@dataclass(frozen=True)
class MunicipalityRef:
    id: int
    name: str
    region_code: str | None
    kind: str  # city | district
    core: str
    oktmo: str | None


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


class MunicipalityMatcher:
    def __init__(self, refs: list[MunicipalityRef]) -> None:
        self.refs = refs
        self.by_oktmo = {r.oktmo[:8]: r for r in refs if r.oktmo}

    def match(
        self, name: str | None, *, region_code: str | None = None, oktmo: str | None = None
    ) -> tuple[MunicipalityRef | None, str]:
        code = digits(oktmo)
        if code and len(code) >= 8 and code[:8] in self.by_oktmo:
            return self.by_oktmo[code[:8]], "oktmo"
        if not name:
            return None, "none"
        region_code = region_code or region_code_from(None, oktmo)
        core, kind = normalize_municipality(name)
        if not core:
            return None, "none"
        pool = [r for r in self.refs if region_code is None or r.region_code == region_code]

        exact = [r for r in pool if r.core == core]
        picked = self._pick(exact, kind)
        if picked:
            return picked, "exact"
        if len(exact) > 1:
            return None, "ambiguous"

        fuzzy = []
        for r in pool:
            cp = _common_prefix(core, r.core)
            # «карачаевск» ~ «карачаевский» (0.83 of the longer) but not «курский сельсовет» ~ «курский».
            if cp >= 5 and cp / min(len(core), len(r.core)) >= 0.8 and cp / max(len(core), len(r.core)) >= 0.7:
                fuzzy.append(r)
        picked = self._pick(fuzzy, kind)
        if picked:
            return picked, "prefix"
        return None, "ambiguous" if len(fuzzy) > 1 else "none"

    @staticmethod
    def _pick(cands: list[MunicipalityRef], kind: str | None) -> MunicipalityRef | None:
        if kind:
            same_kind = [r for r in cands if r.kind == kind]
            if len(same_kind) == 1:
                return same_kind[0]
        return cands[0] if len(cands) == 1 else None
