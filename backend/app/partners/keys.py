"""
API keys of organisations: format, hashing, request headers, the e-mail text.

A key is "pulse_" + 43 random URL-safe characters (256 bits), made by `python -m app.cli key-issue`
after the organisation's payment is confirmed. It is shown once. The database keeps only its
SHA-256 hash (a random 256-bit secret needs no slow password hash) and its first 14 characters
(`prefix`), which identify the key in lists and when revoking it. Requests send it as
`Authorization: Bearer <key>` or `X-API-Key: <key>`. Never log or print a key anywhere else.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets

KEY_START = "pulse_"
PREFIX_LEN = len(KEY_START) + 8
_KEY_RE = re.compile(r"^pulse_[A-Za-z0-9_-]{43}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MSK = dt.timezone(dt.timedelta(hours=3), "MSK")  # Moscow time, no daylight saving


def new_key() -> str:
    return KEY_START + secrets.token_urlsafe(32)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def key_prefix(key: str) -> str:
    return key[:PREFIX_LEN]


def looks_like_key(value: str) -> bool:
    return bool(_KEY_RE.fullmatch(value))


def key_from_headers(bearer: str | None, header: str | None) -> str | None:
    """The key from `Authorization: Bearer …` (preferred) or `X-API-Key`."""
    for value in (bearer, header):
        if value and value.strip():
            return value.strip()
    return None


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if not _EMAIL_RE.fullmatch(email):
        raise ValueError(f"not an e-mail address: {value!r}")
    return email


def end_of_day(day: dt.date) -> dt.datetime:
    """A key "valid until 13.11.2027" works through that whole day, Moscow time."""
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tzinfo=MSK)


def email_text(organisation: str, key: str, base_url: str, expires_at: dt.datetime | None) -> str:
    """Letter to the organisation with its new key (the team sends it from its own mailbox)."""
    base = base_url.rstrip("/")
    until = (
        f"Ключ действует до {(expires_at.astimezone(MSK) - dt.timedelta(seconds=1)):%d.%m.%Y} включительно."
        if expires_at else "Ключ действует бессрочно, пока его не отзовут."
    )
    return "\n".join([
        f"Ключ доступа к API Pulse Score для организации «{organisation}».",
        "",
        f"Ключ: {key}",
        until,
        "",
        "Передавайте ключ в заголовке каждого запроса:",
        f"    Authorization: Bearer {key}",
        "",
        f"Проверка ключа: {base}/v1/me",
        f"Список наборов данных и их колонок: {base}/v1/datasets",
        "Пример — время доезда скорой по Дагестану в таблицу для Excel:",
        f'    curl -H "Authorization: Bearer {key}" -o ambulance.csv "{base}/v1/datasets/ambulance.csv?region=RU-DA&excel=true"',
        "",
        "Храните ключ как пароль: в менеджере паролей или в настройках сервера, не в коде сайта и не в",
        "общих документах. Если ключ мог попасть к посторонним, напишите нам: мы отзовём его и выпустим новый.",
    ])
