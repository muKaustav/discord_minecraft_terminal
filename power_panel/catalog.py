"""Persistent catalog for installed Minecraft server packs."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


class PackCatalog:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._init_schema()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS packs (
                    id INTEGER PRIMARY KEY,
                    project_id INTEGER NOT NULL,
                    project_name TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    client_file_id INTEGER NOT NULL,
                    client_file_name TEXT,
                    client_url TEXT NOT NULL,
                    server_file_id INTEGER NOT NULL UNIQUE,
                    server_file_name TEXT,
                    minecraft_version TEXT,
                    loader TEXT,
                    state TEXT NOT NULL,
                    install_path TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operations (
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    message TEXT NOT NULL,
                    pack_id INTEGER REFERENCES packs(id),
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict | None:
        return dict(row) if row is not None else None

    def upsert_pack(self, pack: dict, state: str, install_path: str | None = None, error: str | None = None) -> dict:
        fields = (
            "project_id", "project_name", "slug", "client_file_id", "client_file_name",
            "client_url", "server_file_id", "server_file_name", "minecraft_version", "loader",
        )
        values = [pack.get(field) for field in fields]
        with self._connect() as connection:
            connection.execute(
                f"""
                INSERT INTO packs ({', '.join(fields)}, state, install_path, error)
                VALUES ({', '.join('?' for _ in fields)}, ?, ?, ?)
                ON CONFLICT(server_file_id) DO UPDATE SET
                    project_name=excluded.project_name, client_file_id=excluded.client_file_id,
                    client_file_name=excluded.client_file_name, client_url=excluded.client_url,
                    server_file_name=excluded.server_file_name, minecraft_version=excluded.minecraft_version,
                    loader=excluded.loader, state=excluded.state, install_path=excluded.install_path,
                    error=excluded.error, updated_at=CURRENT_TIMESTAMP
                """,
                (*values, state, install_path, error),
            )
            row = connection.execute("SELECT * FROM packs WHERE server_file_id = ?", (pack["server_file_id"],)).fetchone()
        return self._dict(row)  # type: ignore[return-value]

    def list_packs(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM packs ORDER BY project_name, client_file_id").fetchall()
        return [self._dict(row) for row in rows]  # type: ignore[list-item]

    def get_pack(self, pack_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM packs WHERE id = ?", (pack_id,)).fetchone()
        return self._dict(row)

    def select_pack(self, pack_id: int) -> None:
        pack = self.get_pack(pack_id)
        if not pack or pack["state"] != "ready":
            raise ValueError("Only installed modpacks can be selected.")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO settings(key, value) VALUES('active_pack_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(pack_id),),
            )

    def active_pack(self) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT packs.* FROM packs JOIN settings ON settings.value = CAST(packs.id AS TEXT) "
                "WHERE settings.key = 'active_pack_id'"
            ).fetchone()
        return self._dict(row)

    def start_operation(self, kind: str, pack_id: int | None, message: str) -> dict:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT id FROM operations WHERE state = 'working' LIMIT 1"
            ).fetchone()
            if active:
                raise ValueError("Another modpack operation is already running.")
            cursor = connection.execute(
                "INSERT INTO operations(kind, state, message, pack_id) VALUES(?, 'working', ?, ?)",
                (kind, message, pack_id),
            )
            row = connection.execute("SELECT * FROM operations WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._dict(row)  # type: ignore[return-value]

    def update_operation(self, operation_id: int, state: str, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE operations SET state = ?, message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (state, message, operation_id),
            )

    def current_operation(self) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM operations ORDER BY id DESC LIMIT 1").fetchone()
        return self._dict(row)
