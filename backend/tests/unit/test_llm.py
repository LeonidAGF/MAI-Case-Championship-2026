"""Claude client helpers and the chat loop with a scripted fake client: no network, no DB, no cost."""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.chat import llm
from app.chat.service import SYNTHETIC_NOTICE, UNGROUNDED_TEXT, ChatService, normalize_history
from app.chat.tools import ChatTools
from app.core.config import BACKEND_DIR, Settings
from app.modelcfg import load_model_config
from app.profiles.service import ProfileResolver

CFG = load_model_config(BACKEND_DIR / "config" / "model.default.yaml")
PROFILE = ProfileResolver(CFG, default_preset="family").resolve("family")
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def make_settings(**overrides: object) -> Settings:
    """Hermetic settings: the developer's backend/.env (real key, other model) is not read."""
    return Settings(_env_file=None, **overrides)


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def tool(name: str, args: dict, block_id: str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=args)


def message(*blocks: SimpleNamespace, stop: str = "end_turn", tokens: tuple[int, int] = (100, 10)) -> SimpleNamespace:
    usage = SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1], cache_read_input_tokens=50,
                            cache_creation_input_tokens=0)
    return SimpleNamespace(content=list(blocks), stop_reason=stop, stop_details=None, usage=usage)


class FakeMessages:
    def __init__(self, owner: "FakeClaude", endpoint: str) -> None:
        self.owner, self.endpoint = owner, endpoint

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.owner.requests.append({**kwargs, "messages": list(kwargs["messages"]), "_endpoint": self.endpoint})
        step = self.owner.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step(kwargs) if callable(step) else step


class FakeClaude:
    def __init__(self, script: list) -> None:
        self.script, self.requests = script, []
        self.messages = FakeMessages(self, "messages")
        self.beta = SimpleNamespace(messages=FakeMessages(self, "beta"))

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
        settings = make_settings(anthropic_api_key="sk-ant-test", **overrides)
        svc = ChatService(SimpleNamespace(settings=settings, cfg=CFG))
        svc._client = FakeClaude(script)

        async def context_block(question, profile):  # noqa: ANN001, ANN202
            return "Профиль пользователя: «Семья с детьми».", [], synthetic

        async def execute(self, name, args, profile):  # noqa: ANN001, ANN202
            self.sources["Атлас доступности СКФО"] = ""
            return {"territory": args.get("territory") or args.get("territories"), "total": 61.3, "rank": 42,
                    "cluster_size": 180}

        monkeypatch.setattr(svc, "_context_block", context_block)
        monkeypatch.setattr(ChatTools, "execute", execute)
        return svc

    return build


async def run(svc: ChatService, history: list[dict[str, str]]) -> list[tuple[str, dict]]:
    return events([c async for c in svc.stream(history, PROFILE)])


# --------------------------------------------------------------------------- request shape
def test_request_kwargs_and_fallbacks_per_model() -> None:
    sonnet = make_settings()
    kwargs = llm.request_kwargs(sonnet, system=[{"type": "text", "text": "s"}], messages=[], tools=[])
    assert kwargs["model"] == "claude-sonnet-5-5" and kwargs["output_config"] == {"effort": "medium"}
    assert kwargs["max_tokens"] == 16000 and kwargs["cache_control"] == {"type": "ephemeral"}
    assert kwargs["betas"] == ["server-side-fallback-2026-07-01"] and kwargs["fallbacks"] == "default"
    assert "thinking" not in kwargs and "temperature" not in kwargs  # adaptive thinking is the model default
    haiku = llm.request_kwargs(make_settings(llm_model="claude-haiku-5-5", llm_effort="low"), system=[], messages=[], tools=[])
    assert "betas" not in haiku and "fallbacks" not in haiku and haiku["output_config"] == {"effort": "low"}
    off = llm.request_kwargs(make_settings(llm_fallbacks=False), system=[], messages=[], tools=[])
    assert "fallbacks" not in off
    assert llm.uses_fallbacks(make_settings(llm_model="claude-opus-5-5"))


def test_echo_content_after_mid_output_fallback() -> None:
    plain = [SimpleNamespace(type="thinking"), text("a"), tool("get_index", {}, "t1")]
    assert llm.echo_content(plain) == plain  # unchanged, thinking included
    marker = SimpleNamespace(type="fallback")
    blocks = [SimpleNamespace(type="thinking"), text("partial"), tool("x", {}, "t0"), marker,
              SimpleNamespace(type="thinking"), tool("get_index", {}, "t1")]
    echoed = llm.echo_content(blocks)
    assert [b.type for b in echoed] == ["text", "fallback", "thinking", "tool_use"]
    assert echoed[1] is marker and echoed[-1].id == "t1"


def test_errors_are_classified_without_raw_api_text() -> None:
    def status(cls, code: int, headers: dict | None = None, body: dict | None = None):  # noqa: ANN001, ANN202
        return cls("api says", response=httpx2.Response(code, headers=headers or {}, request=REQUEST), body=body)

    model = "claude-sonnet-5-5"
    assert llm.classify(llm.LLMNotConfigured("x"), model).code == "chat_unavailable"
    assert llm.classify(status(anthropic.AuthenticationError, 401), model).code == "chat_unavailable"
    assert "региона" in llm.classify(status(anthropic.PermissionDeniedError, 403), model).message
    assert llm.classify(status(anthropic.NotFoundError, 404), model).message.startswith("Модель claude-sonnet-5-5")
    limited = llm.classify(status(anthropic.RateLimitError, 429, {"retry-after": "7"}), model)
    assert (limited.code, limited.retry_after) == ("llm_rate_limited", 7)
    assert limited.payload() == {"code": "llm_rate_limited", "message": limited.message, "retry_after": 7}
    credit = status(anthropic.BadRequestError, 400, body={"error": {"message": "Your credit balance is too low"}})
    assert llm.classify(credit, model).code == "llm_quota_exceeded"
    assert llm.classify(status(anthropic.InternalServerError, 500), model).code == "llm_unavailable"
    assert llm.classify(anthropic.APITimeoutError(request=REQUEST), model).code == "llm_timeout"
    assert llm.classify(anthropic.APIConnectionError(request=REQUEST), model).code == "llm_unavailable"
    assert llm.classify(TypeError("Could not resolve authentication method"), model).code == "chat_unavailable"
    assert llm.classify(RuntimeError("boom"), model).code == "llm_error"
    assert "request_id" in llm.describe(status(anthropic.InternalServerError, 500))


def test_normalize_history_drops_empty_and_merges_unanswered_turns() -> None:
    raw = [
        {"role": "assistant", "content": "Здравствуйте!"},  # cannot start the conversation
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "  "},  # a reply that never arrived
        {"role": "user", "content": "Какой индекс у Ахты?"},
    ]
    assert normalize_history(raw) == [{"role": "user", "content": "Привет\n\nКакой индекс у Ахты?"}]
    assert normalize_history([{"role": "assistant", "content": "x"}]) == []


def test_status_and_blank_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert ChatService(SimpleNamespace(settings=make_settings(anthropic_api_key="k"), cfg=CFG)).status() == \
        "enabled (claude-sonnet-5-5, effort medium)"
    blank = make_settings(anthropic_api_key="  ")
    assert blank.anthropic_api_key is None
    assert ChatService(SimpleNamespace(settings=blank, cfg=CFG)).status() == "enabled, credentials not found (ANTHROPIC_API_KEY)"
    assert ChatService(SimpleNamespace(settings=make_settings(chat_enabled=False), cfg=CFG)).status() == "disabled"


# --------------------------------------------------------------------------- chat loop
async def test_tool_round_trip_with_parallel_calls(chat) -> None:  # noqa: ANN001
    def round2(kwargs: dict) -> SimpleNamespace:
        echo, results = kwargs["messages"][-2:]
        assert echo["role"] == "assistant" and [b.type for b in echo["content"]] == ["text", "tool_use", "tool_use"]
        assert results["role"] == "user" and [r["tool_use_id"] for r in results["content"]] == ["t1", "t2"]
        assert json.loads(results["content"][0]["content"])["total"] == 61.3 and results["content"][1]["is_error"] is False
        return message(text("Индекс Ахты — 61,3 из 100, место 42 из 180."))

    svc = chat([
        message(text("Сейчас посмотрю."), tool("get_index", {"territory": "Ахты"}, "t1"),
                tool("get_index", {"territory": "Рутул"}, "t2"), stop="tool_use"),
        round2,
    ])
    out = await run(svc, [{"role": "user", "content": "Какой индекс у Ахты и Рутула?"}])
    assert [n for n, _ in out] == ["meta", "tool_call", "tool_result", "tool_call", "tool_result", "citations", "delta", "done"]
    assert out[0][1] == {"model": "claude-sonnet-5-5", "profile": "family"}
    assert out[1][1] == {"id": "t1", "name": "get_index", "input": {"territory": "Ахты"}}
    assert out[6][1]["text"] == "Индекс Ахты — 61,3 из 100, место 42 из 180."  # preamble text is not part of the answer
    done = out[-1][1]
    assert done["grounding"]["ok"] and done["stop_reason"] == "end_turn"
    assert done["usage"] == {"input_tokens": 200, "output_tokens": 20, "cache_read_input_tokens": 100,
                             "cache_creation_input_tokens": 0}

    first = svc._client.requests[0]
    assert first["_endpoint"] == "beta" and first["fallbacks"] == "default"
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"} and "Профиль пользователя" in first["system"][1]["text"]
    assert first["messages"] == [{"role": "user", "content": "Какой индекс у Ахты и Рутула?"}]
    assert [t["name"] for t in first["tools"]] == ["get_index", "compare_territories", "run_scenario"]
    assert all(t["input_schema"]["type"] == "object" for t in first["tools"])


async def test_haiku_uses_the_plain_endpoint(chat) -> None:  # noqa: ANN001
    svc = chat([message(text("Сравнивать город с селом нельзя."))], llm_model="claude-haiku-5-5")
    out = await run(svc, [{"role": "user", "content": "Город или село?"}])
    assert out[-1][0] == "done" and svc._client.requests[0]["_endpoint"] == "messages"


async def test_ungrounded_answer_and_synthetic_notice(chat) -> None:  # noqa: ANN001
    out = await run(chat([message(text("В Ведено до больницы 137 минут."))]), [{"role": "user", "content": "Ведено?"}])
    assert out[-1][1]["grounding"]["ungrounded"] == ["137"]
    assert next(d for n, d in out if n == "delta")["text"] == UNGROUNDED_TEXT
    out = await run(chat([message(text("Сравнивать город с селом нельзя."))], synthetic=True),
                    [{"role": "user", "content": "Город или село?"}])
    assert next(d for n, d in out if n == "delta")["text"] == SYNTHETIC_NOTICE + "Сравнивать город с селом нельзя."


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (message(stop="end_turn"), "llm_empty_answer"),  # a turn that ends without text
        (message(SimpleNamespace(type="thinking"), stop="max_tokens"), "llm_truncated"),
        (message(text("частичный ответ"), stop="refusal"), "refusal"),
    ],
)
async def test_turn_without_an_answer_ends_with_one_error(chat, reply, code) -> None:  # noqa: ANN001
    out = await run(chat([reply]), [{"role": "user", "content": "?"}])
    assert [n for n, _ in out] == ["meta", "error"] and out[-1][1]["code"] == code


async def test_tool_rounds_limit_and_api_errors(chat) -> None:  # noqa: ANN001
    loop = [message(tool("get_index", {"territory": "Ахты"}, f"t{i}"), stop="tool_use") for i in range(2)]
    out = await run(chat(loop, chat_max_tool_rounds=2), [{"role": "user", "content": "Ахты"}])
    assert out[-1] == ("error", {"code": "llm_too_many_steps", "message": "Ассистент не уложился в лимит шагов — уточните вопрос"})

    overloaded = anthropic.InternalServerError("overloaded", response=httpx2.Response(529, request=REQUEST), body=None)
    out = await run(chat([overloaded]), [{"role": "user", "content": "Привет"}])
    assert [n for n, _ in out] == ["meta", "error"] and out[-1][1]["code"] == "llm_unavailable"


async def test_missing_key_degrades_to_an_error_event(chat, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    svc = chat([])
    svc._client, svc.settings = None, make_settings(anthropic_api_key=None)
    out = await run(svc, [{"role": "user", "content": "Привет"}])
    assert out[-1] == ("error", {"code": "chat_unavailable", "message": "Чат не настроен: нет ключа Anthropic API (ANTHROPIC_API_KEY)"})
