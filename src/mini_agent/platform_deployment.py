"""Chapter 24: Agent Platform deployment preflight and canary release gate.

The module is a trusted, OFFLINE control-plane teaching reference.
- Configuration and canary observations are supplied by a trusted host.
- It NEVER authenticates users, deploys Kubernetes resources, modifies
  traffic, launches pods/VMs, requests secrets, or accesses any network.
- Validation is a conservative *subset* of deployment-policy checks.
- A permitted rollout decision is NOT evidence that cluster traffic changed.
- Rolling back code traffic does NOT undo completed external tool effects.

Production must use IAM, admission controllers, signed image verification,
NetworkPolicy enforcement, Kubernetes/Argo controllers, actual rollback
receipts, isolated workers, durable operations and audited telemetry.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator


class DeploymentError(ValueError):
    """A safe error reason with no secret or manifest payload."""


class VersionConflict(DeploymentError):
    """The release changed since the host read it."""


_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUIRED_SERVICES = frozenset({
    "agent-api", "agent-scheduler", "tool-gateway", "sandbox-worker",
})
_ALLOWED_RUNTIME = frozenset({"hardened-oci", "gvisor", "microvm"})


def _valid_name(value: str) -> bool:
    return isinstance(value, str) and bool(_NAME.fullmatch(value))


def _positive_int(value: int) -> bool:
    return type(value) is int and value >= 1


def _money(value: Decimal | str) -> Decimal:
    try:
        val = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DeploymentError("invalid_cost_value") from exc
    if not val.is_finite() or val < 0:
        raise DeploymentError("invalid_cost_value")
    return val


@dataclass(frozen=True)
class ServiceProfile:
    name: str
    namespace: str
    service_account: str
    image_digest: str
    replicas: int
    cpu_request_millis: int
    cpu_limit_millis: int
    memory_request_mib: int
    memory_limit_mib: int
    isolation: str
    network_profile: str = "deny-default-egress-proxy"
    run_as_non_root: bool = True
    read_only_root: bool = True
    drop_all_capabilities: bool = True
    allow_privilege_escalation: bool = False
    host_network: bool = False
    host_path: bool = False
    automount_service_account_token: bool = False

    def __post_init__(self) -> None:
        if not all(_valid_name(x) for x in (
            self.name, self.namespace, self.service_account,
        )):
            raise DeploymentError("invalid_service_identity")
        if not isinstance(self.image_digest, str) or not _DIGEST.fullmatch(self.image_digest):
            raise DeploymentError("image_digest_required")
        if any(not _positive_int(x) for x in (
            self.replicas, self.cpu_request_millis, self.cpu_limit_millis,
            self.memory_request_mib, self.memory_limit_mib,
        )):
            raise DeploymentError("invalid_resources_or_replicas")
        if (self.cpu_limit_millis < self.cpu_request_millis or
                self.memory_limit_mib < self.memory_request_mib):
            raise DeploymentError("resource_limit_below_request")
        if self.isolation not in _ALLOWED_RUNTIME:
            raise DeploymentError("unrecognized_isolation_class")


@dataclass(frozen=True)
class TenantProfile:
    tenant_id: str
    namespace: str
    max_queued: int
    max_running: int
    cpu_quota_millis: int
    memory_quota_mib: int

    def __post_init__(self) -> None:
        if not _valid_name(self.tenant_id) or not _valid_name(self.namespace):
            raise DeploymentError("invalid_tenant_or_namespace")
        if any(not _positive_int(x) for x in (
            self.max_queued, self.max_running, self.cpu_quota_millis,
            self.memory_quota_mib,
        )):
            raise DeploymentError("invalid_tenant_quota")
        if self.max_running > self.max_queued:
            raise DeploymentError("tenant_running_exceeds_queue_budget")


@dataclass(frozen=True)
class PlatformManifest:
    platform_id: str
    policy_version: str
    services: tuple[ServiceProfile, ...]
    tenants: tuple[TenantProfile, ...]
    stable_db_schema: int
    candidate_min_db_schema: int
    candidate_max_db_schema: int

    def __post_init__(self) -> None:
        if not _valid_name(self.platform_id) or not _valid_name(self.policy_version):
            raise DeploymentError("invalid_platform_identity")
        if (not isinstance(self.services, tuple) or not isinstance(self.tenants, tuple)
                or not all(isinstance(x, ServiceProfile) for x in self.services)
                or not all(isinstance(x, TenantProfile) for x in self.tenants)):
            raise DeploymentError("invalid_manifest_collections")
        if any(type(x) is not int or x < 1 for x in (
            self.stable_db_schema, self.candidate_min_db_schema,
            self.candidate_max_db_schema,
        )):
            raise DeploymentError("invalid_schema_version")

    def fingerprint(self) -> str:
        data = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
        return sha256(data.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Finding:
    code: str
    component: str


def preflight(manifest: PlatformManifest) -> tuple[Finding, ...]:
    """Strictly inspect an approved manifest without contacting a cluster.

    Presence of fields is NOT proof of K8s enforcement. Findings are stable
    error codes: no secrets, user payload, or arbitrary source content.
    """
    if not isinstance(manifest, PlatformManifest):
        raise DeploymentError("platform_manifest_required")
    findings: list[Finding] = []
    def reject(code: str, component: str) -> None:
        findings.append(Finding(code, component))

    names = [x.name for x in manifest.services]
    for missing in sorted(_REQUIRED_SERVICES - set(names)):
        reject("missing_required_service", missing)
    if len(names) != len(set(names)):
        reject("duplicate_service", "platform")
    if not manifest.tenants:
        reject("no_tenants", "platform")
    tenant_names = [t.tenant_id for t in manifest.tenants]
    if len(set(tenant_names)) != len(tenant_names):
        reject("duplicate_tenant", "platform")
    namespaces = [t.namespace for t in manifest.tenants]
    if len(set(namespaces)) != len(namespaces):
        reject("shared_tenant_namespace", "platform")
    if set(namespaces).intersection(x.namespace for x in manifest.services):
        reject("tenant_namespace_collides_with_control_plane", "platform")
    if not (manifest.candidate_min_db_schema <= manifest.stable_db_schema
            <= manifest.candidate_max_db_schema):
        reject("rollback_schema_incompatible", "database")

    accounts = [x.service_account for x in manifest.services]
    if len(accounts) != len(set(accounts)):
        reject("service_accounts_not_separated", "platform")
    for svc in manifest.services:
        if svc.network_profile != "deny-default-egress-proxy":
            reject("network_default_deny_missing", svc.name)
        if (not svc.run_as_non_root or not svc.read_only_root or
                not svc.drop_all_capabilities or svc.allow_privilege_escalation or
                svc.host_network or svc.host_path or
                svc.automount_service_account_token):
            reject("unhardened_workload", svc.name)
        if svc.name == "sandbox-worker":
            if svc.isolation not in ("gvisor", "microvm"):
                reject("untrusted_worker_requires_isolated_runtime", svc.name)
            if svc.namespace == "agent-control":
                reject("sandbox_worker_shares_control_namespace", svc.name)
        else:
            if svc.isolation != "hardened-oci":
                reject("control_plane_requires_hardened_oci", svc.name)
            if svc.namespace != "agent-control":
                reject("control_plane_namespace_mismatch", svc.name)
    return tuple(sorted(set(findings), key=lambda f: (f.code, f.component)))


def reference_kubernetes_objects(manifest: PlatformManifest) -> tuple[dict[str, Any], ...]:
    """Render limited examples, NOT a complete installable production stack.

    JSON is valid YAML syntax; objects are not applied. Cluster operator must
    create ServiceAccounts, RBAC, real network-proxy allow rules, workload
    service routing, secrets, admission policy and an isolated RuntimeClass.
    A label declaring Pod Security 'restricted' alone cannot enforce it
    without the admission controller configured and enabled.
    """
    if preflight(manifest):
        raise DeploymentError("deployment_preflight_failed")
    namespaces = sorted({svc.namespace for svc in manifest.services} |
                        {tenant.namespace for tenant in manifest.tenants})
    items: list[dict[str, Any]] = []
    for ns in namespaces:
        items.append({
            "apiVersion": "v1", "kind": "Namespace",
            "metadata": {"name": ns, "labels": {
                "pod-security.kubernetes.io/enforce": "restricted",
            }},
        })
        items.append({
            "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {"name": "default-deny", "namespace": ns},
            "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
        })
    for svc in sorted(manifest.services, key=lambda s: s.name):
        container: dict[str, Any] = {
            "name": svc.name,
            # Image registry/repository path must be fixed separately;
            # this is an immutable digest placeholder, not a runnable image.
            "image": "registry.invalid/agent/" + svc.name + "@" + svc.image_digest,
            "resources": {"requests": {
                "cpu": str(svc.cpu_request_millis) + "m",
                "memory": str(svc.memory_request_mib) + "Mi",
            }, "limits": {
                "cpu": str(svc.cpu_limit_millis) + "m",
                "memory": str(svc.memory_limit_mib) + "Mi",
            }},
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "readOnlyRootFilesystem": True,
                "runAsNonRoot": True,
                "capabilities": {"drop": ["ALL"]},
            },
        }
        pod_spec = {
            "serviceAccountName": svc.service_account,
            "automountServiceAccountToken": False,
            "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
            "hostNetwork": False,
            "containers": [container],
        }
        if svc.isolation != "hardened-oci":
            # RuntimeClass 'gvisor' or 'microvm' MUST exist and be reviewed
            # independently: a string in JSON is not a sandbox.
            pod_spec["runtimeClassName"] = svc.isolation
        items.append({
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": svc.name, "namespace": svc.namespace},
            "spec": {
                "replicas": svc.replicas,
                "selector": {"matchLabels": {"app": svc.name}},
                "template": {
                    "metadata": {"labels": {"app": svc.name}},
                    "spec": pod_spec,
                },
            },
        })
    return tuple(items)


@dataclass(frozen=True)
class CanaryPolicy:
    stages: tuple[int, ...] = (5, 25, 50, 100)
    min_attempts: int = 20
    min_seconds: int = 60
    min_verified_rate: float = .96
    max_latency_ratio: float = 1.25
    max_cost_ratio: Decimal = Decimal("1.15")

    def __post_init__(self) -> None:
        if (not isinstance(self.stages, tuple) or not self.stages or
                any(type(x) is not int or x < 1 or x > 100 for x in self.stages)
                or tuple(sorted(set(self.stages))) != self.stages
                or self.stages[-1] != 100):
            raise DeploymentError("invalid_canary_stages")
        if (not _positive_int(self.min_attempts) or
                not _positive_int(self.min_seconds)):
            raise DeploymentError("invalid_minimum_evidence")
        if (type(self.min_verified_rate) not in (int, float)
                or not math.isfinite(self.min_verified_rate)
                or not 0 < self.min_verified_rate <= 1):
            raise DeploymentError("invalid_success_rate_policy")
        if (type(self.max_latency_ratio) not in (int, float)
                or not math.isfinite(self.max_latency_ratio)
                or self.max_latency_ratio < 1):
            raise DeploymentError("invalid_latency_ratio_policy")
        object.__setattr__(self, "max_cost_ratio", _money(self.max_cost_ratio))
        if self.max_cost_ratio < 1:
            raise DeploymentError("invalid_cost_ratio_policy")


@dataclass(frozen=True)
class CanaryMetrics:
    """Trusted aggregated observations, never model-generated 'done' text."""
    elapsed_seconds: int
    attempted: int
    verified: int
    p95_latency_ms: float
    total_cost_usd: Decimal
    security_incidents: int
    source: str = "trusted-observer"

    def __post_init__(self) -> None:
        if any(type(v) is not int or v < 0 for v in (
            self.elapsed_seconds, self.attempted, self.verified,
            self.security_incidents,
        )) or self.verified > self.attempted:
            raise DeploymentError("invalid_canary_counters")
        if (type(self.p95_latency_ms) not in (float, int)
                or not math.isfinite(self.p95_latency_ms)
                or self.p95_latency_ms < 0):
            raise DeploymentError("invalid_latency_measurement")
        object.__setattr__(self, "total_cost_usd", _money(self.total_cost_usd))
        if self.source != "trusted-observer":
            raise DeploymentError("trusted_observation_required")


@dataclass(frozen=True)
class RolloutJudgment:
    action: str                 # promote, pause, abort
    reason: str                 # safe code
    current_weight: int
    next_weight: int


def judge_canary(
    baseline: CanaryMetrics | None,
    candidate: CanaryMetrics | None,
    *,
    weight: int,
    next_weight: int,
    policy: CanaryPolicy,
) -> RolloutJudgment:
    """No evidence -> PAUSE, hard guardrail failure -> ABORT.

    Independent verification counts come from trusted telemetry. The
    function does not and cannot validate which upstream service supplied
    the numbers. Caller authentication belongs outside this Python module.
    """
    if weight not in policy.stages or next_weight not in (0, *policy.stages):
        raise DeploymentError("invalid_rollout_weight")
    if baseline is None or candidate is None:
        return RolloutJudgment("pause", "missing_evidence", weight, weight)
    if candidate.security_incidents:
        return RolloutJudgment("abort", "security_incident", weight, 0)
    if (candidate.elapsed_seconds < policy.min_seconds or
            baseline.elapsed_seconds < policy.min_seconds or
            candidate.attempted < policy.min_attempts or
            baseline.attempted < policy.min_attempts):
        return RolloutJudgment("pause", "insufficient_observation", weight, weight)
    if not baseline.verified:
        return RolloutJudgment("pause", "missing_verified_baseline", weight, weight)
    if candidate.verified / candidate.attempted < policy.min_verified_rate:
        return RolloutJudgment("abort", "verified_success_regression", weight, 0)
    if baseline.p95_latency_ms == 0:
        return RolloutJudgment("pause", "missing_latency_baseline", weight, weight)
    if candidate.p95_latency_ms > baseline.p95_latency_ms * policy.max_latency_ratio:
        return RolloutJudgment("abort", "latency_regression", weight, 0)
    baseline_unit = baseline.total_cost_usd / Decimal(baseline.verified)
    if baseline_unit == 0:
        return RolloutJudgment("pause", "missing_cost_baseline", weight, weight)
    candidate_unit = candidate.total_cost_usd / Decimal(candidate.verified)
    if candidate_unit > baseline_unit * policy.max_cost_ratio:
        return RolloutJudgment("abort", "cost_regression", weight, 0)
    return RolloutJudgment("promote", "all_gates_passed", weight, next_weight)


@dataclass(frozen=True)
class ReleaseSpec:
    release_id: str
    stable_version: str
    candidate_version: str
    manifest: PlatformManifest

    def __post_init__(self) -> None:
        if any(not _valid_name(x) for x in (
            self.release_id, self.stable_version, self.candidate_version
        )) or self.stable_version == self.candidate_version:
            raise DeploymentError("invalid_release_identity")
        if not isinstance(self.manifest, PlatformManifest):
            raise DeploymentError("manifest_required")
        if preflight(self.manifest):
            raise DeploymentError("deployment_preflight_failed")


@dataclass(frozen=True)
class ReleaseSnapshot:
    release_id: str
    stable_version: str
    candidate_version: str
    manifest_hash: str
    policy_hash: str
    generation: int
    status: str
    weight: int
    stage_index: int
    reason: str


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


class ReleaseLedger:
    """SQLite CAS history of decisions, NOT cluster traffic changes.

    Changing the 'weight' means writing a *desired rollout decision* here.
    A real host must independently execute the operation through a trusted
    traffic controller and reconcile its actual applied state and receipts.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(path) == ":memory:" or not self.path.parent.is_dir():
            raise DeploymentError("persistent_ledger_file_required")
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS releases(
                release_id TEXT PRIMARY KEY,
                stable_version TEXT NOT NULL,
                candidate_version TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                policy_hash TEXT NOT NULL,
                policy_json TEXT NOT NULL,
                generation INTEGER NOT NULL,
                status TEXT NOT NULL,
                weight INTEGER NOT NULL,
                stage_index INTEGER NOT NULL,
                reason TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS release_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                previous_weight INTEGER NOT NULL,
                next_weight INTEGER NOT NULL
            );
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        db.execute("PRAGMA busy_timeout=5000")
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> ReleaseSnapshot:
        return ReleaseSnapshot(
            row["release_id"], row["stable_version"],
            row["candidate_version"], row["manifest_hash"],
            row["policy_hash"], row["generation"], row["status"],
            row["weight"], row["stage_index"], row["reason"],
        )

    def load(self, release_id: str) -> ReleaseSnapshot:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM releases WHERE release_id=?", (release_id,)
            ).fetchone()
        if row is None:
            raise KeyError(release_id)
        return self._snapshot(row)

    def initialize(self, spec: ReleaseSpec, policy: CanaryPolicy) -> ReleaseSnapshot:
        if not isinstance(spec, ReleaseSpec) or not isinstance(policy, CanaryPolicy):
            raise DeploymentError("invalid_release_creation")
        manifest_hash = spec.manifest.fingerprint()
        policy_blob = _canonical({
            **asdict(policy), "max_cost_ratio": str(policy.max_cost_ratio),
        })
        policy_hash = sha256(policy_blob.encode("utf-8")).hexdigest()
        with self._tx() as db:
            exists = db.execute(
                "SELECT * FROM releases WHERE release_id=?", (spec.release_id,)
            ).fetchone()
            if exists:
                if (exists["stable_version"] != spec.stable_version or
                        exists["candidate_version"] != spec.candidate_version or
                        exists["manifest_hash"] != manifest_hash or
                        exists["policy_hash"] != policy_hash):
                    raise DeploymentError("release_identity_reuse")
                return self._snapshot(exists)
            weight = policy.stages[0]
            db.execute(
                "INSERT INTO releases VALUES(?,?,?,?,?, ?,0,'observing',?,0,?)",
                (spec.release_id, spec.stable_version, spec.candidate_version,
                 manifest_hash, policy_hash, policy_blob,
                 weight, "initialized"),
            )
            db.execute(
                "INSERT INTO release_events(release_id,generation,action,reason,"
                "previous_weight,next_weight) VALUES(?,0,'initialize','initialized',0,?)",
                (spec.release_id, weight),
            )
            row = db.execute(
                "SELECT * FROM releases WHERE release_id=?", (spec.release_id,)
            ).fetchone()
            return self._snapshot(row)

    def events(self, release_id: str) -> tuple[tuple[Any, ...], ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT generation,action,reason,previous_weight,next_weight "
                "FROM release_events WHERE release_id=? ORDER BY id",
                (release_id,),
            ).fetchall()
        return tuple(tuple(row) for row in rows)

    def decide(
        self, release_id: str, *,
        expected_generation: int,
        baseline: CanaryMetrics | None,
        candidate: CanaryMetrics | None,
    ) -> tuple[RolloutJudgment, ReleaseSnapshot]:
        """Host supplies aggregated evidence from independently trusted signals.

        Checks CAS and the exact saved rollout policy on every transition.
        This is only a decision journal, not a Kubernetes write or rollback.
        """
        if type(expected_generation) is not int or expected_generation < 0:
            raise VersionConflict("invalid_release_generation")
        with self._tx() as db:
            row = db.execute(
                "SELECT * FROM releases WHERE release_id=?", (release_id,)
            ).fetchone()
            if row is None:
                raise KeyError(release_id)
            snap = self._snapshot(row)
            if snap.generation != expected_generation:
                raise VersionConflict("stale_release_generation")
            if snap.status in ("promoted", "aborted"):
                raise DeploymentError("release_is_terminal")
            policy_obj = json.loads(row["policy_json"])
            policy_obj["stages"] = tuple(policy_obj["stages"])
            policy = CanaryPolicy(**policy_obj)
            index = snap.stage_index
            next_weight = (policy.stages[index + 1]
                           if index + 1 < len(policy.stages)
                           else policy.stages[index])
            verdict = judge_canary(
                baseline, candidate, weight=snap.weight,
                next_weight=next_weight, policy=policy,
            )
            if verdict.action == "abort":
                state, index, weight = "aborted", index, 0
            elif verdict.action == "pause":
                state, weight = "paused", snap.weight
            elif index + 1 < len(policy.stages):
                index += 1
                state, weight = "observing", policy.stages[index]
            else:
                state, weight = "promoted", snap.weight
            generation = snap.generation + 1
            db.execute(
                "UPDATE releases SET generation=?,status=?,weight=?,"
                "stage_index=?,reason=? WHERE release_id=?",
                (generation, state, weight, index, verdict.reason, release_id),
            )
            db.execute(
                "INSERT INTO release_events(release_id,generation,action,reason,"
                "previous_weight,next_weight) VALUES(?,?,?,?,?,?)",
                (release_id, generation, verdict.action, verdict.reason,
                 snap.weight, weight),
            )
        return verdict, self.load(release_id)
