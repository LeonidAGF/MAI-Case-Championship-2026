"""
Organisations and API keys in the database: the team's server commands and the request check.

There is no sign-up and no admin UI. After an organisation's payment is confirmed, a team member
runs `org-add` (once) and `key-issue`, then e-mails the key to the organisation's contact address
from their own mailbox (see keys.email_text). `key-extend` moves the expiry date after the next
payment, so the organisation keeps the same key; `key-revoke` and `org-disable` cut access off.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import Forbidden, Unauthorized
from app.partners.keys import key_hash, key_prefix, looks_like_key, new_key, normalize_email

DEFAULT_SCOPES = ("datasets",)
SCOPES = {"datasets": "чтение наборов данных и результатов сценариев"}
TOUCH_EVERY_S = 60  # last_used_at is written at most once a minute per key
BEARER = {"WWW-Authenticate": "Bearer"}


@dataclass(frozen=True)
class Principal:
    """Who is calling: the key and its organisation."""

    key_id: int
    prefix: str
    key_name: str
    scopes: tuple[str, ...]
    rate_limit_per_minute: int | None
    expires_at: dt.datetime | None
    organisation_id: int
    organisation: str


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# --- request check ----------------------------------------------------------------------------
async def authenticate(sessionmaker: async_sessionmaker[AsyncSession], raw: str) -> Principal:
    if not looks_like_key(raw):
        raise Unauthorized("Ключ API не подходит: он должен начинаться с pulse_", code="api_key_invalid", headers=BEARER)
    async with sessionmaker() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT k.id, k.prefix, k.name, k.scopes, k.rate_limit_per_minute, k.expires_at, k.revoked_at,
                           k.last_used_at, o.id AS org_id, o.name AS org_name, o.is_active
                    FROM api_key k JOIN organisation o ON o.id = k.organisation_id
                    WHERE k.key_hash = :h
                    """
                ),
                {"h": key_hash(raw)},
            )
        ).first()
        if row is None:
            raise Unauthorized("Ключ API не найден", code="api_key_invalid", headers=BEARER)
        now = _now()
        if row.revoked_at is not None:
            raise Forbidden("Ключ API отозван: обратитесь к команде Pulse Score за новым", code="api_key_revoked")
        if row.expires_at is not None and row.expires_at <= now:
            raise Forbidden("Срок действия ключа API истёк: обратитесь к команде Pulse Score",
                            code="api_key_expired")
        if not row.is_active:
            raise Forbidden("Доступ организации к API отключён", code="organisation_disabled")
        if row.last_used_at is None or (now - row.last_used_at).total_seconds() > TOUCH_EVERY_S:
            await session.execute(text("UPDATE api_key SET last_used_at = now() WHERE id = :id"), {"id": row.id})
            await session.commit()
        return Principal(row.id, row.prefix, row.name, tuple(row.scopes or ()), row.rate_limit_per_minute,
                         row.expires_at, row.org_id, row.org_name)


# --- server commands --------------------------------------------------------------------------
async def find_organisation(session: AsyncSession, ref: str) -> Any:
    """By id or exact name."""
    row = (
        await session.execute(
            text("SELECT id, name, contact_email, is_active FROM organisation WHERE name = :n OR CAST(id AS text) = :n"),
            {"n": ref.strip()},
        )
    ).first()
    if row is None:
        raise ValueError(f"organisation {ref!r} not found (python -m app.cli org-list)")
    return row


async def add_organisation(session: AsyncSession, name: str, email: str, comment: str = "") -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise ValueError("the organisation name is empty")
    email = normalize_email(email)
    exists = (await session.execute(text("SELECT 1 FROM organisation WHERE name = :n"), {"n": name})).first()
    if exists:
        raise ValueError(f"organisation {name!r} already exists")
    oid = (
        await session.execute(
            text("INSERT INTO organisation (name, contact_email, comment) VALUES (:n, :e, :c) RETURNING id"),
            {"n": name, "e": email, "c": comment},
        )
    ).scalar_one()
    return {"organisation_id": oid, "name": name, "contact_email": email}


async def list_organisations(session: AsyncSession) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            """
            SELECT o.id, o.name, o.contact_email, o.comment, o.is_active, o.created_at,
                   count(k.id) FILTER (WHERE k.revoked_at IS NULL AND (k.expires_at IS NULL OR k.expires_at > now()))
                       AS active_keys,
                   max(k.last_used_at) AS last_used_at
            FROM organisation o LEFT JOIN api_key k ON k.organisation_id = o.id
            GROUP BY o.id ORDER BY o.name
            """
        )
    )
    return [dict(r._mapping) for r in rows]


async def set_organisation_active(session: AsyncSession, ref: str, active: bool) -> dict[str, Any]:
    org = await find_organisation(session, ref)
    await session.execute(text("UPDATE organisation SET is_active = :a WHERE id = :id"), {"a": active, "id": org.id})
    return {"organisation_id": org.id, "name": org.name, "is_active": active}


async def issue_key(
    session: AsyncSession,
    org_ref: str,
    *,
    name: str = "",
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
    expires_at: dt.datetime | None = None,
    rate_limit_per_minute: int | None = None,
    note: str = "",
) -> tuple[str, dict[str, Any]]:
    """(the key — show it once and never store it, the key's record)."""
    org = await find_organisation(session, org_ref)
    if not org.is_active:
        raise ValueError(f"organisation {org.name!r} is disabled (python -m app.cli org-enable)")
    unknown = sorted(set(scopes) - set(SCOPES))
    if unknown:
        raise ValueError(f"unknown scopes {unknown}; known: {sorted(SCOPES)}")
    if expires_at is not None and expires_at <= _now():
        raise ValueError("the expiry date is in the past")
    key = new_key()
    kid = (
        await session.execute(
            text(
                """
                INSERT INTO api_key (organisation_id, name, prefix, key_hash, scopes, rate_limit_per_minute, note, expires_at)
                VALUES (:org, :name, :prefix, :hash, :scopes, :rl, :note, :exp) RETURNING id
                """
            ),
            {"org": org.id, "name": name, "prefix": key_prefix(key), "hash": key_hash(key), "scopes": list(scopes),
             "rl": rate_limit_per_minute, "note": note, "exp": expires_at},
        )
    ).scalar_one()
    return key, {
        "key_id": kid, "prefix": key_prefix(key), "organisation": org.name, "send_to": org.contact_email,
        "scopes": list(scopes), "expires_at": expires_at, "rate_limit_per_minute": rate_limit_per_minute, "note": note,
    }


async def list_keys(session: AsyncSession, org_ref: str | None = None) -> list[dict[str, Any]]:
    where, params = "true", {}
    if org_ref:
        params["org"] = (await find_organisation(session, org_ref)).id
        where = "k.organisation_id = :org"
    rows = await session.execute(
        text(
            f"""
            SELECT k.prefix, o.name AS organisation, k.name, k.scopes, k.note, k.rate_limit_per_minute,
                   k.created_at, k.expires_at, k.last_used_at, k.revoked_at,
                   CASE WHEN k.revoked_at IS NOT NULL THEN 'revoked'
                        WHEN k.expires_at IS NOT NULL AND k.expires_at <= now() THEN 'expired'
                        WHEN NOT o.is_active THEN 'organisation_disabled'
                        ELSE 'active' END AS status
            FROM api_key k JOIN organisation o ON o.id = k.organisation_id
            WHERE {where} ORDER BY o.name, k.created_at
            """  # noqa: S608 - `where` is one of two fixed fragments
        ),
        params,
    )
    return [dict(r._mapping) for r in rows]


async def _key(session: AsyncSession, prefix: str) -> Any:
    row = (
        await session.execute(
            text("SELECT id, prefix, revoked_at FROM api_key WHERE prefix = :p"), {"p": prefix.strip()[:16]}
        )
    ).first()
    if row is None:
        raise ValueError(f"key {prefix!r} not found (python -m app.cli key-list)")
    return row


async def revoke_key(session: AsyncSession, prefix: str) -> dict[str, Any]:
    key = await _key(session, prefix)
    if key.revoked_at is None:
        await session.execute(text("UPDATE api_key SET revoked_at = now() WHERE id = :id"), {"id": key.id})
    return {"prefix": key.prefix, "revoked": True}


async def extend_key(session: AsyncSession, prefix: str, expires_at: dt.datetime | None) -> dict[str, Any]:
    key = await _key(session, prefix)
    if key.revoked_at is not None:
        raise ValueError(f"key {key.prefix} is revoked: issue a new one")
    if expires_at is not None and expires_at <= _now():
        raise ValueError("the expiry date is in the past")
    await session.execute(text("UPDATE api_key SET expires_at = :e WHERE id = :id"), {"e": expires_at, "id": key.id})
    return {"prefix": key.prefix, "expires_at": expires_at}
