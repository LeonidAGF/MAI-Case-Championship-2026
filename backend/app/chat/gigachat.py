"""GigaChat (Sber) REST client for the chat assistant: OAuth token cache + chat completions.

API reference: https://developers.sber.ru/docs/ru/gigachat/api/reference/rest/post-chat
* OAuth: POST GIGACHAT_AUTH_URL with ``Authorization: Basic <authorization key>``, a fresh
  ``RqUID`` (UUID4) and form field ``scope``; the access token lives 30 minutes
  (``expires_at`` is a unix time in milliseconds).
* Chat: POST GIGACHAT_BASE_URL/chat/completions with ``functions`` (custom function calling).
  The assistant message that carries ``function_call`` (and ``functions_state_id``) goes back
  into the history unchanged, followed by ``{"role": "function", "name", "content": "<JSON>"}``.
* TLS: both hosts chain to the Russian Trusted Root CA (Минцифры), which certifi and Linux
  images do not ship. The CA files from config/certs are added to an SSL context that only this
  client uses; the OS store (e.g. a Windows machine with the certificate installed) also counts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
import uuid
from pathlib import Path
from typing import Any

import certifi
import httpx

log = logging.getLogger(__name__)

TOKEN_REFRESH_MARGIN_S = 120.0
RETRY_STATUS = {429, 500, 502, 503, 504}
CA_SUFFIXES = {".crt", ".cer", ".pem"}


class GigaChatError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GigaChatNotConfigured(GigaChatError):
    """No authorization key (GIGACHAT_CREDENTIALS)."""


class GigaChatAuthError(GigaChatError):
    """The key or scope was rejected."""


class GigaChatQuotaExceeded(GigaChatError):
    """HTTP 402: the token package of the model is used up."""


class GigaChatRateLimited(GigaChatError):
    pass


class GigaChatBadRequest(GigaChatError):
    """400/404/413/422: malformed request, unknown model, invalid parameters."""


class GigaChatUnavailable(GigaChatError):
    """Network, TLS or 5xx failure."""


def ca_files(path: Path | None) -> list[Path]:
    if path is None:
        return []
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.suffix.lower() in CA_SUFFIXES)
    if path.is_file():
        return [path]
    log.warning("GIGACHAT_CA_BUNDLE %s does not exist", path)
    return []


def build_ssl_context(ca_bundle: Path | None) -> ssl.SSLContext:
    """OS trust store + certifi + the Russian Trusted CA files (PEM or DER)."""
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cafile=certifi.where())
    for path in ca_files(ca_bundle):
        data = path.read_bytes()
        try:
            if b"-----BEGIN CERTIFICATE-----" in data:
                ctx.load_verify_locations(cadata=data.decode("ascii", errors="ignore"))
            else:
                ctx.load_verify_locations(cadata=data)
        except ssl.SSLError as exc:
            log.warning("cannot load CA certificate %s: %s", path, exc)
    return ctx


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300]
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error_description") or body.get("error") or body)[:300]
    return str(body)[:300]


def _transport_message(exc: httpx.TransportError) -> str:
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return (
            "TLS: сертификат GigaChat не прошёл проверку — нужен Russian Trusted Root CA "
            "(GIGACHAT_CA_BUNDLE, по умолчанию backend/config/certs)"
        )
    return f"{type(exc).__name__}: {text[:200]}"


class GigaChatClient:
    def __init__(
        self,
        *,
        credentials: str,
        scope: str,
        auth_url: str,
        base_url: str,
        verify: ssl.SSLContext | bool = True,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not credentials or not credentials.strip():
            raise GigaChatNotConfigured("GIGACHAT_CREDENTIALS is not set")
        self._credentials = credentials.strip()
        self.scope = scope
        self.auth_url = auth_url
        self.base_url = base_url.rstrip("/")
        self.max_retries = max(0, max_retries)
        self._http = httpx.AsyncClient(verify=verify, timeout=timeout_s, transport=transport)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        delay = 0.5
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._http.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                tls = "CERTIFICATE_VERIFY_FAILED" in str(exc)
                if tls or attempt >= self.max_retries:
                    raise GigaChatUnavailable(_transport_message(exc)) from exc
            else:
                if resp.status_code not in RETRY_STATUS or attempt >= self.max_retries:
                    return resp
            await asyncio.sleep(delay)
            delay *= 3
        raise GigaChatUnavailable("retries exhausted")  # pragma: no cover - loop always returns or raises

    async def token(self, *, refresh: bool = False) -> str:
        async with self._lock:
            if not refresh and self._token and time.time() < self._token_expires_at - TOKEN_REFRESH_MARGIN_S:
                return self._token
            resp = await self._send(
                "POST",
                self.auth_url,
                headers={
                    "Authorization": f"Basic {self._credentials}",
                    "RqUID": str(uuid.uuid4()),
                    "Accept": "application/json",
                },
                data={"scope": self.scope},
            )
            if resp.status_code in (400, 401, 403):
                raise GigaChatAuthError(f"OAuth {resp.status_code}: {_detail(resp)}", status=resp.status_code)
            if resp.status_code == 429:
                raise GigaChatRateLimited("OAuth rate limited", status=429)
            if resp.status_code != 200:
                raise GigaChatUnavailable(f"OAuth HTTP {resp.status_code}: {_detail(resp)}", status=resp.status_code)
            body = resp.json()
            expires = float(body.get("expires_at") or 0)
            if expires > 10_000_000_000:  # milliseconds (the documented unit)
                expires /= 1000.0
            self._token = str(body["access_token"])
            self._token_expires_at = expires if expires > 0 else time.time() + 25 * 60
            return self._token

    async def _authorized(
        self, method: str, path: str, *, headers: dict[str, str] | None = None, **kwargs: Any
    ) -> httpx.Response:
        for refresh in (False, True):  # a 401 means the token was revoked or expired early: re-auth once
            token = await self.token(refresh=refresh)
            resp = await self._send(
                method,
                f"{self.base_url}{path}",
                headers={"Accept": "application/json", **(headers or {}), "Authorization": f"Bearer {token}"},
                **kwargs,
            )
            if resp.status_code != 401:
                break
        return resp

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        resp = await self._authorized(
            "POST", "/chat/completions", content=body, headers={"Content-Type": "application/json"}
        )
        status = resp.status_code
        if status == 200:
            return resp.json()
        message = f"HTTP {status}: {_detail(resp)}"
        if status in (401, 403):
            raise GigaChatAuthError(message, status=status)
        if status == 402:
            raise GigaChatQuotaExceeded(message, status=status)
        if status == 429:
            raise GigaChatRateLimited(message, status=status)
        if status in (400, 404, 413, 422):
            raise GigaChatBadRequest(message, status=status)
        raise GigaChatUnavailable(message, status=status)

    async def models(self) -> list[str]:
        resp = await self._authorized("GET", "/models")
        if resp.status_code != 200:
            raise GigaChatUnavailable(f"models HTTP {resp.status_code}: {_detail(resp)}", status=resp.status_code)
        return [str(m.get("id")) for m in resp.json().get("data", []) if m.get("type", "chat") == "chat"]

    async def close(self) -> None:
        await self._http.aclose()


def client_from_settings(settings: Any) -> GigaChatClient:
    """Raises GigaChatNotConfigured without GIGACHAT_CREDENTIALS."""
    if settings.gigachat_verify_ssl:
        verify: ssl.SSLContext | bool = build_ssl_context(settings.gigachat_ca_bundle)
    else:
        log.warning("GIGACHAT_VERIFY_SSL=false: TLS certificates of GigaChat are NOT checked")
        verify = False
    secret = settings.gigachat_credentials
    return GigaChatClient(
        credentials=secret.get_secret_value() if secret else "",
        scope=settings.gigachat_scope,
        auth_url=settings.gigachat_auth_url,
        base_url=settings.gigachat_base_url,
        verify=verify,
        timeout_s=settings.gigachat_timeout_s,
        max_retries=settings.gigachat_max_retries,
    )


async def check(settings: Any) -> dict[str, Any]:
    """Credentials, TLS and model availability — free calls only (OAuth + model list)."""
    out: dict[str, Any] = {
        "provider": "gigachat", "model": settings.gigachat_model, "scope": settings.gigachat_scope,
        "ca_files": [p.name for p in ca_files(settings.gigachat_ca_bundle)],
    }
    try:
        client = client_from_settings(settings)
    except GigaChatNotConfigured as exc:
        return {**out, "ok": False, "error": str(exc)}
    try:
        await client.token()
        models = await client.models()
        out.update(token="ok", models=models, ok=settings.gigachat_model in models)
        if not out["ok"]:
            out["error"] = f"model {settings.gigachat_model} is not available for this key"
    except GigaChatError as exc:
        out.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        await client.close()
    return out


def to_functions(tools: list[dict[str, Any]], examples: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Function definitions (name, description, parameters) + their few-shot examples."""
    return [{**t, "few_shot_examples": examples[t["name"]]} if examples.get(t["name"]) else dict(t) for t in tools]


def parse_arguments(raw: Any) -> Any:
    """``function_call.arguments`` is an object, but the spec allows JSON wrapped in a string."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw  # the tool validator reports it back to the model
    return raw if raw is not None else {}
