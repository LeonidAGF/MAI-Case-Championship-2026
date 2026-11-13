"""Data API for organisations and its manually approved payments: keys, table formats, paid periods.

Formulas and formats on literal values only (no datasets). The flow on the real database is in
tests/integration/test_real_v1.py.
"""

import datetime as dt
from decimal import Decimal

import pytest

from app.partners.keys import (
    MSK, email_text, end_of_day, key_from_headers, key_hash, key_prefix, looks_like_key, new_key, normalize_email,
)
from app.partners.tables import BOM, Column, Table, cell, to_csv, to_json
from app.payments.service import DETAILS_MISSING, add_months, extended_until, money, order_letter, parse_amount, rubles


def test_keys() -> None:
    a, b = new_key(), new_key()
    assert a != b and looks_like_key(a) and len(a) == 49 and a.startswith("pulse_")
    assert key_prefix(a) == a[:14] and len(key_hash(a)) == 64 and key_hash(a) == key_hash(a) != key_hash(b)
    assert not looks_like_key("pulse_short") and not looks_like_key("Bearer " + a)
    assert key_from_headers(a, "other") == a and key_from_headers(None, f" {a} ") == a and key_from_headers("", None) is None
    assert normalize_email(" Info@Akhty.RU ") == "info@akhty.ru"
    with pytest.raises(ValueError):
        normalize_email("not an address")


def test_paid_until_is_the_end_of_the_last_day_in_moscow() -> None:
    assert end_of_day(dt.date(2027, 11, 13)) == dt.datetime(2027, 11, 14, tzinfo=MSK)
    letter = email_text("Администрация района", "pulse_x", "https://api.example/", end_of_day(dt.date(2027, 11, 13)))
    assert "до 13.11.2027 включительно" in letter and "https://api.example/v1/me" in letter
    assert "Authorization: Bearer pulse_x" in letter


def test_paid_period_arithmetic() -> None:
    assert add_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)
    assert add_months(dt.date(2028, 1, 31), 1) == dt.date(2028, 2, 29)
    assert add_months(dt.date(2026, 11, 13), 12) == dt.date(2027, 11, 13)
    now = dt.datetime(2026, 11, 13, 9, 0, tzinfo=dt.UTC)
    assert extended_until(None, 12, now=now) == end_of_day(dt.date(2027, 11, 13))
    ahead = end_of_day(dt.date(2027, 3, 1))  # still valid: the new months follow the paid ones
    assert extended_until(ahead, 12, now=now) == end_of_day(dt.date(2028, 3, 1))
    lapsed = end_of_day(dt.date(2026, 10, 1))  # expired: counted from today
    assert extended_until(lapsed, 1, now=now) == end_of_day(dt.date(2026, 12, 13))


def test_amounts_and_invoice_letter() -> None:
    assert parse_amount("60 000,50") == Decimal("60000.50") and money(Decimal("60000")) == "60000.00"
    for bad in ("12.345", "0", "-5", "много"):
        with pytest.raises(ValueError):
            parse_amount(bad)
    assert rubles(Decimal("60000")) == "60 000,00"
    letter = order_letter("Администрация района", 15, 12, Decimal("60000"), "р/с … в банке …")
    assert "12 мес., 60 000,00 ₽" in letter and "заказ № 15" in letter and "р/с … в банке …" in letter
    assert DETAILS_MISSING in order_letter("Администрация района", 15, 12, Decimal("60000"), "")


def test_csv_for_programs_and_for_excel() -> None:
    cols = [Column("name", "Населённый пункт"), Column("minutes", "Время, мин", "float"),
            Column("met", "В нормативе", "bool"), Column("status", "Норматив", words={"met": "в нормативе"}),
            Column("population", "Население, чел.", "int")]
    table = Table("t", "Т", cols, [
        {"name": "Ахты", "minutes": 8.25, "met": True, "status": "met", "population": 12000},
        {"name": "=HYPERLINK(1)", "minutes": None, "met": False, "status": None, "population": None},
    ])
    machine = to_csv(table, excel=False).decode("utf-8").splitlines()
    assert machine == ["name,minutes,met,status,population", "Ахты,8.25,true,met,12000", "'=HYPERLINK(1),,false,,"]
    raw = to_csv(table, excel=True)
    assert raw.startswith(BOM)
    excel = raw[len(BOM):].decode("utf-8").splitlines()
    assert excel[0] == "Населённый пункт;Время, мин;В нормативе;Норматив;Население, чел."
    assert excel[1] == "Ахты;8,25;да;в нормативе;12000"
    assert cell(-0.0, Column("x", "x", "float"), excel=False) == "0"
    assert cell(dt.date(2026, 11, 11), Column("d", "d", "date"), excel=True) == "2026-11-11"
    js = to_json(table)
    assert js["total"] == 2 and js["items"][1] == {"name": "=HYPERLINK(1)", "minutes": None, "met": False,
                                                   "status": None, "population": None}
