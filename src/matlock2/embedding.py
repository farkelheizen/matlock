from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from tokenizers import Tokenizer as HuggingFaceTokenizer


class FastEmbedder:
    """Local FastEmbed model with the matching Hugging Face tokenizer."""

    def __init__(self, model_name: str, dimensions: int, max_input_tokens: int) -> None:
        try:
            from fastembed import TextEmbedding

            self._embedding = TextEmbedding(model_name=model_name)
            self._tokenizer = HuggingFaceTokenizer.from_pretrained(model_name)
        except Exception as exc:
            raise RuntimeError(
                f"Could not initialize local embedding model {model_name!r}. "
                "Check the FastEmbed model name and network access for its initial download."
            ) from exc
        self.model_name = model_name
        self.dimensions = dimensions
        self.max_input_tokens = max_input_tokens
        self.runtime_version = _package_version("fastembed")
        self.tokenizer_version = _package_version("tokenizers")
        self.version = (
            f"fastembed-{self.runtime_version};tokenizers-{self.tokenizer_version}"
        )

    def count(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=True).ids)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for values in self._embedding.embed(texts):
            vector = values.tolist() if hasattr(values, "tolist") else list(values)
            float_vector = [float(value) for value in vector]
            if len(float_vector) != self.dimensions:
                raise ValueError(
                    f"Model {self.model_name!r} returned {len(float_vector)} dimensions; "
                    f"configuration requires {self.dimensions}"
                )
            vectors.append(float_vector)
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding model returned an unexpected number of vectors")
        return vectors


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "unknown"
