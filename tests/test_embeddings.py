from pathlib import Path

import numpy as np
import pytest

from synclint.embeddings import Embedded, EmbeddingClient
from synclint.model import AnswerNotRecorded

# Two cents per million tokens, text-embedding-3-small's price, so the
# arithmetic below is the arithmetic a real run does.
PRICE = 0.02


class FakeEmbedder:
    """Stands in for the provider, and counts what it was asked."""

    name = "fake-embedder"

    def __init__(self) -> None:
        self.asked: list[str] = []

    def embed(self, text: str) -> Embedded:
        self.asked.append(text)
        return Embedded(vector=(float(len(text)), 1.0, 0.5), tokens=len(text.split()))


def test_embeds_each_text_as_one_row_in_the_order_given() -> None:
    embedder = FakeEmbedder()
    client = EmbeddingClient(embedder, price=PRICE)

    matrix = client.embed(["one", "three"])

    assert matrix.shape == (2, 3)
    assert matrix[:, 0].tolist() == [3.0, 5.0]
    assert embedder.asked == ["one", "three"]


def test_counts_the_tokens_embedded_and_what_they_cost() -> None:
    client = EmbeddingClient(FakeEmbedder(), price=PRICE)

    client.embed(["three words here", "two more"])

    assert client.calls == 2
    assert client.tokens == 5
    assert client.dollars == pytest.approx(5 * PRICE / 1_000_000)


def test_a_recorded_embedding_is_replayed_rather_than_asked_again(
    tmp_path: Path,
) -> None:
    first = FakeEmbedder()
    EmbeddingClient(first, price=PRICE, cache=tmp_path).embed(["find(query)"])
    second = FakeEmbedder()
    client = EmbeddingClient(second, price=PRICE, cache=tmp_path)

    matrix = client.embed(["find(query)"])

    assert second.asked == []
    assert matrix.tolist() == [[11.0, 1.0, 0.5]]
    # Replayed, so nothing was paid this run — but the tokens it cost to record
    # are carried with the vector, so the cost of indexing can still be stated.
    assert client.calls == 0
    assert client.tokens == 1


def test_a_text_embedded_twice_in_one_run_is_asked_once(tmp_path: Path) -> None:
    embedder = FakeEmbedder()
    client = EmbeddingClient(embedder, price=PRICE, cache=tmp_path)

    client.embed(["same", "same"])

    assert embedder.asked == ["same"]


def test_a_replaying_client_names_the_gap_rather_than_asking(tmp_path: Path) -> None:
    client = EmbeddingClient.replaying("text-embedding-3-small", tmp_path)

    with pytest.raises(AnswerNotRecorded):
        client.embed(["never recorded"])


def test_embeddings_recorded_under_another_model_are_not_replayed(
    tmp_path: Path,
) -> None:
    EmbeddingClient(FakeEmbedder(), price=PRICE, cache=tmp_path).embed(["find"])

    with pytest.raises(AnswerNotRecorded):
        EmbeddingClient.replaying("text-embedding-3-small", tmp_path).embed(["find"])


def test_a_recorded_vector_replays_bit_for_bit(tmp_path: Path) -> None:
    class Precise(FakeEmbedder):
        def embed(self, text: str) -> Embedded:
            return Embedded(vector=(0.1, 1 / 3, -2e-7), tokens=1)

    recorded = EmbeddingClient(Precise(), price=PRICE, cache=tmp_path).embed(["x"])
    replayed = EmbeddingClient(FakeEmbedder(), price=PRICE, cache=tmp_path).embed(["x"])

    # FakeEmbedder shares Precise's name, so the second client finds the first
    # one's recording. The provider returns float32, and so does the cache.
    assert np.array_equal(recorded, replayed)
    assert replayed.dtype == np.float32
