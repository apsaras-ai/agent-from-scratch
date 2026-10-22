"""Chapter 12: SQLite-persisted run events and atomic checkpoint projection.

This is a single-process teaching runtime, NOT Temporal. Transactionally
record command intent and final outcome. An uncertain external effect is never
automatically retried after restart; reconciliation is required.
"""
from __future__ import annotations
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class Conflict(RuntimeError): pass


@dataclass(frozen=True)
class Snapshot:
    run_id: str
    version: int
    status: str
    cursor: int


class DurableStore:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.execute("PRAGMA busy_timeout=3000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs(
            run_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
            status TEXT NOT NULL, cursor INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
            run_id TEXT NOT NULL, version INTEGER NOT NULL,
            event_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(run_id,version), UNIQUE(run_id,event_id));
        CREATE TABLE IF NOT EXISTS operations(
            run_id TEXT NOT NULL, op_id TEXT NOT NULL,
            state TEXT NOT NULL, result TEXT,
            PRIMARY KEY(run_id,op_id));
        """)

    def close(self): self.db.close()

    def create(self, run_id: str):
        with self.db:
            self.db.execute("INSERT INTO runs VALUES(?,0,'created',0)", (run_id,))
        return self.load(run_id)

    def load(self, run_id: str) -> Snapshot:
        row = self.db.execute(
            "SELECT version,status,cursor FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None: raise KeyError(run_id)
        return Snapshot(run_id, *row)

    def append(self, run_id: str, event_id: str, kind: str, payload: dict,
               *, expected_version: int, cursor: int, status: str) -> Snapshot:
        """Event + state projection commit atomically with optimistic CAS."""
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("invalid version")
        if type(cursor) is not int or cursor < 0:
            raise ValueError("invalid cursor")
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            prior = self.db.execute(
                "SELECT version,status,cursor FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if prior is None: raise KeyError(run_id)
            identical = self.db.execute(
                "SELECT kind,payload FROM events WHERE run_id=? AND event_id=?",
                (run_id,event_id)
            ).fetchone()
            if identical is not None:
                if identical != (kind,raw): raise Conflict("event id reused")
                self.db.execute("COMMIT")
                return self.load(run_id)
            if prior[0] != expected_version: raise Conflict("stale version")
            if prior[1] in ("completed","failed","cancelled"):
                raise Conflict("terminal run")
            if cursor < prior[2]: raise Conflict("cursor cannot move backwards")
            self.db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?)",
                (run_id, expected_version+1,event_id,kind,raw))
            self.db.execute(
                "UPDATE runs SET version=?, status=?, cursor=? WHERE run_id=?",
                (expected_version+1,status,cursor,run_id))
            self.db.execute("COMMIT")
            return self.load(run_id)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def begin_operation(self, run_id: str, op_id: str) -> str:
        """Durably record intent BEFORE touching any external side effect."""
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO operations VALUES(?,?,'pending',NULL)",
                (run_id,op_id))
        return self.operation(run_id,op_id)[0]

    def confirm_operation(self, run_id: str, op_id: str, result: str) -> None:
        with self.db:
            row = self.operation(run_id,op_id)
            if row[0] == "confirmed" and row[1] != result:
                raise Conflict("confirmed result conflict")
            self.db.execute(
                "UPDATE operations SET state='confirmed',result=? "
                "WHERE run_id=? AND op_id=?", (result,run_id,op_id))

    def operation(self, run_id: str, op_id: str):
        row = self.db.execute(
            "SELECT state,result FROM operations WHERE run_id=? AND op_id=?",
            (run_id,op_id)).fetchone()
        if row is None: raise KeyError(op_id)
        return row

    def history(self, run_id: str):
        return [
            (version,event_id,kind,json.loads(payload))
            for version,event_id,kind,payload in self.db.execute(
                "SELECT version,event_id,kind,payload FROM events "
                "WHERE run_id=? ORDER BY version",(run_id,))
        ]


def resume_decision(store: DurableStore, run_id: str, op_id: str) -> str:
    """Return safe next action, never replay a possibly executed side effect."""
    state, _ = store.operation(run_id,op_id)
    if state == "confirmed": return "use_recorded_result"
    return "reconcile_external_effect_before_retry"
