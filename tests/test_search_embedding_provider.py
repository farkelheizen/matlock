from __future__ import annotations

import types

import pytest

from matlock.config import SearchEmbeddingConfig
from matlock.search import embedding
from matlock.search.embedding import EmbeddingProviderError, create_embedding_provider


def test_fastembed_provider_reports_missing_dependency(monkeypatch):
    def _missing_import(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(embedding.importlib, "import_module", _missing_import)

    with pytest.raises(EmbeddingProviderError, match="fastembed"):
        create_embedding_provider(SearchEmbeddingConfig(provider="fastembed"))


def test_fastembed_provider_embeds_text_with_lazy_loaded_module(monkeypatch):
    class FakeTextEmbedding:
        def __init__(self, model_name: str):
            self.model_name = model_name

        def embed(self, texts: list[str]):
            for text in texts:
                yield [float(len(text)), 2.0]

    fake_module = types.SimpleNamespace(TextEmbedding=FakeTextEmbedding)
    monkeypatch.setattr(embedding.importlib, "import_module", lambda name: fake_module)

    provider = create_embedding_provider(
        SearchEmbeddingConfig(provider="fastembed", model_name="mini", dimensions=2)
    )

    assert provider.embed(["a", "longer"]) == [[1.0, 2.0], [6.0, 2.0]]


def test_openai_compatible_provider_posts_to_embeddings_endpoint(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_post_json(url: str, payload: dict[str, object], headers: dict[str, str]):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {
            "data": [
                {"embedding": [0.1, 0.2, 0.3]},
                {"embedding": [0.4, 0.5, 0.6]},
            ]
        }

    monkeypatch.setattr(embedding, "_post_json", _fake_post_json)
    provider = create_embedding_provider(
        SearchEmbeddingConfig(
            provider="openai-compatible",
            model_name="text-embedding-3-small",
            dimensions=3,
            api_base_url="http://localhost:11434/v1",
            api_key="secret",
        )
    )

    vectors = provider.embed(["alpha", "beta"])

    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert captured["url"] == "http://localhost:11434/v1/embeddings"
    assert captured["payload"] == {
        "model": "text-embedding-3-small",
        "input": ["alpha", "beta"],
    }
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer secret",
    }