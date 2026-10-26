"""Chapter 24: deployment preflight and CAS canary gate (offline only).

No real cluster, pods, service accounts, DNS, ingress, HPA, metrics backend,
auth service, rollout controller, sandbox isolation or external effects.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import json
import tempfile
import unittest

from mini_agent.platform_deployment import (
    CanaryMetrics, CanaryPolicy, DeploymentError, PlatformManifest,
    ReleaseLedger, ReleaseSpec, ServiceProfile, VersionConflict,
    judge_canary, preflight, reference_kubernetes_objects,
)
from mini_agent.platform_fixture import healthy_metrics, sample_platform, sample_release


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = sample_platform()

    def modify(self, service_name, **changes):
        svc = tuple(
            replace(service, **changes) if service.name == service_name else service
            for service in self.manifest.services
        )
        return replace(self.manifest, services=svc)

    def codes(self, manifest):
        return {x.code for x in preflight(manifest)}

    def test_valid_reference_profile_has_no_findings(self):
        self.assertEqual(preflight(self.manifest), ())
        self.assertEqual(len(self.manifest.services), 4)
        self.assertEqual(len(self.manifest.tenants), 2)

    def test_image_digest_must_be_immutable_sha256(self):
        with self.assertRaises(DeploymentError):
            self.modify("agent-api", image_digest="repo/image:latest")
        with self.assertRaises(DeploymentError):
            self.modify("agent-api", image_digest="sha256:abcd")

    def test_unsafe_privileges_are_blocking(self):
        for kw in (
            {"allow_privilege_escalation": True},
            {"host_path": True},
            {"host_network": True},
            {"automount_service_account_token": True},
            {"run_as_non_root": False},
            {"read_only_root": False},
            {"drop_all_capabilities": False},
        ):
            with self.subTest(kw=kw):
                self.assertIn(
                    "unhardened_workload",
                    self.codes(self.modify("agent-api", **kw)),
                )

    def test_egress_must_be_explicit_and_default_denied(self):
        m = self.modify("tool-gateway", network_profile="open-internet")
        self.assertIn("network_default_deny_missing", self.codes(m))
        with self.assertRaises(DeploymentError):
            reference_kubernetes_objects(m)

    def test_untrusted_worker_requires_hardened_isolation(self):
        self.assertIn(
            "untrusted_worker_requires_isolated_runtime",
            self.codes(self.modify("sandbox-worker", isolation="hardened-oci")),
        )
        self.assertIn(
            "sandbox_worker_shares_control_namespace",
            self.codes(self.modify("sandbox-worker", namespace="agent-control")),
        )

    def test_control_components_cannot_claim_worker_runtime(self):
        self.assertIn(
            "control_plane_requires_hardened_oci",
            self.codes(self.modify("agent-api", isolation="gvisor")),
        )
        self.assertIn(
            "control_plane_namespace_mismatch",
            self.codes(self.modify("agent-api", namespace="agent-sandbox")),
        )

    def test_namespace_and_service_account_separation(self):
        t1, t2 = self.manifest.tenants
        broken = replace(self.manifest, tenants=(t1, replace(t2, namespace=t1.namespace)))
        self.assertIn("shared_tenant_namespace", self.codes(broken))
        account = self.manifest.services[0].service_account
        self.assertIn(
            "service_accounts_not_separated",
            self.codes(self.modify("tool-gateway", service_account=account)),
        )
        self.assertIn(
            "tenant_namespace_collides_with_control_plane",
            self.codes(replace(
                self.manifest,
                tenants=(replace(t1, namespace="agent-control"), t2),
            )),
        )

    def test_required_services_and_tenants(self):
        m = replace(self.manifest, services=self.manifest.services[:3])
        self.assertIn("missing_required_service", self.codes(m))
        self.assertIn("no_tenants", self.codes(replace(self.manifest, tenants=())))

    def test_schema_rollback_compatibility_gate(self):
        fail = replace(self.manifest, candidate_min_db_schema=3)
        self.assertIn("rollback_schema_incompatible", self.codes(fail))
        with self.assertRaises(DeploymentError):
            sample_release().__class__(
                "release-1", "old", "new", fail
            )

    def test_resources_validate_requests_and_limits(self):
        with self.assertRaises(DeploymentError):
            self.modify("agent-api", cpu_limit_millis=1)
        with self.assertRaises(DeploymentError):
            self.modify("agent-api", replicas=True)
        with self.assertRaises(DeploymentError):
            self.modify("agent-api", memory_request_mib=0)

    def test_manifest_fingerprint_changes_when_policy_changes(self):
        a = self.manifest.fingerprint()
        self.assertEqual(a, sample_platform().fingerprint())
        b = replace(self.manifest, policy_version="v2").fingerprint()
        self.assertNotEqual(a, b)

    def test_render_is_only_reference_json_and_is_hardened(self):
        objects = reference_kubernetes_objects(self.manifest)
        self.assertEqual(
            sum(x["kind"] == "Deployment" for x in objects), 4
        )
        self.assertEqual(
            sum(x["kind"] == "NetworkPolicy" for x in objects), 4
        )
        self.assertEqual(
            sum(x["kind"] == "Namespace" for x in objects), 4
        )
        serialized = json.dumps(objects)
        self.assertNotIn("privileged\": true", serialized)
        self.assertNotIn("hostPath", serialized)
        self.assertNotIn("imagePullSecrets", serialized)
        self.assertNotIn("token-secret", serialized)
        self.assertTrue(all(
            x["spec"]["policyTypes"] == ["Ingress", "Egress"]
            for x in objects if x["kind"] == "NetworkPolicy"
        ))
        for obj in objects:
            if obj["kind"] != "Deployment":
                continue
            pod = obj["spec"]["template"]["spec"]
            ctx = pod["containers"][0]["securityContext"]
            self.assertFalse(pod["automountServiceAccountToken"])
            self.assertEqual(pod["securityContext"]["seccompProfile"]["type"],
                             "RuntimeDefault")
            self.assertEqual(ctx["capabilities"]["drop"], ["ALL"])
            self.assertTrue(ctx["readOnlyRootFilesystem"])
            self.assertTrue(pod["containers"][0]["image"].count("@sha256:") == 1)
        worker = next(o for o in objects if o["kind"] == "Deployment"
                      and o["metadata"]["name"] == "sandbox-worker")
        self.assertEqual(
            worker["spec"]["template"]["spec"]["runtimeClassName"], "gvisor"
        )


class CanaryGateTests(unittest.TestCase):
    def setUp(self):
        self.baseline, self.good = healthy_metrics()
        self.policy = CanaryPolicy()

    def decision(self, old=None, new=None):
        return judge_canary(
            self.baseline if old is None else old,
            self.good if new is None else new,
            weight=5, next_weight=25, policy=self.policy,
        )

    def test_healthy_window_promotes(self):
        output = self.decision()
        self.assertEqual((output.action, output.next_weight),
                         ("promote", 25))

    def test_candidate_observed_weight_mismatch_pauses(self):
        v = self.decision(new=replace(self.good, observed_weight=25))
        self.assertEqual((v.action, v.reason),
                         ("pause", "observation_weight_mismatch"))

    def test_missing_evidence_pauses(self):
        v = judge_canary(None, self.good, weight=5,
                         next_weight=25, policy=self.policy)
        self.assertEqual((v.action, v.reason), ("pause", "missing_evidence"))

    def test_small_window_pauses_not_autopromote(self):
        short = replace(self.good, attempted=10, verified=10)
        v = self.decision(new=short)
        self.assertEqual(v.action, "pause")
        self.assertEqual(v.next_weight, 5)
        short = replace(self.good, elapsed_seconds=30)
        self.assertEqual(self.decision(new=short).action, "pause")

    def test_security_incident_aborts_even_for_low_sample_count(self):
        v = self.decision(new=replace(
            self.good, security_incidents=1, attempted=3, verified=3
        ))
        self.assertEqual(v.action, "abort")
        self.assertEqual(v.next_weight, 0)

    def test_task_quality_regression_aborts(self):
        v = self.decision(new=replace(self.good, verified=40))
        self.assertEqual(v.reason, "verified_success_regression")
        self.assertEqual(v.action, "abort")

    def test_total_verified_zero_after_sufficient_requests_is_regression(self):
        v = self.decision(new=replace(self.good, verified=0))
        self.assertEqual(v.action, "abort")

    def test_latency_regression_aborts(self):
        v = self.decision(new=replace(self.good, p95_latency_ms=260))
        self.assertEqual(v.reason, "latency_regression")

    def test_cost_per_verified_regression_aborts(self):
        v = self.decision(new=replace(
            self.good, total_cost_usd=Decimal("9")
        ))
        self.assertEqual(v.reason, "cost_regression")

    def test_no_verified_baseline_pauses(self):
        v = self.decision(old=replace(self.baseline, verified=0))
        self.assertEqual(v.reason, "missing_verified_baseline")
        self.assertEqual(v.action, "pause")

    def test_no_latency_baseline_pauses(self):
        v = self.decision(old=replace(self.baseline, p95_latency_ms=0))
        self.assertEqual(v.reason, "missing_latency_baseline")

    def test_no_cost_baseline_pauses(self):
        v = self.decision(old=replace(
            self.baseline, total_cost_usd=Decimal("0")
        ))
        self.assertEqual(v.reason, "missing_cost_baseline")

    def test_invalid_metrics_are_rejected(self):
        with self.assertRaises(DeploymentError):
            replace(self.good, attempted=10, verified=20)
        with self.assertRaises(DeploymentError):
            replace(self.good, p95_latency_ms=float("nan"))
        with self.assertRaises(DeploymentError):
            replace(self.good, total_cost_usd=Decimal("NaN"))
        with self.assertRaises(DeploymentError):
            replace(self.good, source="self_reported_model")
        with self.assertRaises(DeploymentError):
            replace(self.good, window_id="../escape")

    def test_invalid_policy_stages_rejected(self):
        for stages in ((5, 25, 25, 100), (50, 25, 100), (5, 50), (), (100, 10)):
            with self.subTest(stages=stages), self.assertRaises(DeploymentError):
                CanaryPolicy(stages=stages)
        with self.assertRaises(DeploymentError):
            CanaryPolicy(max_cost_ratio=Decimal("0.5"))

    def test_no_quality_bypass_through_zero_attempts(self):
        v = self.decision(new=replace(
            self.good, attempted=0, verified=0
        ))
        self.assertEqual(v.action, "pause")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "rollouts.sqlite"
        self.ledger = ReleaseLedger(self.path)
        self.spec = sample_release()
        self.policy = CanaryPolicy()
        self.baseline, self.canary = healthy_metrics()

    def tearDown(self):
        self.tmp.cleanup()

    def initialize(self):
        return self.ledger.initialize(self.spec, self.policy)

    def decide(self, snap, baseline=None, canary=None):
        scoped_candidate = (
            replace(
                self.canary, observed_weight=snap.weight,
                window_id="window-" + str(snap.weight) + "-" + str(snap.generation),
            )
            if canary is None else canary
        )
        return self.ledger.decide(
            self.spec.release_id,
            expected_generation=snap.generation,
            baseline=self.baseline if baseline is None else baseline,
            candidate=scoped_candidate,
        )

    def test_replayed_measurement_cannot_approve_later_stage(self):
        snap = self.initialize()
        _, first = self.decide(snap, canary=self.canary)
        self.assertEqual(first.weight, 25)
        reused = replace(self.canary, observed_weight=25)
        verdict, after = self.decide(first, canary=reused)
        self.assertEqual((verdict.action, verdict.reason),
                         ("pause", "reused_observation_window"))
        self.assertEqual(after.weight, 25)
        self.assertEqual(after.status, "paused")

    def test_wrong_release_measurements_rejected_without_state_change(self):
        snap = self.initialize()
        with self.assertRaises(DeploymentError):
            self.decide(snap, canary=replace(
                self.canary, release_id="unrelated-release",
            ))
        with self.assertRaises(DeploymentError):
            self.decide(snap, baseline=replace(
                self.baseline, release_id="unrelated-stable",
            ))
        self.assertEqual(self.ledger.load(snap.release_id), snap)

    def test_distinct_stage_windows_can_progress_and_record_count(self):
        snap = self.initialize()
        for _ in range(4):
            _, snap = self.decide(snap)
        self.assertEqual(snap.status, "promoted")
        with self.ledger._connect() as db:
            count = db.execute(
                "SELECT count(*) FROM release_metric_windows "
                "WHERE release_id=?", (self.spec.release_id,)
            ).fetchone()[0]
        self.assertEqual(count, 4)

    def test_release_created_with_5_percent_and_durable_event(self):
        state = self.initialize()
        self.assertEqual((state.weight, state.status, state.generation),
                         (5, "observing", 0))
        self.assertEqual(
            self.ledger.events(state.release_id)[0][1], "initialize"
        )
        new = ReleaseLedger(self.path)
        self.assertEqual(new.load(state.release_id), state)

    def test_exact_reinitialize_is_idempotent(self):
        first = self.initialize()
        self.assertEqual(self.initialize(), first)
        self.assertEqual(len(self.ledger.events(first.release_id)), 1)

    def test_mutated_manifest_or_policy_under_same_id_rejected(self):
        self.initialize()
        changed = replace(
            self.spec,
            manifest=replace(self.spec.manifest, policy_version="v2"),
        )
        with self.assertRaises(DeploymentError):
            self.ledger.initialize(changed, self.policy)
        with self.assertRaises(DeploymentError):
            self.ledger.initialize(self.spec, CanaryPolicy(min_attempts=40))

    def test_progressive_release_requires_four_healthy_evaluations(self):
        s = self.initialize()
        weights = [25, 50, 100, 100]
        for i, expected in enumerate(weights):
            _, s = self.decide(s)
            self.assertEqual(s.weight, expected)
            self.assertEqual(s.generation, i + 1)
        self.assertEqual(s.status, "promoted")
        with self.assertRaises(DeploymentError):
            self.decide(s)
        self.assertEqual(len(self.ledger.events(s.release_id)), 5)

    def test_pause_on_missing_observation_can_resume(self):
        s = self.initialize()
        _, paused = self.ledger.decide(
            self.spec.release_id, expected_generation=0,
            baseline=None, candidate=self.canary,
        )
        self.assertEqual((paused.status, paused.weight), ("paused", 5))
        self.assertEqual(paused.generation, 1)
        _, resumed = self.decide(paused)
        self.assertEqual((resumed.status, resumed.weight), ("observing", 25))

    def test_security_incident_aborts_traffic_in_plan(self):
        s = self.initialize()
        _, after = self.decide(
            s, canary=replace(self.canary, security_incidents=1)
        )
        self.assertEqual((after.status, after.weight), ("aborted", 0))
        self.assertEqual(after.reason, "security_incident")
        with self.assertRaises(DeploymentError):
            self.decide(after)

    def test_stale_generation_rejected(self):
        s = self.initialize()
        _, after = self.decide(s)
        with self.assertRaises(VersionConflict):
            self.decide(s)
        self.assertEqual(after.generation, 1)

    def test_two_controllers_compete_and_only_one_wins(self):
        s = self.initialize()
        a, b = ReleaseLedger(self.path), ReleaseLedger(self.path)
        def attempt(ledger):
            try:
                return ledger.decide(
                    self.spec.release_id, expected_generation=s.generation,
                    baseline=self.baseline, candidate=self.canary,
                )[1].generation
            except VersionConflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (a, b)))
        self.assertEqual(sum(x is not None for x in results), 1)
        self.assertEqual(self.ledger.load(s.release_id).generation, 1)

    def test_stale_approval_cannot_reuse_release_id(self):
        s = self.initialize()
        _, after = self.decide(s)
        with self.assertRaises(VersionConflict):
            self.ledger.decide(s.release_id,
                               expected_generation=0,
                               baseline=self.baseline,
                               candidate=self.canary)
        self.assertEqual(after.generation, 1)

    def test_event_history_has_no_raw_source_or_secret(self):
        state = self.initialize()
        self.decide(state)
        audit = repr(self.ledger.events(state.release_id))
        self.assertNotIn("secret", audit.lower())
        self.assertNotIn("token", audit.lower())
        self.assertEqual(len(self.ledger.events(state.release_id)), 2)

    def test_ledger_requires_filesystem_path(self):
        with self.assertRaises(DeploymentError):
            ReleaseLedger(":memory:")


if __name__ == "__main__":
    unittest.main()
