"""The model client: what a run asks the model, what it costs, and what it caches."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from openai import OpenAI
from openai.types.shared.reasoning_effort import ReasoningEffort


@dataclass(frozen=True)
class Pricing:
    """Dollars per million tokens, as the provider publishes them."""

    input: float
    output: float


@dataclass(frozen=True)
class ModelResponse:
    """One answer, with what it cost to produce."""

    text: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Spend:
    """What a run consumed."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    dollars: float = 0.0


# OpenAI's published rule of thumb for English prose. Code tokenises denser
# than that, so treat it as a floor on the token count, never a measurement.
_CHARACTERS_PER_TOKEN = 4


class SpendCeilingExceeded(RuntimeError):
    """Raised instead of making the call that would take a run over its ceiling."""


class Model(Protocol):
    """The provider call. This is the injection point: tests supply their own."""

    name: str
    max_output_tokens: int

    def complete(
        self, system: str, user: str, schema: dict[str, object]
    ) -> ModelResponse: ...


class ModelClient:
    """Asks the model, counts what it costs, and stops before the ceiling."""

    def __init__(
        self,
        model: Model,
        *,
        pricing: Pricing,
        ceiling: float,
        cache: Path | None = None,
    ) -> None:
        self._model = model
        self._pricing = pricing
        self._ceiling = ceiling
        self._cache = cache
        self._spend = Spend()

    def complete(self, system: str, user: str, schema: dict[str, object]) -> str:
        """Ask the model one question and return its answer as JSON matching `schema`.

        Raises `SpendCeilingExceeded` rather than make a call that could take
        the run past its ceiling.
        """
        key = _key(self._model.name, system, user, schema)
        recorded = self._recorded(key)
        if recorded is not None:
            return recorded
        self._reserve(system + user)
        response = self._model.complete(system, user, schema)
        self._spend = _add(self._spend, response, self._pricing)
        self._record(key, system, user, response)
        return response.text

    @property
    def spend(self) -> Spend:
        return self._spend

    def _recorded(self, key: str) -> str | None:
        if self._cache is None:
            return None
        path = self._cache / f"{key}.json"
        if not path.is_file():
            return None
        text: str = json.loads(path.read_text(encoding="utf-8"))["text"]
        return text

    def _record(
        self, key: str, system: str, user: str, response: ModelResponse
    ) -> None:
        if self._cache is None:
            return
        self._cache.mkdir(parents=True, exist_ok=True)
        path = self._cache / f"{key}.json"
        # The prompt is written beside the answer although nothing reads it
        # back: a cache you cannot grep is one you have to trust blindly.
        path.write_text(
            json.dumps(
                {
                    "model": self._model.name,
                    "system": system,
                    "user": user,
                    "text": response.text,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _reserve(self, prompt: str) -> None:
        # The ceiling has to hold before the call, and neither side of the cost
        # is known until after it. Output is bounded exactly and priced highest,
        # so it is reserved in full; input is estimated from characters, which
        # under-counts code. The over-reservation on output is what pays for
        # that, and the ceiling is approximate in synclint's favour either way.
        worst_case = (
            len(prompt) / _CHARACTERS_PER_TOKEN * self._pricing.input
            + self._model.max_output_tokens * self._pricing.output
        ) / 1_000_000
        if self._spend.dollars + worst_case > self._ceiling:
            raise SpendCeilingExceeded(
                f"{self._spend.dollars:.4f} spent of a {self._ceiling:.4f} ceiling; "
                f"the next call could cost {worst_case:.4f}"
            )


def _add(spend: Spend, response: ModelResponse, pricing: Pricing) -> Spend:
    dollars = (
        response.input_tokens * pricing.input + response.output_tokens * pricing.output
    ) / 1_000_000
    return Spend(
        calls=spend.calls + 1,
        input_tokens=spend.input_tokens + response.input_tokens,
        output_tokens=spend.output_tokens + response.output_tokens,
        dollars=spend.dollars + dollars,
    )


def _key(model: str, system: str, user: str, schema: dict[str, object]) -> str:
    """The cache key: a hash of the prompt and everything else that shapes the answer.

    The model name and the schema are part of it. Two models answering the same
    question give different answers, as do two schemas, and replaying one for
    the other would be a lie about what was measured.
    """
    request = json.dumps(
        {"model": model, "system": system, "user": user, "schema": schema},
        sort_keys=True,
    )
    return hashlib.sha256(request.encode("utf-8")).hexdigest()


# Dollars per million tokens, read from OpenAI's pricing page on 2026-09-21.
# Only the models synclint has actually been run against are listed; a model
# with no published price here has to be given one rather than be guessed at,
# because the ceiling is only as honest as the numbers behind it.
PRICES = {
    "gpt-5.5": Pricing(input=5.00, output=30.00),
    "gpt-5.4": Pricing(input=2.50, output=15.00),
    "gpt-5.4-mini": Pricing(input=0.75, output=4.50),
    "gpt-5.4-nano": Pricing(input=0.20, output=1.25),
}

# The whole project has $2 to spend. #6 measures whether the larger model earns
# its price; until then the cheap one is the honest default.
DEFAULT_MODEL = "gpt-5.4-mini"


class OpenAIModel:
    """The provider call, against OpenAI's Responses API."""

    # Reasoning tokens are billed as output and count against this, so it has
    # to leave room for thinking as well as for the answer, which is one
    # sentence. Verification is a short judgement over a little text rather
    # than a hard reasoning problem, hence the low effort; #6 measures whether
    # either is set too mean.
    max_output_tokens = 2048
    _EFFORT: ReasoningEffort = "low"

    def __init__(self, name: str = DEFAULT_MODEL) -> None:
        self.name = name
        self._client = OpenAI()

    def complete(
        self, system: str, user: str, schema: dict[str, object]
    ) -> ModelResponse:
        response = self._client.responses.create(
            model=self.name,
            instructions=system,
            input=user,
            max_output_tokens=self.max_output_tokens,
            reasoning={"effort": self._EFFORT},
            text={
                "format": {
                    "type": "json_schema",
                    "name": "synclint",
                    "schema": schema,
                    "strict": True,
                }
            },
        )
        if response.usage is None:
            # The ledger is the only thing standing between this and the
            # budget, so an unmetered answer is worse than no answer.
            raise RuntimeError(f"{self.name} answered without reporting its usage")
        return ModelResponse(
            text=response.output_text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
