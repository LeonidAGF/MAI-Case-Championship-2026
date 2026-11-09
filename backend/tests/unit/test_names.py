import pytest

from app.etl.names import MunicipalityMatcher, MunicipalityRef, normalize_municipality, region_code_from

REFS = [
    MunicipalityRef(1, "Курский муниципальный округ", "RU-STA", "district", "курский", None),
    MunicipalityRef(2, "городской округ Ставрополь", "RU-STA", "city", "ставрополь", None),
    MunicipalityRef(3, "Карачаевский район", "RU-KC", "district", "карачаевский", None),
    MunicipalityRef(4, "Карачаевский городской округ", "RU-KC", "city", "карачаевский", None),
    MunicipalityRef(5, "Ногайский район", "RU-KC", "district", "ногайский", None),
    MunicipalityRef(6, "Ногайский район", "RU-DA", "district", "ногайский", None),
    MunicipalityRef(7, "Черкесский городской округ", "RU-KC", "city", "черкесский", "91701000"),
    MunicipalityRef(8, "городской округ Махачкала", "RU-DA", "city", "махачкала", None),
]
M = MunicipalityMatcher(REFS)


@pytest.mark.parametrize(
    ("name", "core", "kind"),
    [
        ("Курский муниципальный округ", "курский", "district"),
        ("Курский район", "курский", "district"),
        ('МО "Курский муниципальный округ"', "курский", "district"),
        ("г. Ставрополь", "ставрополь", "city"),
        ("Городской округ «город Махачкала»", "махачкала", "city"),
        ("Карачаевский городской округ", "карачаевский", "city"),
        ("Ёлкинский р-н", "елкинский", "district"),
    ],
)
def test_normalize(name: str, core: str, kind: str) -> None:
    assert normalize_municipality(name) == (core, kind)


@pytest.mark.parametrize(
    ("name", "region", "oktmo", "expected"),
    [
        ("Курский район", "RU-STA", None, 1),  # pre-reform name of a municipal okrug
        ("город Ставрополь", None, None, 2),
        ("Карачаевский район", "RU-KC", None, 3),
        ("г. Карачаевск", "RU-KC", None, 4),  # city form -> city okrug (prefix match)
        ("г. Черкесск", "RU-KC", None, 7),
        ("Ногайский район", "RU-DA", None, 6),  # same name in two subjects -> region decides
        ("что угодно", None, "91701000", 7),  # OKTMO wins
        ("ГО город Махачкала", "RU-DA", None, 8),
    ],
)
def test_match(name: str, region: str | None, oktmo: str | None, expected: int) -> None:
    ref, _ = M.match(name, region_code=region, oktmo=oktmo)
    assert ref is not None and ref.id == expected


def test_no_false_positives() -> None:
    assert M.match("Ногайский район")[0] is None  # ambiguous without region
    assert M.match("Курский сельсовет", region_code="RU-STA")[0] is None
    assert M.match("Тмутаракань", region_code="RU-STA")[0] is None


@pytest.mark.parametrize(
    ("name", "oktmo", "code"),
    [("Республика Дагестан", None, "RU-DA"), ("Кабардино-Балкарская Республика", None, "RU-KB"),
     ("Республика Северная Осетия — Алания", None, "RU-SE"), (None, "07701000", "RU-STA"), (None, "96000000", "RU-CE"),
     ("Москва", None, None)],
)
def test_region_codes(name: str | None, oktmo: str | None, code: str | None) -> None:
    assert region_code_from(name, oktmo) == code
