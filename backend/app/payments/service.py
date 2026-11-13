"""
Payment orders for paid access to the /v1 data API, approved by hand.

There is no payment provider. The team sends an invoice letter with its payment details, and a team
member confirms the order once the money is on the account.

1. `order-create --org … --months 12 --amount 60000` -> an order awaiting payment and a letter with
   the amount, the order number and the payment details (PAYMENT_DETAILS or --details).
2. The organisation pays and quotes the order number.
3. `order-confirm <id> --received 60000 --reference "…"` after checking the account: the received
   amount must equal the order's. The order becomes paid and every non-revoked key of the
   organisation is extended by the paid months. An organisation without a key waits for
   `key-issue --order <id>`: the team e-mails the first key, as with any key.
4. `order-cancel <id>` closes an order that will not be paid.
Every step is written to payment_event; confirming an order twice is refused.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.partners.keys import MSK, end_of_day
from app.partners.store import find_organisation, issue_key

MONTHS_MAX = 36
DETAILS_MISSING = "<реквизиты для оплаты: PAYMENT_DETAILS в backend/.env или --details>"


def money(amount: Decimal | str | int) -> str:
    """Decimal('60000') -> '60000.00' (storage, JSON)."""
    return str(Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def rubles(amount: Decimal) -> str:
    """For letters: 60000 -> '60 000,00'."""
    return f"{Decimal(amount):,.2f}".replace(",", " ").replace(".", ",")


def add_months(day: dt.date, months: int) -> dt.date:
    """Calendar months; the day is clamped: 31 January + 1 month = 28 or 29 February."""
    years, month0 = divmod(day.month - 1 + months, 12)
    year, month = day.year + years, month0 + 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def extended_until(current: dt.datetime | None, months: int, *, now: dt.datetime | None = None) -> dt.datetime:
    """Expiry after paying `months`: counted from the current expiry while it is ahead, else from today
    (Moscow time); the key works through the whole last day."""
    now = now or dt.datetime.now(dt.UTC)
    start = now.astimezone(MSK).date()
    if current is not None and current > now:
        start = max(start, (current.astimezone(MSK) - dt.timedelta(seconds=1)).date())
    return end_of_day(add_months(start, months))


def parse_amount(value: str) -> Decimal:
    try:
        amount = Decimal(str(value).replace(" ", "").replace(" ", "").replace(",", "."))
    except InvalidOperation:
        raise ValueError(f"not an amount in rubles: {value!r}") from None
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("an amount has at most two decimals (kopecks)")
    if amount <= 0:
        raise ValueError("an amount must be positive")
    return amount


def order_letter(organisation: str, order_id: int, months: int, amount: Decimal, details: str) -> str:
    """Invoice letter (the team sends it from its own mailbox)."""
    return "\n".join([
        f"Счёт на доступ к API Pulse Score для организации «{organisation}»: {months} мес., {rubles(amount)} ₽.",
        f"Номер заказа: {order_id}.",
        "",
        "Реквизиты для оплаты:",
        f"    {details or DETAILS_MISSING}",
        f"В назначении платежа укажите: «Доступ к API Pulse Score, заказ № {order_id}».",
        "",
        "Когда оплата поступит, мы продлим срок действующего ключа API или пришлём новый ключ на этот адрес.",
    ])


async def _event(session: AsyncSession, order_id: int | None, event: str, outcome: str,
                 details: dict[str, Any] | None = None) -> None:
    await session.execute(
        text("INSERT INTO payment_event (order_id, event, outcome, details) VALUES (:o, :e, :out, CAST(:d AS jsonb))"),
        {"o": order_id, "e": event, "out": outcome, "d": json.dumps(details or {}, ensure_ascii=False, default=str)},
    )


async def _locked_order(session: AsyncSession, order_id: int) -> Any:
    order = (
        await session.execute(
            text(
                "SELECT o.id, o.organisation_id, org.name AS organisation, o.months, o.amount, o.currency, o.status "
                "FROM payment_order o JOIN organisation org ON org.id = o.organisation_id "
                "WHERE o.id = :id FOR UPDATE OF o"
            ),
            {"id": order_id},
        )
    ).first()
    if order is None:
        raise ValueError(f"order {order_id} not found (python -m app.cli order-list)")
    return order


async def create_order(
    session: AsyncSession, *, org_ref: str, months: int, amount: Decimal, details: str = "", note: str = "",
) -> dict[str, Any]:
    if not 1 <= months <= MONTHS_MAX:
        raise ValueError(f"months must be from 1 to {MONTHS_MAX}")
    if amount <= 0:
        raise ValueError("an amount must be positive")
    org = await find_organisation(session, org_ref)
    if not org.is_active:
        raise ValueError(f"organisation {org.name!r} is disabled (python -m app.cli org-enable)")
    title = f"Доступ к API Pulse Score на {months} мес."
    order_id = (
        await session.execute(
            text(
                "INSERT INTO payment_order (organisation_id, months, amount, description, note) "
                "VALUES (:org, :m, :a, :d, :n) RETURNING id"
            ),
            {"org": org.id, "m": months, "a": amount, "d": title, "n": note},
        )
    ).scalar_one()
    await _event(session, order_id, "order.created", "awaiting_payment", {"amount": money(amount), "months": months})
    return {
        "order_id": order_id, "organisation": org.name, "send_to": org.contact_email, "months": months,
        "amount": money(amount), "status": "awaiting_payment",
        "email_text": order_letter(org.name, order_id, months, amount, details),
    }


async def _extend_keys(session: AsyncSession, organisation_id: int, months: int) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT id, prefix, expires_at FROM api_key WHERE organisation_id = :o AND revoked_at IS NULL "
                "ORDER BY id FOR UPDATE"
            ),
            {"o": organisation_id},
        )
    ).all()
    out = []
    for r in rows:
        if r.expires_at is None:  # a key without an end date needs no extension
            out.append({"prefix": r.prefix, "expires_at": None})
            continue
        until = extended_until(r.expires_at, months)
        await session.execute(text("UPDATE api_key SET expires_at = :e WHERE id = :id"), {"e": until, "id": r.id})
        out.append({"prefix": r.prefix, "expires_at": until})
    return out


async def confirm_order(
    session: AsyncSession, order_id: int, *, received: Decimal, reference: str = "", confirmed_by: str = "",
) -> dict[str, Any]:
    """The team saw the money on the account: give the paid months."""
    order = await _locked_order(session, order_id)
    if order.status in ("paid", "fulfilled"):
        raise ValueError(f"order {order_id} is already confirmed ({order.status})")
    if order.status == "canceled":
        raise ValueError(f"order {order_id} is canceled: create a new order")
    if money(received) != money(order.amount):
        raise ValueError(
            f"received {money(received)} RUB, but order {order_id} is for {money(order.amount)} RUB: check the transfer "
            "(another amount needs another order)"
        )
    await session.execute(
        text(
            "UPDATE payment_order SET status = 'paid', paid_at = now(), payment_reference = :r, confirmed_by = :b "
            "WHERE id = :id"
        ),
        {"r": reference, "b": confirmed_by, "id": order.id},
    )
    keys = await _extend_keys(session, order.organisation_id, order.months)
    status, outcome = "paid", "paid_awaiting_key"
    if keys:
        await session.execute(
            text("UPDATE payment_order SET status = 'fulfilled', fulfilled_at = now() WHERE id = :id"), {"id": order.id}
        )
        status, outcome = "fulfilled", "keys_extended"
    await _event(session, order.id, "payment.confirmed", outcome,
                 {"received": money(received), "reference": reference, "confirmed_by": confirmed_by, "keys": keys})
    return {"order_id": order.id, "organisation": order.organisation, "status": status, "outcome": outcome,
            "keys": keys}


async def cancel_order(session: AsyncSession, order_id: int, *, reason: str = "") -> dict[str, Any]:
    order = await _locked_order(session, order_id)
    if order.status != "awaiting_payment":
        raise ValueError(f"order {order_id} is {order.status}: only an unpaid order can be canceled")
    await session.execute(
        text("UPDATE payment_order SET status = 'canceled', canceled_at = now(), cancel_reason = :r WHERE id = :id"),
        {"r": reason, "id": order.id},
    )
    await _event(session, order.id, "order.canceled", "canceled", {"reason": reason})
    return {"order_id": order.id, "status": "canceled"}


async def issue_key_for_order(session: AsyncSession, order_id: int, *, name: str = "") -> tuple[str, dict[str, Any]]:
    """The first key of an organisation, for a paid order: valid for the paid months."""
    order = await _locked_order(session, order_id)
    if order.status != "paid":
        raise ValueError(
            f"order {order_id} is {order.status}: a key is issued for a confirmed order whose organisation has no key"
        )
    until = extended_until(None, order.months)
    key, info = await issue_key(session, str(order.organisation_id), name=name or f"заказ № {order.id}",
                                expires_at=until, note=f"заказ № {order.id}")
    await session.execute(
        text("UPDATE payment_order SET status = 'fulfilled', fulfilled_at = now() WHERE id = :id"), {"id": order.id}
    )
    await _event(session, order.id, "key.issued", "key_issued", {"prefix": info["prefix"], "expires_at": until})
    return key, info


async def list_orders(session: AsyncSession, org_ref: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    conds, params = ["true"], {}
    if org_ref:
        conds.append("o.organisation_id = :org")
        params["org"] = (await find_organisation(session, org_ref)).id
    if status:
        conds.append("o.status = :status")
        params["status"] = status
    rows = await session.execute(
        text(
            f"""
            SELECT o.id, org.name AS organisation, o.months, o.amount, o.currency, o.status, o.payment_reference,
                   o.confirmed_by, o.created_at, o.paid_at, o.fulfilled_at, o.canceled_at, o.cancel_reason, o.note
            FROM payment_order o JOIN organisation org ON org.id = o.organisation_id
            WHERE {" AND ".join(conds)} ORDER BY o.id DESC
            """  # noqa: S608 - fixed fragments only
        ),
        params,
    )
    return [{**dict(r._mapping), "amount": money(r.amount)} for r in rows]
