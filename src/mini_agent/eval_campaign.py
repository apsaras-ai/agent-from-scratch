"""Chapter 27: durable evaluation campaign, regression analysis and dashboard.

Operates only on Chapter 25/26 deterministic offline benchmark protocol. A
trusted host owns runner adapters and independent graders. This persists safe
aggregates, NOT hidden answers, tool payloads, credentials or model messages.
No actual model, cluster, dashboard server, or network is started.

The dashboard is read-only static HTML. SQLite protects local campaign writes
with BEGIN IMMEDIATE and uses immutable record fingerprints and CAS releases.
This is NOT a secure multi-tenant eval service or authenticated release gate.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator

from .agent_evaluation import EvalError, compare_paired
from .benchmark_framework import (
    BenchmarkResult, BenchmarkProtocol, RegressionGate,
    run_benchmark, gate_candidate,
)

_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class CampaignSpec:
    campaign_id: str
    protocol: BenchmarkProtocol
    baseline_id: str
    candidate_id: str

    def __post_init__(self):
        if (not isinstance(self.protocol, BenchmarkProtocol)
                or any(not isinstance(v, str) or not _ID.fullmatch(v)
                       for v in (self.campaign_id,self.baseline_id,self.candidate_id))
                or self.baseline_id == self.candidate_id):
            raise EvalError("invalid_campaign_spec")

    def digest(self) -> str:
        return sha256(_json((self.campaign_id,self.protocol.digest(),
                             self.baseline_id,self.candidate_id)).encode()).hexdigest()


@dataclass(frozen=True)
class CampaignSnapshot:
    campaign_id: str
    digest: str
    revision: int
    status: str
    reason: str
    baseline_rate: float | None
    candidate_rate: float | None
    paired_improvements: int | None
    paired_regressions: int | None
    baseline_cost: str | None
    candidate_cost: str | None


class CampaignStore:
    """SQLite immutable inputs and CAS final gate; one authoritative file.

    This store saves aggregated metrics only. The caller is responsible for
    authenticating the evaluator, approving the suite, and protecting the file.
    """

    def __init__(self, path: str | Path):
        self.path=Path(path)
        if str(path)==":memory:" or not self.path.parent.is_dir():
            raise EvalError("persistent_campaign_database_required")
        with self._db() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS campaigns(
                campaign_id TEXT PRIMARY KEY,
                spec_digest TEXT NOT NULL,
                revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                baseline_digest TEXT,
                candidate_digest TEXT,
                baseline_summary TEXT,
                candidate_summary TEXT,
                paired_improvements INTEGER,
                paired_regressions INTEGER
            );
            CREATE TABLE IF NOT EXISTS campaign_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                kind TEXT NOT NULL,
                reason TEXT NOT NULL
            );
            """)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn=sqlite3.connect(str(self.path),timeout=5,isolation_level=None)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory=sqlite3.Row
        try: yield conn
        finally: conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def initialize(self,spec:CampaignSpec) -> CampaignSnapshot:
        if not isinstance(spec,CampaignSpec):
            raise EvalError("invalid_campaign")
        with self._tx() as db:
            row=db.execute("SELECT * FROM campaigns WHERE campaign_id=?",
                           (spec.campaign_id,)).fetchone()
            if row:
                if row["spec_digest"] != spec.digest():
                    raise EvalError("campaign_id_reused_for_other_protocol")
            else:
                db.execute(
                    "INSERT INTO campaigns VALUES(?, ?, 0, 'created', 'initialized',"
                    " NULL,NULL,NULL,NULL,NULL,NULL)",
                    (spec.campaign_id,spec.digest()))
                db.execute(
                    "INSERT INTO campaign_events(campaign_id,revision,kind,reason)"
                    " VALUES(?,0,'created','initialized')",(spec.campaign_id,))
        return self.load(spec.campaign_id)

    @staticmethod
    def _snap(row:sqlite3.Row)->CampaignSnapshot:
        baseline=json.loads(row["baseline_summary"]) if row["baseline_summary"] else None
        candidate=json.loads(row["candidate_summary"]) if row["candidate_summary"] else None
        return CampaignSnapshot(
            row["campaign_id"],row["spec_digest"],row["revision"],
            row["status"],row["reason"],
            baseline["micro_success_rate"] if baseline else None,
            candidate["micro_success_rate"] if candidate else None,
            row["paired_improvements"],row["paired_regressions"],
            baseline["cost_per_verified_task_usd"] if baseline else None,
            candidate["cost_per_verified_task_usd"] if candidate else None,
        )

    def load(self,campaign_id:str)->CampaignSnapshot:
        with self._db() as db:
            row=db.execute("SELECT * FROM campaigns WHERE campaign_id=?",
                           (campaign_id,)).fetchone()
        if row is None: raise KeyError(campaign_id)
        return self._snap(row)

    def events(self,campaign_id:str)->tuple[tuple[Any,...],...]:
        with self._db() as db:
            rows=db.execute(
                "SELECT revision,kind,reason FROM campaign_events "
                "WHERE campaign_id=? ORDER BY id",(campaign_id,)).fetchall()
        return tuple(tuple(row) for row in rows)

    def evaluate(
        self, spec:CampaignSpec, suite, baseline_runner, candidate_runner,
        *, gate:RegressionGate, expected_revision:int=0,
    ) -> CampaignSnapshot:
        """Execute fresh complete trials, then commit both results atomically.

        The old state is never overwritten by stale revision. In production
        execute untrusted runners in separate secured workers rather than
        in the same Python process as hidden graders.
        """
        if not isinstance(spec,CampaignSpec) or not isinstance(gate,RegressionGate):
            raise EvalError("invalid_evaluation_inputs")
        a=run_benchmark(suite,spec.protocol,spec.baseline_id,baseline_runner)
        b=run_benchmark(suite,spec.protocol,spec.candidate_id,candidate_runner)
        # In addition to protocol, require identical per-trial key coverage.
        pairs=compare_paired(a.report,b.report)
        decision=gate_candidate(a,b,gate)
        a_summary=a.safe_summary()
        b_summary=b.safe_summary()
        # Non-finite or serialized raw sensitive fields cannot be inserted.
        x,y=_json(a_summary),_json(b_summary)
        with self._tx() as db:
            row=db.execute("SELECT * FROM campaigns WHERE campaign_id=?",
                           (spec.campaign_id,)).fetchone()
            if row is None or row["spec_digest"]!=spec.digest():
                raise EvalError("unknown_or_mismatched_campaign")
            if type(expected_revision) is not int or row["revision"]!=expected_revision:
                raise EvalError("stale_campaign_revision")
            if row["status"]!="created":
                raise EvalError("campaign_already_evaluated")
            db.execute(
                "UPDATE campaigns SET revision=revision+1,status=?,reason=?,"
                "baseline_digest=?,candidate_digest=?,baseline_summary=?,"
                "candidate_summary=?,paired_improvements=?,paired_regressions=? "
                "WHERE campaign_id=?",
                (decision.action,decision.reason,a.report.suite_digest,
                 b.report.suite_digest,x,y,
                 pairs.improved,pairs.regressed,spec.campaign_id))
            db.execute(
                "INSERT INTO campaign_events(campaign_id,revision,kind,reason)"
                " VALUES(?,1,?,?)",(spec.campaign_id,decision.action,decision.reason))
        return self.load(spec.campaign_id)


def dashboard_html(snapshot:CampaignSnapshot)->str:
    """Escape all variable content; return self-contained static report.

    No CDN/JS/network or unsafe embedded run text. For real deployment the
    page still needs trusted HTTP auth and content-security policy.
    """
    if not isinstance(snapshot,CampaignSnapshot):
        raise EvalError("campaign_snapshot_required")
    if snapshot.status not in ("created","approve","block","insufficient_evidence"):
        raise EvalError("unknown_campaign_state")
    def show(value):
        return escape("Unknown" if value is None else str(value),quote=True)
    fields=(
        ("Campaign",snapshot.campaign_id),("Status",snapshot.status),
        ("Reason",snapshot.reason),("Revision",snapshot.revision),
        ("Baseline success rate",snapshot.baseline_rate),
        ("Candidate success rate",snapshot.candidate_rate),
        ("Paired improvements",snapshot.paired_improvements),
        ("Paired regressions",snapshot.paired_regressions),
        ("Baseline cost/verified",snapshot.baseline_cost),
        ("Candidate cost/verified",snapshot.candidate_cost),
    )
    rows="\n".join(
        f"<tr><th>{show(label)}</th><td>{show(value)}</td></tr>"
        for label,value in fields)
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width'>"
        "<meta http-equiv='Content-Security-Policy' "
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        "<title>Agent Evaluation Campaign</title>"
        "<style>body{font:16px system-ui;max-width:800px;margin:3rem auto;"
        "padding:0 1rem;color:#20242a}table{border-collapse:collapse;width:100%}"
        "th,td{border-bottom:1px solid #ddd;padding:12px;text-align:left}"
        "th{width:45%;background:#f6f7f9}</style></head><body>"
        "<h1>Agent Evaluation Campaign</h1><table>"+rows+
        "</table><p>Offline, synthetic fixtures. No real-model performance claims."
        "</p></body></html>"
    )
