from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from matlock.config import SearchEmbeddingConfig


class EmbeddingProviderError(RuntimeError):
    """Raised when an embedding provider cannot be initialized or used."""


class EmbeddingProvider(Protocol):
    model_name: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text."""


@dataclass(slots=True)
class FastEmbedEmbeddingProvider:
    model_name: str
    dimensions: int
    _model: Any

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(vector) for vector in self._model.embed(texts)]


@dataclass(slots=True)
class OpenAICompatibleEmbeddingProvider:
    model_name: str
    dimensions: int
    api_base_url: str
    api_key: str | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.model_name,
            "input": texts,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        response = _post_json(
            f"{self.api_base_url.rstrip('/')}/embeddings",
            payload,
            headers,
        )
        data = response.get("data")
        if not isinstance(data, list):
            raise EmbeddingProviderError(
                "OpenAI-compatible embedding response did not include a 'data' list"
            )

        vectors: list[list[float]] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise EmbeddingProviderError(
                    "OpenAI-compatible embedding response contained an invalid embedding item"
                )
            vectors.append([float(value) for value in item["embedding"]])
        return vectors


def create_embedding_provider(config: SearchEmbeddingConfig) -> EmbeddingProvider:
    """Instantiate the configured embedding provider using lazy imports."""

    if config.provider == "fastembed":
        try:
            fastembed_module = importlib.import_module("fastembed")
        except ModuleNotFoundError as exc:
            raise EmbeddingProviderError(
                "Embedding provider 'fastembed' is not installed. "
                "Install it with 'poetry add fastembed' or switch search.embedding.provider."
            ) from exc

        try:
            model = fastembed_module.TextEmbedding(model_name=config.model_name)
        except Exception as exc:  # pragma: no cover - defensive wrapper around third-party init
            raise EmbeddingProviderError(
                f"Failed to initialize fastembed model '{config.model_name}': {exc}"
            ) from exc
        return FastEmbedEmbeddingProvider(
            model_name=config.model_name,
            dimensions=config.dimensions,
            _model=model,
        )

    if config.provider == "openai-compatible":
        if not config.api_base_url:
            raise EmbeddingProviderError(
                "Embedding provider 'openai-compatible' requires search.embedding.api_base_url"
            )
        return OpenAICompatibleEmbeddingProvider(
            model_name=config.model_name,
            dimensions=config.dimensions,
            api_base_url=config.api_base_url,
            api_key=config.api_key,
        )

    raise EmbeddingProviderError(f"Unsupported embedding provider: {config.provider}")


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req) as response:
            raw = response.read().decode("utf-8")
    except error.URLError as exc:
        raise EmbeddingProviderError(f"Embedding request failed for {url}: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EmbeddingProviderError(
            "Embedding provider returned invalid JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise EmbeddingProviderError("Embedding provider returned a non-object JSON payload")
    return parsed


__all__ = [
    "EmbeddingProvider",
    "EmbeddingProviderError",
    "FastEmbedEmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "create_embedding_provider",
]