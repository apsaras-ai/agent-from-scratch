"""Chapter 21: offline transactional-outbox, crash-window and retry tests.

No network, payment, email, Docker, KVM or untrusted code. A FakeProvider
offers authoritative positive/negative/unknown lookup states. The model cannot
grant itself authorization, provider proof, a fake cleanup receipt or rollback.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

from mini_agent.reliability_outbox import (
    BackendEvidence, DispatchLease, EffectMode, Intent, LedgerConflict,
    LedgerError, OutboxStore, SimulatedCrash,
)
from mini_agent.reliability_fixture import SyntheticProvider
from mini_agent.resilience_patterns import (
    CircuitBreaker, CompensationPlan, ResilienceError, RetryPolicy,
    SagaStep, plan_compensation,
)


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.path = Path(self.t.name) / "outbox.db"
        self.clock = [100.0]
        self.provider = SyntheticProvider()
        self.store = self.make_store()

    def tearDown(self):
        self.t.cleanup()

    def make_store(self, **settings):
        return OutboxStore(
            self.path, oracle=self.provider,
            allowed_actions=frozenset({"send.synthetic", "write.synthetic"}),
            clock=lambda: self.clock[0],
            lease_seconds=10.0, base_backoff=2.0, max_backoff=8.0,
            max_inflight=2, **settings,
        )

    def spec(self, op="op-a", *, mode=EffectMode.NON_IDEMPOTENT,
             key="req-a", tenant="tenant-a", run="run-a",
             payload=None, attempts=3):
        return Intent(
            op, tenant, run, "send.synthetic", key,
            payload or {"body": "synthetic-message"}, mode, attempts,
        )

    def claim(self, *, worker="worker-a", tenants=frozenset({"tenant-a"})):
        return self.store.claim(worker, tenants)

    def test_atomic_producer_intent_outbox_and_event(self):
        item = self.store.create_intent(self.spec())
        self.assertEqual(item.state, "pending")
        with sqlite3.connect(self.path) as db:
            business = db.execute("SELECT count(*) FROM business_intents").fetchone()[0]
            outbox = db.execute("SELECT count(*) FROM outbox").fetchone()[0]
        self.assertEqual((business, outbox), (1, 1))
        self.assertEqual(
            [e.kind for e in self.store.all_events("op-a")],
            ["intent_committed"],
        )

    def test_simulated_crash_between_local_writes_rolls_back_both(self):
        with self.assertRaises(SimulatedCrash):
            self.store.create_intent(
                self.spec(), simulate_crash_after_business_insert=True
            )
        with sqlite3.connect(self.path) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM business_intents").fetchone()[0],
                0,
            )
            self.assertEqual(
                db.execute("SELECT count(*) FROM outbox").fetchone()[0],
                0,
            )
        self.assertEqual(self.store.create_intent(self.spec()).state, "pending")

    def test_duplicate_replay_exact_request_is_idempotent(self):
        intent = self.spec()
        first = self.store.create_intent(intent)
        second = self.store.create_intent(intent)
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.all_events("op-a")), 1)

    def test_conflicting_replay_key_or_content_refused(self):
        self.store.create_intent(self.spec())
        for conflicting in (
            self.spec(payload={"body": "modified"}),
            self.spec(op="op-b"),
            self.spec(key="new-key"),
            self.spec(mode=EffectMode.IDEMPOTENT),
        ):
            with self.subTest(conflicting=conflicting), self.assertRaises(LedgerConflict):
                self.store.create_intent(conflicting)

    def test_same_request_key_different_tenant_is_allowed(self):
        self.store.create_intent(self.spec())
        self.store.create_intent(self.spec("op-b", tenant="tenant-b"))
        self.assertEqual(self.store.get("op-b").tenant_id, "tenant-b")

    def test_restart_recovers_pending_and_inflight_records(self):
        self.store.create_intent(self.spec())
        self.assertEqual(self.make_store().get("op-a").state, "pending")
        lease = self.claim()
        fresh = self.make_store()
        self.assertEqual(fresh.get("op-a").state, "inflight")
        self.assertEqual(fresh.get("op-a").attempts, 1)
        self.assertEqual(fresh.capacity_used(), 1)
        self.assertEqual(fresh.acknowledge(lease).state, "pending")

    def test_atomic_claim_two_independent_connections(self):
        self.store.create_intent(self.spec())
        q1, q2 = self.make_store(), self.make_store()
        def try_claim(q):
            return q.claim("worker-a", frozenset({"tenant-a"}))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(try_claim, (q1, q2)))
        self.assertEqual(sum(x is not None for x in results), 1)
        self.assertEqual(self.store.get("op-a").attempts, 1)

    def test_worker_scope_blocks_unapproved_tenant(self):
        self.store.create_intent(self.spec())
        self.assertIsNone(self.claim(tenants=frozenset({"tenant-b"})))
        self.assertEqual(self.store.get("op-a").state, "pending")
        self.assertEqual(self.claim().tenant_id, "tenant-a")

    def test_lease_heartbeat_extended_and_expired_fenced(self):
        self.store.create_intent(self.spec(mode=EffectMode.IDEMPOTENT))
        lease = self.claim()
        self.clock[0] = 105.0
        self.assertTrue(self.store.heartbeat(lease))
        self.assertEqual(self.store.get("op-a").lease_deadline, 115.0)
        self.clock[0] = 115.0
        self.assertFalse(self.store.heartbeat(lease))
        with self.assertRaises(LedgerConflict):
            self.store.acknowledge(lease)
        self.assertEqual(self.store.expire(), ("op-a",))

    def test_known_provider_receipt_is_verified_not_worker_text(self):
        self.store.create_intent(self.spec())
        lease = self.claim()
        receipt = self.provider.apply(lease)
        confirmed = self.store.acknowledge(lease)
        self.assertEqual(confirmed.state, "confirmed")
        self.assertEqual(confirmed.receipt, receipt)
        self.assertEqual(self.provider.committed_effects, 1)
        self.assertEqual(self.store.capacity_used(), 0)
        self.assertIsNone(self.claim())

    def test_ack_before_provider_dispatch_cannot_fake_success(self):
        self.store.create_intent(self.spec())
        lease = self.claim()
        result = self.store.acknowledge(lease)
        self.assertEqual(result.state, "pending")
        self.assertIsNone(result.receipt)
        self.assertEqual(self.provider.committed_effects, 0)
        self.assertIsNone(self.claim())

    def test_crash_after_nonidempotent_provider_commit_never_resends(self):
        self.store.create_intent(self.spec())
        lease = self.claim()
        receipt = self.provider.apply(lease)
        # Crash after external commit, before scheduler ACK:
        self.clock[0] = 110
        self.assertEqual(self.store.expire(), ("op-a",))
        self.assertEqual(self.store.capacity_used(), 1)
        self.assertIsNone(self.claim())
        with self.assertRaises(LedgerConflict):
            self.store.acknowledge(lease)
        after_reaper = self.store.confirm_reaped(
            "op-a", lease.generation, actually_destroyed=True
        )
        self.assertEqual(after_reaper.state, "needs_reconciliation")
        self.assertEqual(self.store.capacity_used(), 0)
        self.assertIsNone(self.claim())
        recovered = self.make_store().reconcile("op-a")
        self.assertEqual(recovered.state, "confirmed")
        self.assertEqual(recovered.receipt, receipt)
        self.assertEqual(self.provider.attempted_requests, 1)
        self.assertEqual(self.provider.committed_effects, 1)

    def test_nonidempotent_no_dispatch_proven_negative_allows_later_retry(self):
        self.store.create_intent(self.spec())
        lease = self.claim()
        self.clock[0] = 110
        self.store.expire()
        self.store.confirm_reaped("op-a", 1, actually_destroyed=True)
        result = self.store.reconcile("op-a")
        self.assertEqual(result.state, "pending")
        self.assertEqual(result.next_ready_at, 112)
        self.assertIsNone(self.claim())
        self.clock[0] = 112
        second = self.claim()
        self.assertEqual((second.attempt, second.generation), (2, 2))
        self.provider.apply(second)
        self.assertEqual(self.store.acknowledge(second).state, "confirmed")

    def test_idempotent_retry_after_reap_reuses_same_provider_key(self):
        self.store.create_intent(self.spec(mode=EffectMode.IDEMPOTENT))
        lease = self.claim()
        receipt1 = self.provider.apply(lease)
        self.clock[0] = 110
        self.store.expire()
        # Reaper confirmation is required even for idempotent effects.
        with self.assertRaises(LedgerError):
            self.store.confirm_reaped(
                "op-a", 1, actually_destroyed=False
            )
        self.assertEqual(self.store.capacity_used(), 1)
        queued = self.store.confirm_reaped(
            "op-a", 1, actually_destroyed=True
        )
        self.assertEqual(queued.state, "pending")
        self.clock[0] = 112
        second = self.claim()
        self.assertNotEqual(second.token, lease.token)
        self.assertEqual(second.generation, 2)
        receipt2 = self.provider.apply(second)
        self.assertEqual(receipt1, receipt2)
        self.assertEqual(self.provider.attempted_requests, 2)
        self.assertEqual(self.provider.committed_effects, 1)
        self.assertEqual(self.store.acknowledge(second).state, "confirmed")

    def test_nonidempotent_uncertain_handler_outcome_requires_reconciliation(self):
        self.store.create_intent(self.spec())
        lease = self.claim()
        # Handler may have committed even if it raised/connection reset.
        self.provider.apply(lease)
        pending = self.store.mark_uncertain(lease)
        self.assertEqual(pending.state, "reap_required")
        self.assertEqual(self.store.capacity_used(), 1)
        self.store.confirm_reaped("op-a", 1, actually_destroyed=True)
        self.assertEqual(self.store.reconcile("op-a").state, "confirmed")
        self.assertEqual(self.provider.committed_effects, 1)

    def test_unknown_oracle_fails_closed_without_retry(self):
        self.store.create_intent(self.spec())
        lease = self.claim()
        self.provider.force_unknown_inspection = True
        self.assertEqual(self.store.acknowledge(lease).state,
                         "needs_reconciliation")
        again = self.store.reconcile("op-a")
        self.assertEqual(again.state, "needs_reconciliation")
        self.assertIsNone(self.claim())
        self.assertEqual(self.provider.attempted_requests, 0)

    def test_reap_requires_generation_and_real_host_confirmation(self):
        self.store.create_intent(self.spec())
        lease = self.claim()
        self.clock[0] = 110
        self.store.expire()
        with self.assertRaises(LedgerError):
            self.store.confirm_reaped(
                "op-a", 1, actually_destroyed=False
            )
        with self.assertRaises(LedgerConflict):
            self.store.confirm_reaped(
                "op-a", 2, actually_destroyed=True
            )
        self.store.confirm_reaped("op-a", 1, actually_destroyed=True)
        with self.assertRaises(LedgerConflict):
            self.store.confirm_reaped("op-a", 1, actually_destroyed=True)

    def test_max_inflight_includes_reap_required(self):
        db = OutboxStore(
            self.path, oracle=self.provider,
            allowed_actions=frozenset({"send.synthetic"}),
            clock=lambda: self.clock[0], lease_seconds=10,
            max_inflight=1,
        )
        db.create_intent(self.spec())
        db.create_intent(self.spec("op-b", key="req-b"))
        first = db.claim("worker", frozenset({"tenant-a"}))
        self.clock[0] = 110
        db.expire()
        self.assertEqual(db.capacity_used(), 1)
        self.assertIsNone(db.claim("worker", frozenset({"tenant-a"})))
        db.confirm_reaped(first.op_id, 1, actually_destroyed=True)
        self.assertEqual(db.claim("worker", frozenset({"tenant-a"})).op_id,
                         "op-b")

    def test_cancellation_before_dispatch_does_not_apply(self):
        self.store.create_intent(self.spec())
        done = self.store.cancel("op-a")
        self.assertEqual(done.state, "cancelled")
        self.assertTrue(done.cancel_requested)
        self.assertIsNone(self.claim())
        self.assertEqual(self.provider.committed_effects, 0)

    def test_running_cancel_requires_reap_and_external_effect_check(self):
        self.store.create_intent(self.spec())
        lease = self.claim()
        self.provider.apply(lease)
        self.assertEqual(self.store.cancel("op-a").state, "inflight")
        self.assertFalse(self.store.heartbeat(lease))
        with self.assertRaises(LedgerConflict):
            self.store.acknowledge(lease)
        self.clock[0] = 110
        self.store.expire()
        self.store.confirm_reaped("op-a", 1, actually_destroyed=True)
        self.assertEqual(self.store.reconcile("op-a").state, "confirmed")

    def test_cancel_after_unknown_and_proven_not_applied(self):
        self.store.create_intent(self.spec())
        lease = self.claim()
        self.store.mark_uncertain(lease)
        self.store.cancel("op-a")
        self.store.confirm_reaped("op-a", 1, actually_destroyed=True)
        self.assertEqual(self.store.reconcile("op-a").state, "cancelled")
        self.assertEqual(self.provider.committed_effects, 0)

    def test_budget_exhaustion_moves_to_dead_letter(self):
        self.store.create_intent(
            self.spec(mode=EffectMode.IDEMPOTENT, attempts=1)
        )
        lease = self.claim()
        self.clock[0] = 110
        self.store.expire()
        self.assertEqual(
            self.store.confirm_reaped(
                "op-a", 1, actually_destroyed=True
            ).state,
            "dead_letter",
        )
        self.assertIsNone(self.claim())

    def test_stale_generation_and_wrong_token_cannot_ack(self):
        self.store.create_intent(self.spec(mode=EffectMode.IDEMPOTENT))
        lease = self.claim()
        self.clock[0] = 110
        self.store.expire()
        self.store.confirm_reaped("op-a", 1, actually_destroyed=True)
        self.clock[0] = 112
        second = self.claim()
        self.provider.apply(second)
        for stale in (
            lease, replace(second, token="tampered"),
            replace(second, worker_id="impersonated"),
            replace(second, generation=1),
        ):
            with self.subTest(stale=stale), self.assertRaises(LedgerConflict):
                self.store.acknowledge(stale)
        self.assertEqual(self.store.acknowledge(second).state, "confirmed")

    def test_redacted_event_log_excludes_secrets_and_lease_token(self):
        secret = "SYNTHETIC_SECRET_FOR_LOG_TEST"
        self.store.create_intent(
            self.spec(payload={"body": secret})
        )
        lease = self.claim()
        self.provider.apply(lease)
        self.store.acknowledge(lease)
        history = repr(self.store.all_events("op-a"))
        self.assertNotIn(secret, history)
        self.assertNotIn(lease.token, history)
        self.assertIn("confirmed", history)

    def test_outbox_validation(self):
        for invalid in (
            lambda: self.spec("path/escape"),
            lambda: self.spec(mode="unknown"),
            lambda: self.spec(attempts=0),
            lambda: self.spec(payload={"non_finite": float("nan")}),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(LedgerError):
                invalid()
        with self.assertRaises(LedgerError):
            self.store.create_intent(replace(self.spec(), action="unknown"))
        with self.assertRaises(LedgerError):
            self.store.claim("", frozenset({"tenant-a"}))

    def test_callback_is_not_allowed_to_report_invalid_backend_evidence(self):
        class WrongOracle:
            def inspect(self, spec):
                return {"status": "committed", "receipt": "fake"}
        queue = OutboxStore(
            self.path, oracle=WrongOracle(),
            allowed_actions=frozenset({"send.synthetic"}),
            clock=lambda: self.clock[0],
        )
        queue.create_intent(self.spec())
        lease = queue.claim("worker", frozenset({"tenant-a"}))
        with self.assertRaises(LedgerError):
            queue.acknowledge(lease)


class ResilienceTests(unittest.TestCase):
    def test_full_jitter_is_stable_bounded_and_spread(self):
        policy = RetryPolicy(max_attempts=5, base_seconds=1, cap_seconds=4)
        samples = [policy.delay("request-" + str(i), 1) for i in range(100)]
        self.assertEqual(policy.delay("same", 2), policy.delay("same", 2))
        self.assertTrue(all(0 <= x <= 1 for x in samples))
        self.assertGreater(len(set(round(x, 2) for x in samples)), 20)
        self.assertIsNone(policy.delay("same", 5))
        self.assertLessEqual(policy.delay("same", 4), 4)

    def test_retry_policy_rejects_invalid_inputs(self):
        for fn in (
            lambda: RetryPolicy(max_attempts=0),
            lambda: RetryPolicy(base_seconds=float("inf")),
            lambda: RetryPolicy(cap_seconds=.1, base_seconds=1),
            lambda: RetryPolicy().delay("", 1),
            lambda: RetryPolicy().delay("key", True),
            lambda: RetryPolicy().delay("key", 9),
        ):
            with self.subTest(fn=fn), self.assertRaises(ResilienceError):
                fn()

    def test_circuit_closed_opens_after_failure_threshold(self):
        now = [0.0]
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=5,
                                 clock=lambda: now[0])
        first = breaker.acquire()
        self.assertEqual(breaker.record(first, succeeded=False), "closed")
        second = breaker.acquire()
        self.assertEqual(breaker.record(second, succeeded=False), "open")
        self.assertIsNone(breaker.acquire())
        now[0] = 4.99
        self.assertIsNone(breaker.acquire())

    def test_half_open_allows_exactly_one_probe_and_closes_on_success(self):
        now = [0.0]
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=5,
                                 clock=lambda: now[0])
        breaker.record(breaker.acquire(), succeeded=False)
        now[0] = 5
        p = breaker.acquire()
        self.assertTrue(p.probe)
        self.assertIsNone(breaker.acquire())
        self.assertEqual(breaker.record(p, succeeded=True), "closed")
        self.assertIsNotNone(breaker.acquire())

    def test_half_open_probe_failure_reopens_for_cooldown(self):
        now = [0.0]
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=4,
                                 clock=lambda: now[0])
        breaker.record(breaker.acquire(), succeeded=False)
        now[0] = 4
        probe = breaker.acquire()
        self.assertEqual(breaker.record(probe, succeeded=False), "open")
        now[0] = 7
        self.assertIsNone(breaker.acquire())
        now[0] = 8
        self.assertTrue(breaker.acquire().probe)

    def test_circuit_release_without_call_and_duplicate_permit_rejected(self):
        b = CircuitBreaker()
        p = b.acquire()
        b.release(p)
        with self.assertRaises(ResilienceError):
            b.record(p, succeeded=True)
        with self.assertRaises(ResilienceError):
            b.release(p)

    def test_saga_reverse_order_requires_host_verified_states(self):
        steps = (
            SagaStep("reserve", "reserve_item", "release_item"),
            SagaStep("charge", "charge_card", "refund_card"),
            SagaStep("notify", "send_message", None),
        )
        result = plan_compensation(steps, {
            "reserve": "committed", "charge": "committed",
            "notify": "not_started",
        })
        self.assertEqual(result.status, "proposed")
        self.assertEqual(result.actions, ("refund_card", "release_item"))

    def test_saga_unknown_blocks_automatic_compensation(self):
        steps = (
            SagaStep("a", "reserve", "release"),
            SagaStep("b", "charge", "refund"),
        )
        result = plan_compensation(steps, {
            "a": "committed", "b": "unknown",
        })
        self.assertEqual(result.status, "blocked_unknown")
        self.assertEqual(result.actions, ())

    def test_saga_irreversible_effect_requires_manual_review(self):
        steps = (SagaStep("notify", "publish", None),)
        result = plan_compensation(steps, {"notify": "committed"})
        self.assertEqual(result.status, "manual_review_required")
        self.assertEqual(result.actions, ())

    def test_saga_requires_complete_consistent_state(self):
        with self.assertRaises(ResilienceError):
            plan_compensation(
                (SagaStep("a", "do"), SagaStep("a", "do_again")),
                {"a": "committed"},
            )
        with self.assertRaises(ResilienceError):
            plan_compensation((SagaStep("a", "do"),), {"a": "model_says_done"})


if __name__ == "__main__":
    unittest.main()
