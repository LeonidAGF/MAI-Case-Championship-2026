"""Chat orchestration with a scripted fake Anthropic client (no network, no cost)."""

import json
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration


class FakeStream:
    def __init__(self, message, texts):  # noqa: ANN001
        self.message, self.texts = message, texts

    async def __aenter__(self):  # noqa: ANN204
        return self

    async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
        return False

    def __aiter__(self):  # noqa: ANN204
        async def gen():  # noqa: ANN202
            for t in self.texts:
                yield SimpleNamespace(type="text", text=t)

        return gen()

    async def get_final_message(self):  # noqa: ANN201
        return self.message


class FakeMessages:
    def __init__(self, script):  # noqa: ANN001
        self.script = script
        self.calls = []

    def stream(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        texts, blocks, stop = self.script.pop(0)(kwargs)
        usage = SimpleNamespace(input_tokens=100, output_tokens=20)
        return FakeStream(SimpleNamespace(content=blocks, stop_reason=stop, usage=usage), texts)


class FakeClient:
    def __init__(self, script):  # noqa: ANN001
        self.messages = FakeMessages(script)
        self.beta = SimpleNamespace(messages=self.messages)

    async def close(self) -> None:
        pass


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    for chunk in raw.strip().split("\n\n"):
        name, _, data = chunk.partition("\ndata: ")
        events.append((name.removeprefix("event: "), json.loads(data)))
    return events


def last_tool_result(kwargs) -> dict:  # noqa: ANN001
    return json.loads(kwargs["messages"][-1]["content"][0]["content"])


async def test_tool_loop_grounded_answer(client, container) -> None:  # noqa: ANN001
    def round1(kwargs):  # noqa: ANN001, ANN202
        tool = SimpleNamespace(type="tool_use", id="tu_1", name="get_index", input={"territory": "Махачкала"})
        return ["Смотрю данные. "], [SimpleNamespace(type="text", text="Смотрю данные. "), tool], "tool_use"

    def round2(kwargs):  # noqa: ANN001, ANN202
        res = last_tool_result(kwargs)
        answer = f"Индекс Махачкалы — {str(res['total']).replace('.', ',')} из 100, место {res['rank']} из {res['cluster_size']}."
        return [answer], [SimpleNamespace(type="text", text=answer)], "end_turn"

    fake = FakeClient([round1, round2])
    container.chat._client = fake
    r = await client.post("/chat", json={"messages": [{"role": "user", "content": "Какой индекс у Махачкалы?"}], "profile": "family"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    names = [e[0] for e in events]
    assert names[0] == "meta" and names[-1] == "done"
    assert ("tool_call", "get_index") in [(n, d.get("name")) for n, d in events]
    done = events[-1][1]
    assert done["grounding"]["ok"] is True and done["stop_reason"] == "end_turn"
    cites = next(d for n, d in events if n == "citations")["sources"]
    assert any("Атлас" in s["title"] for s in cites)

    first = fake.messages.calls[0]  # request shape per the Claude API guidance
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert first["betas"] == ["server-side-fallback-2026-07-01"]
    assert first["output_config"] == {"effort": "medium"}
    assert [t["name"] for t in first["tools"]] == ["get_index", "compare_territories", "run_scenario"]
    assert all(t["eager_input_streaming"] for t in first["tools"])
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "синтетические" in first["system"][1]["text"]  # warns about demo data


async def test_ungrounded_number_is_flagged(client, container) -> None:  # noqa: ANN001
    def only(kwargs):  # noqa: ANN001, ANN202
        text = "В Ведено до больницы 137 минут."
        return [text], [SimpleNamespace(type="text", text=text)], "end_turn"

    container.chat._client = FakeClient([only])
    r = await client.post("/chat", json={"messages": [{"role": "user", "content": "Сколько до больницы в Ведено?"}]})
    done = parse_sse(r.text)[-1][1]
    assert done["grounding"]["ok"] is False and "137" in done["grounding"]["ungrounded"]


async def test_scenario_tool_and_cross_cluster(client, container) -> None:  # noqa: ANN001
    calls = []

    def r1(kwargs):  # noqa: ANN001, ANN202
        return [], [
            SimpleNamespace(type="tool_use", id="a", name="run_scenario", input={"scenario_code": "W1", "territory": "Ахты"}),
            SimpleNamespace(type="tool_use", id="b", name="compare_territories", input={"territories": ["Махачкала", "Ахты"]}),
        ], "tool_use"

    def r2(kwargs):  # noqa: ANN001, ANN202
        results = {b["tool_use_id"]: json.loads(b["content"]) for b in kwargs["messages"][-1]["content"]}
        calls.append(results)
        return ["Готово."], [SimpleNamespace(type="text", text="Готово.")], "end_turn"

    container.chat._client = FakeClient([r1, r2])
    r = await client.post("/chat", json={"messages": [{"role": "user", "content": "Открой ФАП в Ахтах и сравни с Махачкалой"}]})
    events = parse_sse(r.text)
    assert events[-1][0] == "done"
    results = calls[0]
    assert results["a"]["affected"] > 0 and results["a"]["run_id"]
    assert results["b"]["error"] == "cluster_mismatch"


async def test_no_credentials_degrades_to_error_event(client, container, monkeypatch) -> None:  # noqa: ANN001
    def boom():  # noqa: ANN202
        raise TypeError("Could not resolve authentication method")

    container.chat._client = None
    monkeypatch.setattr(container.chat, "client", boom)
    r = await client.post("/chat", json={"messages": [{"role": "user", "content": "Привет"}]})
    events = parse_sse(r.text)
    assert events[-1] == ("error", {"code": "chat_unavailable", "message": "Чат не настроен: нет доступа к LLM API"})
    bad = await client.post("/chat", json={"messages": [{"role": "assistant", "content": "x"}]})
    assert bad.status_code == 422 and bad.json()["code"] == "chat_last_not_user"
