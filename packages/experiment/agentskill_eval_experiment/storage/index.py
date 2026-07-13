"""Disposable SQLite index derived entirely from manifest truth."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class ManifestIndexRecord:
    relative_path: str
    kind: str
    entity_id: str
    experiment_id: str
    payload_sha256: str
    semantic_sha256: Optional[str]
    status: Optional[str]
    evaluation_outcome: Optional[str]


class LocalSqliteIndex:
    """A query cache that can always be deleted and rebuilt."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS manifest_index (
                    relative_path TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    semantic_sha256 TEXT,
                    status TEXT,
                    evaluation_outcome TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_manifest_kind ON manifest_index(kind)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_manifest_status ON manifest_index(status)"
            )

    def upsert(self, record: ManifestIndexRecord) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO manifest_index (
                    relative_path, kind, entity_id, experiment_id,
                    payload_sha256, semantic_sha256, status, evaluation_outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    kind = excluded.kind,
                    entity_id = excluded.entity_id,
                    experiment_id = excluded.experiment_id,
                    payload_sha256 = excluded.payload_sha256,
                    semantic_sha256 = excluded.semantic_sha256,
                    status = excluded.status,
                    evaluation_outcome = excluded.evaluation_outcome,
                    updated_at = CURRENT_TIMESTAMP
                """,
                self._values(record),
            )

    def replace_all(self, records: Iterable[ManifestIndexRecord]) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute("DELETE FROM manifest_index")
            connection.executemany(
                """
                INSERT INTO manifest_index (
                    relative_path, kind, entity_id, experiment_id,
                    payload_sha256, semantic_sha256, status, evaluation_outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._values(record) for record in records],
            )

    def records(self) -> List[ManifestIndexRecord]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT relative_path, kind, entity_id, experiment_id,
                       payload_sha256, semantic_sha256, status, evaluation_outcome
                FROM manifest_index
                ORDER BY relative_path
                """
            ).fetchall()
        return [ManifestIndexRecord(*row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    @staticmethod
    def _values(record: ManifestIndexRecord) -> tuple[Optional[str], ...]:
        return (
            record.relative_path,
            record.kind,
            record.entity_id,
            record.experiment_id,
            record.payload_sha256,
            record.semantic_sha256,
            record.status,
            record.evaluation_outcome,
        )
