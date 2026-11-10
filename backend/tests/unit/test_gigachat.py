"""GigaChat client and the GigaChat chat loop — mock transport / fake client, no network, no DB."""

import json
import ssl
import time
from types import SimpleNamespace

import httpx
import pytest

from app.chat import gigachat as gc
from app.chat.service import ChatService
from app.chat.tools import ChatTools
from app.core.config import BACKEND_DIR, Settings
from app.modelcfg import load_model_config
from app.profiles.service import ProfileResolver

CERTS = BACKEND_DIR / "config" / "certs"
CFG = load_model_config(BACKEND_DIR / "config" / "model.default.yaml")


def make_settings(**overrides: object) -> Settings:
    """Hermetic settings: the developer's backend/.env (real key, other model) is not read."""
    return Settings(_env_file=None, **overrides)


def token_response(expires_at: float | None = None) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok", "expires_at": int(expires_at or (time.time() + 1800) * 1000)})


def completion(content: str = "", finish: str = "stop", **message: object) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": content, **message}, "index": 0, "finish_reason": finish}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        "model": "GigaChat-3-Pro",
        "object": "chat.completion",
    }


def make_client(handler, max_retries: int = 0) -> gc.GigaChatClient:  # noqa: ANN001
    return gc.GigaChatClient(
        credentials="Y2lkOnNlY3JldA==", scope="GIGACHAT_API_PERS", auth_url="https://auth.test/api/v2/oauth",
        base_url="https://api.test/v1", transport=httpx.MockTransport(handler), max_retries=max_retries,
    )


async def test_token_is_cached_and_refreshed_once_on_401() -> None:
    calls = {"oauth": 0, "chat": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.test":
            calls["oauth"] += 1
            assert request.headers["Authorization"] == "Basic Y2lkOnNlY3JldA=="
            assert len(request.headers["RqUID"]) == 36 and b"scope=GIGACHAT_API_PERS" in request.content
            return token_response()
        calls["chat"] += 1
        assert request.url.path == "/v1/chat/completions" and request.headers["Authorization"] == "Bearer tok"
        assert json.loads(request.content)["model"] == "m"
        return httpx.Response(401) if calls["chat"] == 2 else httpx.Response(200, json=completion("ok"))

    client = make_client(handler)
    await client.chat({"model": "m", "messages": []})
    await client.chat({"model": "m", "messages": []})  # 401 -> new token -> retried
    await client.chat({"model": "m", "messages": []})
    assert calls == {"oauth": 2, "chat": 4}
    await client.close()


async def test_expiry_in_seconds_is_accepted() -> None:
    oauth = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.test":
            oauth.append(1)
            return httpx.Response(200, json={"access_token": "tok", "expires_at": int(time.time() + 1800)})
        return httpx.Response(200, json=completion("ok"))

    client = make_client(handler)
    for _ in range(3):
        await client.chat({"model": "m", "messages": []})
    assert len(oauth) == 1
    await client.close()


@pytest.mark.parametrize(
    ("status", "error"),
    [(402, gc.GigaChatQuotaExceeded), (404, gc.GigaChatBadRequest), (422, gc.GigaChatBadRequest),
     (429, gc.GigaChatRateLimited), (403, gc.GigaChatAuthError), (503, gc.GigaChatUnavailable)],
)
async def test_error_mapping(status: int, error: type) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return token_response() if request.url.host == "auth.test" else httpx.Response(status, json={"message": "no"})

    client = make_client(handler)
    with pytest.raises(error):
        await client.chat({"model": "m", "messages": []})
    await client.close()


async def test_oauth_rejection_and_missing_key() -> None:
    client = make_client(lambda r: httpx.Response(401, json={"message": "Can't decode 'Authorization' header"}))
    with pytest.raises(gc.GigaChatAuthError, match="decode"):
        await client.token()
    await client.close()
    with pytest.raises(gc.GigaChatNotConfigured):
        gc.GigaChatClient(credentials=" ", scope="s", auth_url="https://a", base_url="https://b")


async def test_transient_errors_are_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(gc.asyncio, "sleep", no_sleep)
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.test":
            return token_response()
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("connection reset")
        return httpx.Response(503) if len(attempts) == 2 else httpx.Response(200, json=completion("ok"))

    client = make_client(handler, max_retries=2)
    assert (await client.chat({"model": "m", "messages": []}))["choices"][0]["message"]["content"] == "ok"
    assert len(attempts) == 3
    await client.close()


def test_repo_certificates_cover_gigachat_chain(tmp_path) -> None:  # noqa: ANN001
    names = [p.name for p in gc.ca_files(CERTS)]
    assert names == ["russian_trusted_root_ca.crt", "russian_trusted_sub_ca.crt"]
    subjects = {dict(x[0] for x in c["subject"]).get("commonName") for c in gc.build_ssl_context(CERTS).get_ca_certs()}
    assert {"Russian Trusted Root CA", "Russian Trusted Sub CA"} <= subjects
    der = tmp_path / "root.cer"  # DER files work too
    der.write_bytes(ssl.PEM_cert_to_DER_cert((CERTS / "russian_trusted_root_ca.crt").read_text()))
    assert any(
        dict(x[0] for x in c["subject"]).get("commonName") == "Russian Trusted Root CA"
        for c in gc.build_ssl_context(der).get_ca_certs()
    )


def test_functions_and_arguments() -> None:
    tools = [{"name": "get_index", "description": "d", "parameters": {"type": "object", "properties": {}}},
             {"name": "other", "description": "e", "parameters": {"type": "object", "properties": {}}}]
    fns = gc.to_functions(tools, {"get_index": [{"request": "q", "params": {}}]})
    assert fns[0] == {**tools[0], "few_shot_examples": [{"request": "q", "params": {}}]}
    assert fns[1] == tools[1] and "few_shot_examples" not in tools[0]
    assert gc.parse_arguments('{"territory": "Ахты"}') == {"territory": "Ахты"}
    assert gc.parse_arguments({"a": 1}) == {"a": 1} and gc.parse_arguments(None) == {}
    assert gc.parse_arguments("{broken") == "{broken"


# --------------------------------------------------------------------------- chat loop
class FakeGigaChat:
    def __init__(self, script):  # noqa: ANN001
        self.script, self.payloads = script, []

    async def chat(self, payload: dict) -> dict:
        self.payloads.append(json.loads(json.dumps(payload, ensure_ascii=False)))
        return self.script.pop(0)(payload)

    async def close(self) -> None:
        pass


def events(chunks: list[str]) -> list[tuple[str, dict]]:
    out = []
    for chunk in chunks:
        name, _, data = chunk.strip().partition("\ndata: ")
        out.append((name.removeprefix("event: "), json.loads(data)))
    return out


@pytest.fixture
def chat(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    def build(script, synthetic: bool = False, **overrides):  # noqa: ANN001, ANN202
        settings = make_settings(gigachat_credentials="Y2lkOnNlY3JldA==", **overrides)
        svc = ChatService(SimpleNamespace(settings=settings, cfg=CFG))
        svc._gigachat = FakeGigaChat(script)

        async def context_block(question, profile):  # noqa: ANN001, ANN202
            return "Профиль пользователя: «Семья с детьми».", [], synthetic

        async def execute(self, name, args, profile):  # noqa: ANN001, ANN202
            self.sources["Атлас доступности СКФО"] = ""
            return {"territory": args.get("territory"), "total": 61.3, "rank": 42, "cluster_size": 180}

        monkeypatch.setattr(svc, "_context_block", context_block)
        monkeypatch.setattr(ChatTools, "execute", execute)
        return svc

    return build


PROFILE = ProfileResolver(CFG, default_preset="family").resolve("family")


async def test_function_call_round_trip(chat) -> None:  # noqa: ANN001
    def round1(payload: dict) -> dict:
        return completion("", "function_call", functions_state_id="77d0-state",
                          function_call={"id": "c1", "name": "get_index", "arguments": {"territory": "Ахты"}})

    def round2(payload: dict) -> dict:
        echo, result = payload["messages"][-2:]
        assert echo == {"role": "assistant", "content": "", "functions_state_id": "77d0-state",
                        "function_call": {"id": "c1", "name": "get_index", "arguments": {"territory": "Ахты"}}}
        assert result["role"] == "function" and result["name"] == "get_index"
        assert json.loads(result["content"])["total"] == 61.3
        return completion("Индекс Ахты — 61,3 из 100, место 42 из 180.")

    svc = chat([round1, round2])
    out = events([c async for c in svc.stream([{"role": "user", "content": "Какой индекс у Ахты?"}], PROFILE)])
    names = [n for n, _ in out]
    assert names == ["meta", "tool_call", "tool_result", "citations", "delta", "done"]
    assert out[0][1] == {"model": "GigaChat-3-Pro", "profile": "family"}
    assert out[1][1] == {"id": "c1", "name": "get_index", "input": {"territory": "Ахты"}} and out[2][1]["ok"]
    assert out[4][1]["text"].startswith("Индекс Ахты — 61,3")
    done = out[-1][1]
    assert done["grounding"]["ok"] and done["stop_reason"] == "stop"
    assert done["usage"] == {"input_tokens": 200, "output_tokens": 20}

    first = svc._gigachat.payloads[0]
    assert first["model"] == "GigaChat-3-Pro" and first["function_call"] == "auto" and first["max_tokens"] == 2048
    assert "temperature" not in first
    assert first["messages"][0]["role"] == "system" and "Профиль пользователя" in first["messages"][0]["content"]
    assert [m["role"] for m in first["messages"]].count("system") == 1
    assert [f["name"] for f in first["functions"]] == ["get_index", "compare_territories", "run_scenario"]
    assert all(f.get("few_shot_examples") and f["parameters"]["type"] == "object" for f in first["functions"])


async def test_arguments_as_json_string_and_ungrounded_answer(chat) -> None:  # noqa: ANN001
    def round1(payload: dict) -> dict:
        return completion("", "function_call", function_call={"name": "get_index", "arguments": '{"territory": "Ведено"}'})

    def round2(payload: dict) -> dict:
        return completion("В Ведено до больницы 137 минут.")

    out = events([c async for c in chat([round1, round2]).stream([{"role": "user", "content": "Ведено?"}], PROFILE)])
    assert next(d for n, d in out if n == "tool_call")["input"] == {"territory": "Ведено"}
    assert out[-1][1]["grounding"]["ungrounded"] == ["137"]
    assert "137" not in next(d for n, d in out if n == "delta")["text"]


async def test_synthetic_data_notice_is_added_by_code(chat) -> None:  # noqa: ANN001
    svc = chat([lambda p: completion("Сравнивать город с селом нельзя.")], synthetic=True)
    out = events([c async for c in svc.stream([{"role": "user", "content": "Город или село?"}], PROFILE)])
    text = next(d for n, d in out if n == "delta")["text"]
    assert text == "Внимание: в Атласе сейчас синтетические демо-данные, а не реальная статистика.\n\nСравнивать город с селом нельзя."
    assert out[-1][1]["grounding"]["ok"]
    no_demo = chat([lambda p: completion("Сравнивать город с селом нельзя.")])
    out = events([c async for c in no_demo.stream([{"role": "user", "content": "Город или село?"}], PROFILE)])
    assert next(d for n, d in out if n == "delta")["text"] == "Сравнивать город с селом нельзя."


async def test_blacklist_is_a_refusal_without_canned_text(chat) -> None:  # noqa: ANN001
    svc = chat([lambda p: completion("Не люблю менять тему разговора.", "blacklist")], gigachat_temperature=0.3)
    out = events([c async for c in svc.stream([{"role": "user", "content": "..."}], PROFILE)])
    assert ("error", {"code": "refusal", "message": "Ассистент не может ответить на этот запрос"}) in out
    assert not any(n == "delta" for n, _ in out) and out[-1][0] == "done"
    assert svc._gigachat.payloads[0]["temperature"] == 0.3


async def test_api_failures_end_the_stream_with_an_error(chat) -> None:  # noqa: ANN001
    def quota(payload: dict) -> dict:
        raise gc.GigaChatQuotaExceeded("HTTP 402", status=402)

    out = events([c async for c in chat([quota]).stream([{"role": "user", "content": "Привет"}], PROFILE)])
    assert [n for n, _ in out] == ["meta", "error"] and out[-1][1]["code"] == "llm_quota_exceeded"

    svc = chat([])
    svc._gigachat = None
    svc.settings = make_settings(gigachat_credentials=None)
    out = events([c async for c in svc.stream([{"role": "user", "content": "Привет"}], PROFILE)])
    assert out[-1] == ("error", {"code": "chat_unavailable", "message": "Чат не настроен: нет ключа GigaChat (GIGACHAT_CREDENTIALS)"})


def test_status_and_blank_env_values() -> None:
    svc = ChatService(SimpleNamespace(settings=make_settings(gigachat_credentials="x"), cfg=CFG))
    assert svc.status() == "enabled (GigaChat-3-Pro)"
    blank = make_settings(gigachat_credentials="", gigachat_ca_bundle="", gigachat_temperature="")
    assert blank.gigachat_credentials is None and blank.gigachat_ca_bundle is None and blank.gigachat_temperature is None
    assert ChatService(SimpleNamespace(settings=blank, cfg=CFG)).status() == "enabled, credentials not found (GIGACHAT_CREDENTIALS)"
    assert ChatService(SimpleNamespace(settings=make_settings(chat_enabled=False), cfg=CFG)).status() == "disabled"
