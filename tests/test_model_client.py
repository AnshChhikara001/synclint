from pathlib import Path

import pytest

from synclint.model import (
    ModelClient,
    ModelResponse,
    Pricing,
    Spend,
    SpendCeilingExceeded,
)

# A dollar per million tokens in, two per million out, so that the arithmetic
# in these tests can be done in the head and checked against a literal.
PRICING = Pricing(input=1.00, output=2.00)

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"accurate": {"type": "boolean"}},
    "required": ["accurate"],
    "additionalProperties": False,
}


class FakeModel:
    """Stands in for the provider call, so tests replay instead of spending."""

    name = "fake-model"
    max_output_tokens = 500

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.asked: list[tuple[str, str, dict[str, object]]] = []

    def complete(
        self, system: str, user: str, schema: dict[str, object]
    ) -> ModelResponse:
        self.asked.append((system, user, schema))
        return ModelResponse(
            text=self.replies.pop(0), input_tokens=1000, output_tokens=200
        )


def test_answers_with_what_the_model_said() -> None:
    model = FakeModel("still accurate")
    client = ModelClient(model, pricing=PRICING, ceiling=1.00)

    answer = client.complete("be terse", "is this right?", SCHEMA)

    assert answer == "still accurate"
    assert model.asked == [("be terse", "is this right?", SCHEMA)]


def test_reports_the_tokens_and_the_dollars_a_run_consumed() -> None:
    model = FakeModel("one", "two")
    client = ModelClient(model, pricing=PRICING, ceiling=1.00)

    client.complete("be terse", "first", SCHEMA)
    client.complete("be terse", "second", SCHEMA)

    # Per call: 1000 in at $1.00/M is $0.0010, 200 out at $2.00/M is $0.0004.
    assert client.spend.calls == 2
    assert client.spend.input_tokens == 2000
    assert client.spend.output_tokens == 400
    assert client.spend.dollars == pytest.approx(0.0028)


def test_refuses_the_call_that_would_take_a_run_over_its_ceiling() -> None:
    model = FakeModel("one", "two")
    # Room for the first call ($0.0014) and its reservation, but not the second:
    # 500 output tokens reserved at $2.00/M is $0.0010 before either is asked.
    client = ModelClient(model, pricing=PRICING, ceiling=0.0020)

    client.complete("be terse", "first", SCHEMA)

    with pytest.raises(SpendCeilingExceeded):
        client.complete("be terse", "second", SCHEMA)

    assert client.spend.calls == 1
    assert [user for _, user, _ in model.asked] == ["first"]


def test_replays_a_recorded_answer_rather_than_asking_again(tmp_path: Path) -> None:
    recording = ModelClient(
        FakeModel("still accurate"), pricing=PRICING, ceiling=1.00, cache=tmp_path
    )
    recording.complete("be terse", "is this right?", SCHEMA)

    # A second run, against a model with nothing left to say.
    model = FakeModel()
    replaying = ModelClient(model, pricing=PRICING, ceiling=1.00, cache=tmp_path)

    assert replaying.complete("be terse", "is this right?", SCHEMA) == "still accurate"
    assert model.asked == []
    assert replaying.spend == Spend()


def test_does_not_replay_one_model_s_answer_for_another(tmp_path: Path) -> None:
    recording = ModelClient(
        FakeModel("still accurate"), pricing=PRICING, ceiling=1.00, cache=tmp_path
    )
    recording.complete("be terse", "is this right?", SCHEMA)

    other = FakeModel("drifted")
    other.name = "a-different-model"
    client = ModelClient(other, pricing=PRICING, ceiling=1.00, cache=tmp_path)

    assert client.complete("be terse", "is this right?", SCHEMA) == "drifted"


def test_does_not_replay_an_answer_shaped_by_a_different_schema(tmp_path: Path) -> None:
    recording = ModelClient(
        FakeModel('{"accurate": true}'), pricing=PRICING, ceiling=1.00, cache=tmp_path
    )
    recording.complete("be terse", "is this right?", SCHEMA)

    wider: dict[str, object] = {**SCHEMA, "required": ["accurate", "explanation"]}
    client = ModelClient(
        FakeModel('{"accurate": true, "explanation": "..."}'),
        pricing=PRICING,
        ceiling=1.00,
        cache=tmp_path,
    )

    assert client.complete("be terse", "is this right?", wider) != '{"accurate": true}'
