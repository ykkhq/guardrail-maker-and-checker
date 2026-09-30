"""SQLite storage for pipelines and deployed versions (stdlib sqlite3; one file)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from guardrail_common.graph import PipelineGraph

SCHEMA = """
CREATE TABLE IF NOT EXISTS pipelines (
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  graph TEXT NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
  slug TEXT NOT NULL,
  version INTEGER NOT NULL,
  graph TEXT NOT NULL,
  compiled TEXT NOT NULL,
  deployed_at REAL NOT NULL,
  PRIMARY KEY (slug, version)
);
"""


class Store:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def list(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT p.slug, p.name, p.updated_at, MAX(v.version) AS version, MAX(v.deployed_at) AS deployed_at "
            "FROM pipelines p LEFT JOIN versions v ON v.slug = p.slug GROUP BY p.slug ORDER BY p.name"
        ).fetchall()
        return [dict(r) for r in rows]

    def get(self, slug: str) -> PipelineGraph | None:
        row = self._db.execute("SELECT graph FROM pipelines WHERE slug = ?", (slug,)).fetchone()
        return PipelineGraph.model_validate_json(row["graph"]) if row else None

    def save(self, graph: PipelineGraph) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO pipelines (slug, name, graph, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(slug) DO UPDATE SET name = excluded.name, graph = excluded.graph, "
                "updated_at = excluded.updated_at",
                (graph.slug, graph.name or graph.slug, graph.model_dump_json(by_alias=True), time.time()),
            )

    def delete(self, slug: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM pipelines WHERE slug = ?", (slug,))
            self._db.execute("DELETE FROM versions WHERE slug = ?", (slug,))

    def add_version(self, graph: PipelineGraph, compiled: dict[str, Any]) -> int:
        with self._lock, self._db:
            (latest,) = self._db.execute(
                "SELECT COALESCE(MAX(version), 0) FROM versions WHERE slug = ?", (graph.slug,)
            ).fetchone()
            self._db.execute(
                "INSERT INTO versions (slug, version, graph, compiled, deployed_at) VALUES (?, ?, ?, ?, ?)",
                (graph.slug, latest + 1, graph.model_dump_json(by_alias=True), json.dumps(compiled), time.time()),
            )
        return latest + 1

    def versions(self, slug: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT version, deployed_at FROM versions WHERE slug = ? ORDER BY version DESC", (slug,)
        ).fetchall()
        return [dict(r) for r in rows]

    def version_graph(self, slug: str, version: int) -> PipelineGraph | None:
        row = self._db.execute(
            "SELECT graph FROM versions WHERE slug = ? AND version = ?", (slug, version)
        ).fetchone()
        return PipelineGraph.model_validate_json(row["graph"]) if row else None
