from __future__ import annotations

from math import sqrt


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Return cosine similarity for two same-length vectors."""

    if len(left) != len(right):
        raise ValueError("vectors must have the same length")

    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0

    dot_product = sum(a * b for a, b in zip(left, right, strict=True))
    return dot_product / (left_norm * right_norm)


def reciprocal_rank(rank: int, *, k: int = 60) -> float:
    """Return the reciprocal-rank score for a 1-based rank."""

    if rank < 1:
        raise ValueError("rank must be >= 1")
    return 1.0 / (k + rank)


def hybrid_rrf_score(
    *,
    fts_rank: int | None,
    vector_rank: int | None,
    alpha: float,
    k: int = 60,
) -> float:
    """Blend FTS and vector reciprocal ranks using the request alpha."""

    if fts_rank is None and vector_rank is None:
        return 0.0
    if fts_rank is None:
        return reciprocal_rank(vector_rank, k=k)
    if vector_rank is None:
        return reciprocal_rank(fts_rank, k=k)
    return 2.0 * (
        ((1.0 - alpha) * reciprocal_rank(fts_rank, k=k))
        + (alpha * reciprocal_rank(vector_rank, k=k))
    )


__all__ = ["cosine_similarity", "hybrid_rrf_score", "reciprocal_rank"]