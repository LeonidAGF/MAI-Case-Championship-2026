"""Chat orchestration with a scripted fake GigaChat client (no network, no cost) and the real tools."""

import json

import pytest

pytestmark = pytest.mark.integration


class FakeGigaChat:
    """Each script step gets the request payload and returns a chat completion."""

    def __init__(self, script):  # noqa: ANN001
        self.script, self.payloads = script, []

    async def chat(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return self.script.pop(0)(payload)

    async def close(self) -> None:
        pass


def completion(content: str = "", finish: str = "stop", **message) -> dict:  # noqa: ANN003
    return {"choices": [{"message": {"role": "assistant", "content": content, **message}, "index": 0, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}


def call(name: str, arguments: dict, state: str | None = None) -> dict:
    extra = {"functions_state_id": state} if state else {}
    return completion("", "function_call", function_call={"name": name, "arguments": arguments}, **extra)


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    for chunk in raw.strip().split("\n\n"):
        name, _, data = chunk.partition("\ndata: ")
        events.append((name.removeprefix("event: "), json.loads(data)))
    return events


def last_result(payload: dict) -> dict:
    message = payload["messages"][-1]
    assert message["role"] == "function"
    return json.loads(message["content"])


@pytest.fixture
def gigachat(container):  # noqa: ANN001, ANN201
    def use(script):  # noqa: ANN001, ANN202
        container.chat._gigachat = FakeGigaChat(script)
        return container.chat._gigachat

    yield use
    container.chat._gigachat = None


async def test_tool_loop_grounded_answer(client, gigachat) -> None:  # noqa: ANN001
    def round1(payload: dict) -> dict:
        return call("get_index", {"territory": "Махачкала"}, state="s-1")

    def round2(payload: dict) -> dict:
        assert payload["messages"][-2]["functions_state_id"] == "s-1"
        res = last_result(payload)
        return completion(f"Индекс Махачкалы — {str(res['total']).replace('.', ',')} из 100, "
                          f"место {res['rank']} из {res['cluster_size']}.")

    fake = gigachat([round1, round2])
    r = await client.post("/chat", json={"messages": [{"role": "user", "content": "Какой индекс у Махачкалы?"}], "profile": "family"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    names = [e[0] for e in events]
    assert events[0] == ("meta", {"model": "GigaChat-3-Pro", "profile": "family"}) and names[-1] == "done"
    assert ("tool_call", "get_index") in [(n, d.get("name")) for n, d in events]
    done = events[-1][1]
    assert done["grounding"]["ok"] is True and done["stop_reason"] == "stop"
    assert done["usage"] == {"input_tokens": 200, "output_tokens": 40}
    cites = next(d for n, d in events if n == "citations")["sources"]
    assert any("Атлас" in s["title"] for s in cites)
    answer = next(d["text"] for n, d in events if n == "delta")
    assert answer.startswith("Внимание: в Атласе сейчас синтетические демо-данные") and "Индекс Махачкалы" in answer

    first = fake.payloads[0]  # request shape of the GigaChat API
    assert first["model"] == "GigaChat-3-Pro" and first["function_call"] == "auto"
    assert [f["name"] for f in first["functions"]] == ["get_index", "compare_territories", "run_scenario"]
    assert all(f["parameters"]["type"] == "object" and f["few_shot_examples"] for f in first["functions"])
    system = first["messages"][0]
    assert system["role"] == "system" and "синтетические" in system["content"]  # warns about demo data
    assert first["messages"][1] == {"role": "user", "content": "Какой индекс у Махачкалы?"}


async def test_ungrounded_number_is_flagged(client, gigachat) -> None:  # noqa: ANN001
    gigachat([lambda p: completion("В Ведено до больницы 137 минут.")])
    r = await client.post("/chat", json={"messages": [{"role": "user", "content": "Сколько до больницы в Ведено?"}]})
    done = parse_sse(r.text)[-1][1]
    assert done["grounding"]["ok"] is False and "137" in done["grounding"]["ungrounded"]
    assert "137" not in "".join(d["text"] for n, d in parse_sse(r.text) if n == "delta")


async def test_scenario_tools_and_cross_cluster(client, gigachat) -> None:  # noqa: ANN001
    seen = {}

    def r1(payload: dict) -> dict:
        return call("run_scenario", {"scenario_code": "W1", "territory": "Ахты"})

    def r2(payload: dict) -> dict:
        seen["w1"] = last_result(payload)
        return call("compare_territories", {"territories": ["Махачкала", "Ахты"]})

    def r3(payload: dict) -> dict:
        seen["compare"] = last_result(payload)
        # W2 with no POI layer loaded; "percent" does not belong to W2 and is dropped.
        return call("run_scenario", {"scenario_code": "W2", "territory": "Ахты", "params": {"kind": "hospital", "percent": 30}})

    def r4(payload: dict) -> dict:
        seen["w2"] = last_result(payload)
        return completion("Готово.")

    gigachat([r1, r2, r3, r4])
    r = await client.post("/chat", json={"messages": [{"role": "user", "content": "Открой ФАП в Ахтах, сравни с Махачкалой, закрой больницу"}]})
    events = parse_sse(r.text)
    assert events[-1][0] == "done"
    assert [d["ok"] for n, d in events if n == "tool_result"] == [True, False, True]
    assert seen["w1"]["affected"] > 0 and seen["w1"]["run_id"]
    assert seen["compare"]["error"] == "cluster_mismatch"
    assert seen["w2"]["affected"] > 0 and seen["w2"]["summary"]["max_delta"] <= 0
    assert seen["w2"]["ignored_params"] == ["percent"]


async def test_no_credentials_degrades_to_error_event(client, container) -> None:  # noqa: ANN001
    container.chat._gigachat = None  # test settings carry no GIGACHAT_CREDENTIALS
    r = await client.post("/chat", json={"messages": [{"role": "user", "content": "Привет"}]})
    events = parse_sse(r.text)
    assert events[-1] == ("error", {"code": "chat_unavailable", "message": "Чат не настроен: нет ключа GigaChat (GIGACHAT_CREDENTIALS)"})
    bad = await client.post("/chat", json={"messages": [{"role": "assistant", "content": "x"}]})
    assert bad.status_code == 422 and bad.json()["code"] == "chat_last_not_user"
