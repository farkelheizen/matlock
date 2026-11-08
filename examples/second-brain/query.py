"""Read-only examples against the database produced by the second-brain scan."""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

import sqlite_vec

from matlock2.embedding import FastEmbedder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(__file__).parent / ".matlock2" / "index.sqlite3",
        help="Path to the populated example database",
    )
    args = parser.parse_args()
    # URI read-only mode prevents a typo from creating an empty database.
    uri = args.database.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.enable_load_extension(True)
        try:
            sqlite_vec.load(db)
        finally:
            db.enable_load_extension(False)

        print("Metadata: project_id = atlas")
        for path, metadata in db.execute(
            """
            SELECT path, metadata_json FROM source_documents
            WHERE json_extract(metadata_json, '$.project_id') = ?
            ORDER BY path
            """,
            ("atlas",),
        ):
            print(f"  {path}: {metadata}")

        print("\nFTS: migration")
        for path, content, score in db.execute(
            """
            SELECT c.source_path, c.content, bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks AS c ON c.id = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
            ORDER BY score, c.source_path
            LIMIT ?
            """,
            ("migration", 5),
        ):
            print(f"  {path} (bm25={score:.6f}): {content}")

        # A partial reindex may leave incompatible models in the same database.
        # Matching names and package versions still cannot prove identical model artifacts.
        models = db.execute(
            "SELECT DISTINCT embedding_model, embedding_version FROM source_documents"
        ).fetchall()
        dimensions = db.execute(
            "SELECT value FROM database_metadata WHERE key = 'vector_dimensions'"
        ).fetchone()
        if len(models) != 1 or dimensions is None:
            raise RuntimeError("Vector example requires one indexed model and known dimensions")

        model_name, indexed_version = models[0]
        model = FastEmbedder(model_name, int(dimensions[0]), 512)
        if model.version != indexed_version:
            raise RuntimeError(
                f"Embedding runtime differs from indexed version: "
                f"{model.version} != {indexed_version}; re-scan with the current runtime"
            )
        query = sqlite_vec.serialize_float32(model.embed(["local database decision"])[0])
        print("\nVector: local database decision")
        for path, content, distance in db.execute(
            """
            SELECT c.source_path, c.content,
                   vec_distance_cosine(v.embedding, ?) AS distance
            FROM vec_chunks AS v
            JOIN chunks AS c ON c.id = v.rowid
            ORDER BY distance, c.source_path, c.ordinal
            LIMIT ?
            """,
            (query, 5),
        ):
            print(f"  {path} (cosine distance={distance:.6f}): {content}")


if __name__ == "__main__":
    main()
