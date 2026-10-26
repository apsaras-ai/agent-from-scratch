"""Chapter 21: crash injection and safe durable Agent side-effect recovery.

All effects are IN-MEMORY synthetic provider records. No real emails,
payments, HTTP requests, untrusted code, secrets or sandbox execution.
"""
from __future__ import annotations

from pathlib import Path
import tempfile

from mini_agent.reliability_fixture import SyntheticProvider
from mini_agent.reliability_outbox import (
    EffectMode, Intent, OutboxStore, SimulatedCrash,
)
from mini_agent.resilience_patterns import (
    CircuitBreaker, RetryPolicy, SagaStep, plan_compensation,
)


def spec(name, mode):
    return Intent(
        name, "tenant-demo", "run-demo", "send.synthetic",
        "idem-" + name, {"message": "synthetic " + name}, mode,
    )


def main() -> None:
    clock = [1000.0]
    provider = SyntheticProvider()
    with tempfile.TemporaryDirectory() as folder:
        db = Path(folder) / "outbox.db"
        queue = OutboxStore(
            db, oracle=provider,
            allowed_actions=frozenset({"send.synthetic"}),
            clock=lambda: clock[0], lease_seconds=5,
            base_backoff=2, max_backoff=8,
        )
        print("Scenario A: crash within the local SQLite transaction")
        try:
            queue.create_intent(
                spec("pre-commit", EffectMode.NON_IDEMPOTENT),
                simulate_crash_after_business_insert=True,
            )
        except SimulatedCrash:
            print("  Rolled back both business intent and outbox row")
        try:
            queue.get("pre-commit")
        except KeyError:
            print("  No phantom outbox message exists")

        print("Scenario B: external side effect committed, ACK lost")
        queue.create_intent(spec("effect", EffectMode.NON_IDEMPOTENT))
        first = queue.claim("worker-1", frozenset({"tenant-demo"}))
        receipt = provider.apply(first)
        print("  Fake provider receipt:", receipt)
        # Crash here: no acknowledge(). New Scheduler object opens SAME DB.
        clock[0] = 1005
        recovered = OutboxStore(
            db, oracle=provider,
            allowed_actions=frozenset({"send.synthetic"}),
            clock=lambda: clock[0], lease_seconds=5,
            base_backoff=2, max_backoff=8,
        )
        print("  Expired lease:", recovered.expire())
        print("  Capacity held before host reaping:", recovered.capacity_used())
        recovered.confirm_reaped(
            "effect", first.generation, actually_destroyed=True
        )
        print("  After trusted fake cleanup:", recovered.get("effect").state)
        print("  Automatically claimable:", recovered.claim(
            "worker-2", frozenset({"tenant-demo"})
        ) is not None)
        print("  Host oracle reconciliation:", recovered.reconcile("effect").state)
        print("  Real external effects:", provider.committed_effects)

        print("Scenario C: idempotent provider dedup across retry")
        recovered.create_intent(spec("idempotent", EffectMode.IDEMPOTENT))
        second = recovered.claim("worker-2", frozenset({"tenant-demo"}))
        first_receipt = provider.apply(second)
        clock[0] = 1010
        recovered.expire()
        recovered.confirm_reaped(
            "idempotent", second.generation, actually_destroyed=True
        )
        clock[0] = 1012
        third = recovered.claim("worker-3", frozenset({"tenant-demo"}))
        second_receipt = provider.apply(third)
        recovered.acknowledge(third)
        print("  Same receipt after duplicate delivery:",
              first_receipt == second_receipt)
        print("  Synthetic provider total effects:",
              provider.committed_effects)

        print("Scenario D: circuit breaker, jitter and compensation plan")
        breaker_time = [0.0]
        breaker = CircuitBreaker(
            failure_threshold=2, cooldown_seconds=4,
            clock=lambda: breaker_time[0],
        )
        breaker.record(breaker.acquire(), succeeded=False)
        breaker.record(breaker.acquire(), succeeded=False)
        print("  Circuit after two failures:", breaker.state)
        print("  Allowed immediately:", breaker.acquire() is not None)
        breaker_time[0] = 4
        probe = breaker.acquire()
        breaker.record(probe, succeeded=True)
        print("  Circuit after successful probe:", breaker.state)
        policy = RetryPolicy(max_attempts=4, base_seconds=1)
        print("  Deterministic bounded jitter example:",
              policy.delay("demo-operation", 1))
        plan = plan_compensation(
            (SagaStep("reserve", "reserve", "release"),
             SagaStep("charge", "charge", "refund"),
             SagaStep("notify", "notify", None)),
            {"reserve": "committed", "charge": "committed",
             "notify": "not_started"},
        )
        print("  Proposed compensations (NOT executed):", plan.actions)
        assert recovered.get("effect").state == "confirmed"
        assert recovered.get("idempotent").state == "confirmed"
        assert provider.committed_effects == 2
        print("No actual network, email, payment, or external side effect.")


if __name__ == "__main__":
    main()
