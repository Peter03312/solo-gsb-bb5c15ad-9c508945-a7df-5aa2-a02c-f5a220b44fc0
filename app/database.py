"""SQLite 持久化层。

表：
- config        单行，保存当前输入及其版本号（input_version 单调递增）；
- solution      至多一行，保存方案 JSON、所依据的输入版本、是否有效。
                 任何输入变更都会把旧方案标记 valid=0（失效）；
- meta          键值（schema_version 等）。

写操作全部在单个事务内完成；非法输入由 validation 层在调用前拦截，
数据库层只接收已规范化的数据。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading

SCHEMA_VERSION = 1


def default_inputs() -> dict:
    return {
        "sheet_w": 450,
        "sheet_h": 320,
        "kerf": 3,
        "max_sheets": 6,
        "state_limit": 200_000,
        "products": [
            {"name": "卡片A", "length": 120, "width": 80,
             "qty": 8, "rotatable": True},
            {"name": "卡片B", "length": 200, "width": 60,
             "qty": 4, "rotatable": False},
        ],
    }


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS config (
                    id            INTEGER PRIMARY KEY CHECK (id = 1),
                    input_version INTEGER NOT NULL,
                    payload       TEXT NOT NULL,
                    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS solution (
                    id                INTEGER PRIMARY KEY CHECK (id = 1),
                    valid             INTEGER NOT NULL,
                    input_version     INTEGER NOT NULL,
                    status            TEXT NOT NULL,
                    payload           TEXT,
                    note              TEXT,
                    states_used       INTEGER,
                    sheets_used       INTEGER,
                    saved_at          TEXT NOT NULL DEFAULT (datetime('now'))
                );
                """
            )
            row = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES('schema_version',?)",
                    (str(SCHEMA_VERSION),))
            exists = conn.execute("SELECT id FROM config WHERE id=1").fetchone()
            if exists is None:
                # 首次启动写入默认输入（合法输入，版本 1），不含方案
                conn.execute(
                    "INSERT INTO config(id,input_version,payload) "
                    "VALUES(1,1,?)",
                    (json.dumps(default_inputs(), ensure_ascii=False),))
                conn.execute(
                    "INSERT INTO solution(id,valid,input_version,status) "
                    "VALUES(1,0,1,'unsaved')")

    # ---------- 输入 ----------
    def get_inputs(self) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT input_version, payload, updated_at FROM config "
                "WHERE id=1").fetchone()
        data = json.loads(row["payload"])
        data["input_version"] = row["input_version"]
        data["updated_at"] = row["updated_at"]
        return data

    def replace_inputs(self, inputs: dict) -> dict:
        """校验通过后的输入整体替换；版本号 +1，并使任何旧方案失效。"""
        clean = {k: inputs[k] for k in
                 ("sheet_w", "sheet_h", "kerf", "max_sheets",
                  "state_limit", "products")}
        payload = json.dumps(clean, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT input_version FROM config WHERE id=1").fetchone()
            new_version = row["input_version"] + 1
            conn.execute(
                "UPDATE config SET input_version=?, payload=?, "
                "updated_at=datetime('now') WHERE id=1",
                (new_version, payload))
            # 输入一变，旧方案立即失效（无论原状态如何）
            conn.execute(
                "UPDATE solution SET valid=0, saved_at=datetime('now') "
                "WHERE id=1")
        data = dict(clean)
        data["input_version"] = new_version
        return data

    # ---------- 方案 ----------
    def get_solution(self) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM solution WHERE id=1").fetchone()
        out = {
            "valid": bool(row["valid"]),
            "input_version": row["input_version"],
            "status": row["status"],
            "note": row["note"],
            "states_used": row["states_used"],
            "sheets_used": row["sheets_used"],
            "saved_at": row["saved_at"],
        }
        if row["payload"]:
            out["payload"] = json.loads(row["payload"])
        return out

    def save_solution(self, input_version: int, result: dict) -> None:
        """保存已通过 verify 的可行方案，绑定输入版本，标记有效。"""
        payload = json.dumps(result, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            cfg = conn.execute(
                "SELECT input_version FROM config WHERE id=1").fetchone()
            current = cfg["input_version"]
            if current != input_version:
                raise StaleVersionError(
                    f"方案基于版本 {input_version}，当前输入版本为 {current}")
            conn.execute(
                "UPDATE solution SET valid=1, input_version=?, status='found', "
                "payload=?, note=?, states_used=?, sheets_used=?, "
                "saved_at=datetime('now') WHERE id=1",
                (input_version, payload, result.get("note", ""),
                 result.get("states_used"), result.get("sheets_used")))

    def invalidate_solution(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE solution SET valid=0, saved_at=datetime('now') "
                "WHERE id=1")


class StaleVersionError(Exception):
    """方案所依据的输入版本与当前版本不一致（方案已失效）。"""
