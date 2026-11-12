"""Chat orchestration with a scripted fake Claude client (no network, no cost) and the real tools."""

import json
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def message(*blocks: SimpleNamespace, stop: str = "end_turn") -> SimpleNamespace:
    usage = SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(content=list(blocks), stop_reason=stop, stop_details=None, usage=usage)


def call(name: str, args: dict, block_id: str = "tu_1") -> SimpleNamespace:
    return message(SimpleNamespace(type="tool_use", id=block_id, name=name, input=args), stop="tool_use")


class FakeMessages:
    def __init__(self, owner: "FakeClaude") -> None:
        self.owner = owner

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.owner.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.owner.script.pop(0)(kwargs)


class FakeClaude:
    """Each script step gets the request kwargs and returns a message."""

    def __init__(self, script: list) -> None:
        self.script, self.requests = script, []
        self.messages = FakeMessages(self)
        self.beta = SimpleNamespace(messages=FakeMessages(self))

    async def close(self) -> None:
        pass


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    for chunk in raw.strip().split("\n\n"):
        name, _, data = chunk.partition("\ndata: ")
        events.append((name.removeprefix("event: "), json.loads(data)))
    return events


def last_results(kwargs: dict) -> list[dict]:
    message = kwargs["messages"][-1]
    assert message["role"] == "user"
    return [json.loads(b["content"]) for b in message["content"] if b["type"] == "tool_result"]


@pytest.fixture
def claude(container):  # noqa: ANN001, ANN201
    def use(script):  # noqa: ANN001, ANN202
        container.chat._client = FakeClaude(script)
        return container.chat._client

    yield use
    container.chat._client = None


async def test_tool_loop_grounded_answer(client, claude) -> None:  # noqa: ANN001
    def round2(kwargs: dict) -> SimpleNamespace:
        res = last_results(kwargs)[0]
        return message(text(f"Индекс Махачкалы — {str(res['total']).replace('.', ',')} из 100, "
                            f"место {res['rank']} из {res['cluster_size']}."))

    fake = claude([lambda kwargs: call("get_index", {"territory": "Махачкала"}), round2])
    r = await client.post("/chat", json={"messages": [{"role": "user", "content": "Какой индекс у Махачкалы?"}], "profile": "family"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    names = [e[0] for e in events]
    assert events[0] == ("meta", {"model": "claude-sonnet-5-5", "profile": "family"}) and names[-1] == "done"
    assert ("tool_call", "get_index") in [(n, d.get("name")) for n, d in events]
    done = events[-1][1]
    assert done["grounding"]["ok"] is True and done["stop_reason"] == "end_turn"
    assert done["usage"]["input_tokens"] == 200 and done["usage"]["output_tokens"] == 40
    cites = next(d for n, d in events if n == "citations")["sources"]
    assert any("Атлас" in s["title"] for s in cites)
    answer = next(d["text"] for n, d in events if n == "delta")
    # The integration database holds synthetic test values, so the code-added notice must appear.
    assert answer.startswith("Внимание: в Атласе сейчас синтетические демо-данные") and "Индекс Махачкалы" in answer

    first = fake.requests[0]  # request shape of the Messages API
    assert first["model"] == "claude-sonnet-5-5" and first["output_config"] == {"effort": "medium"}
    assert first["fallbacks"] == "default" and first["cache_control"] == {"type": "ephemeral"}
    assert [t["name"] for t in first["tools"]] == ["get_index", "compare_territories", "run_scenario"]
    context = first["system"][1]["text"]
    assert "синтетические" in context and "Данные: в базе есть значения признаков" in context
    assert first["messages"] == [{"role": "user", "content": "Какой индекс у Махачкалы?"}]
    second = fake.requests[1]
    assert second["messages"][1]["role"] == "assistant" and second["messages"][2]["content"][0]["tool_use_id"] == "tu_1"


async def test_ungrounded_number_is_flagged(client, claude) -> None:  # noqa: ANN001
    claude([lambda kwargs: message(text("В Ведено до больницы 137 минут."))])
    r = await client.post("/chat", json={"messages": [{"role": "user", "content": "Сколько до больницы в Ведено?"}]})
    done = parse_sse(r.text)[-1][1]
    assert done["grounding"]["ok"] is False and "137" in done["grounding"]["ungrounded"]
    assert "137" not in "".join(d["text"] for n, d in parse_sse(r.text) if n == "delta")


async def test_scenario_tools_and_cross_cluster(client, claude) -> None:  # noqa: ANN001
    seen = {}

    def r1(kwargs: dict) -> SimpleNamespace:  # two calls in one turn: results come back together
        return message(
            SimpleNamespace(type="tool_use", id="a", name="run_scenario", input={"scenario_code": "W1", "territory": "Ахты"}),
            SimpleNamespace(type="tool_use", id="b", name="compare_territories", input={"territories": ["Махачкала", "Ахты"]}),
            stop="tool_use",
        )

    def r2(kwargs: dict) -> SimpleNamespace:
        seen["w1"], seen["compare"] = last_results(kwargs)
        # W2 with no POI layer loaded; "percent" does not belong to W2 and is dropped.
        return call("run_scenario", {"scenario_code": "W2", "territory": "Ахты", "params": {"kind": "hospital", "percent": 30}}, "c")

    def r3(kwargs: dict) -> SimpleNamespace:
        seen["w2"] = last_results(kwargs)[0]
        return message(text("Готово."))

    claude([r1, r2, r3])
    r = await client.post("/chat", json={"messages": [{"role": "user", "content": "Открой ФАП в Ахтах, сравни с Махачкалой, закрой больницу"}]})
    events = parse_sse(r.text)
    assert events[-1][0] == "done"
    assert [d["ok"] for n, d in events if n == "tool_result"] == [True, False, True]
    assert seen["w1"]["affected"] > 0 and seen["w1"]["run_id"]
    assert seen["compare"]["error"] == "cluster_mismatch"
    assert seen["w2"]["affected"] > 0 and seen["w2"]["summary"]["max_delta"] <= 0
    assert seen["w2"]["ignored_params"] == ["percent"]


async def test_failed_turns_in_history_are_cleaned(client, claude) -> None:  # noqa: ANN001
    """An unanswered question and an empty reply from a failed turn must not cause a 422 or an old answer."""
    fake = claude([lambda kwargs: message(text("Здравствуйте! Спросите об индексе любого населённого пункта СКФО."))])
    history = [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "Что ты умеешь?"},
    ]
    r = await client.post("/chat", json={"messages": history})
    assert r.status_code == 200 and parse_sse(r.text)[-1][0] == "done"
    assert fake.requests[0]["messages"] == [{"role": "user", "content": "Привет\n\nЧто ты умеешь?"}]


async def test_empty_answer_is_an_error_not_a_silent_turn(client, claude) -> None:  # noqa: ANN001
    claude([lambda kwargs: message(stop="end_turn")])
    r = await client.post("/chat", json={"messages": [{"role": "user", "content": "?"}]})
    events = parse_sse(r.text)
    assert [n for n, _ in events] == ["meta", "error"] and events[-1][1]["code"] == "llm_empty_answer"


async def test_no_credentials_degrades_to_error_event(client, container, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    container.chat._client = None  # test settings carry no ANTHROPIC_API_KEY
    r = await client.post("/chat", json={"messages": [{"role": "user", "content": "Привет"}]})
    events = parse_sse(r.text)
    assert events[-1] == ("error", {"code": "chat_unavailable", "message": "Чат не настроен: нет ключа Anthropic API (ANTHROPIC_API_KEY)"})
    bad = await client.post("/chat", json={"messages": [{"role": "assistant", "content": "x"}]})
    assert bad.status_code == 422 and bad.json()["code"] == "chat_last_not_user"
