"""Embedding: turning sections and chunks into vectors, and recording what that cost."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from openai import OpenAI

from synclint.model import AnswerNotRecorded

# The cheapest embedding model OpenAI publishes. Indexing the whole fixture
# corpus costs a fraction of a cent on it, so there is nothing a larger model
# could save, and whether it would link better is a question the corpus can
# answer later rather than one worth paying for up front.
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

# Dollars per million input tokens, read from OpenAI's pricing page on
# 2026-09-23. Embeddings have no output to price.
EMBEDDING_PRICES = {
    "text-embedding-3-small": 0.02,
}


@dataclass(frozen=True)
class Embedded:
    """One text's vector, with the tokens it cost to produce."""

    vector: Sequence[float]
    tokens: int


class Embedder(Protocol):
    """The provider call. This is the injection point: tests supply their own."""

    name: str

    def embed(self, text: str) -> Embedded: ...


class EmbeddingClient:
    """Embeds texts, replays the ones already recorded, and counts what they cost.

    There is no spend ceiling here, unlike `ModelClient`. Embedding costs two
    cents per million tokens and is proportional to the size of the repository
    rather than to the size of the change, so the only way to overspend is to
    index something enormous, and that is visible before it is started.
    """

    def __init__(self, embedder: Embedder, *, price: float, cache: Path | None = None) -> None:
        self._embedder = embedder
        self._price = price
        self._cache = cache
        self._calls = 0
        self._tokens = 0

    @classmethod
    def replaying(cls, model: str, embeddings: Path) -> EmbeddingClient:
        """A client that answers only from `embeddings` and cannot make a call.

        The price is real rather than zero, because what it prices is the cost
        of having recorded the vectors, which is worth stating on a replay too.
        """
        return cls(RecordedOnly(model), price=EMBEDDING_PRICES[model], cache=embeddings)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Embed every text, as one float32 row each, in the order given.

        Raises `AnswerNotRecorded` on a replaying client asked for a text that
        was never recorded.
        """
        seen: dict[str, np.ndarray] = {}
        for text in texts:
            if text not in seen:
                seen[text] = self._one(text)
        return np.stack([seen[text] for text in texts]) if texts else np.empty((0, 0))

    @property
    def calls(self) -> int:
        """Calls this client actually made: zero on a run that only replayed."""
        return self._calls

    @property
    def tokens(self) -> int:
        """Tokens behind every text this client embedded, recorded or asked."""
        return self._tokens

    @property
    def dollars(self) -> float:
        """What `tokens` cost, or would have cost, to embed."""
        return self._tokens * self._price / 1_000_000

    def _one(self, text: str) -> np.ndarray:
        key = _key(self._embedder.name, text)
        path = self._cache / f"{key}.json" if self._cache else None
        if path is not None and path.is_file():
            recorded = json.loads(path.read_text(encoding="utf-8"))
            self._tokens += recorded["tokens"]
            return np.frombuffer(base64.b64decode(recorded["vector"]), dtype="<f4")
        # TODO: one call per text, because the API reports usage per request
        # and the tokens are recorded per text so that a replay can still state
        # what indexing cost. Batching would be far fewer calls on a real
        # repository; #14 is where the call count gets measured.
        embedded = self._embedder.embed(text)
        self._calls += 1
        self._tokens += embedded.tokens
        vector = np.asarray(embedded.vector, dtype="<f4")
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Base64 of little-endian float32 is what the API itself sends, so
            # the stored vector is the received one bit for bit, at a quarter
            # of the size of the same numbers written out as JSON floats.
            path.write_text(
                json.dumps(
                    {
                        "model": self._embedder.name,
                        "text": text,
                        "tokens": embedded.tokens,
                        "vector": base64.b64encode(vector.tobytes()).decode("ascii"),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        return vector


class RecordedOnly:
    """An embedder with nothing to ask: every vector has to be on disk already."""

    def __init__(self, name: str) -> None:
        self.name = name

    def embed(self, text: str) -> Embedded:
        raise AnswerNotRecorded(
            f"no recorded {self.name} embedding for this text, and this client "
            "cannot ask for one"
        )


class OpenAIEmbedder:
    """The provider call, against OpenAI's embeddings endpoint."""

    def __init__(self, name: str = DEFAULT_EMBEDDING_MODEL) -> None:
        self.name = name
        self._client = OpenAI()

    def embed(self, text: str) -> Embedded:
        response = self._client.embeddings.create(model=self.name, input=text)
        return Embedded(
            vector=response.data[0].embedding, tokens=response.usage.prompt_tokens
        )


def _key(model: str, text: str) -> str:
    request = json.dumps({"model": model, "text": text}, sort_keys=True)
    return hashlib.sha256(request.encode("utf-8")).hexdigest()
