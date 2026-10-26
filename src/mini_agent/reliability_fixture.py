"""Chapter 21: synthetic external ledger with deterministic failpoints.

The provider is an IN-MEMORY fixture, not email/payment/network. It pretends
to have its own durable authority when the scheduler SQLite file restarts.
"not_applied" is authoritative ONLY because this fixture is synchronous.
"""
from __future__ import annotations

from dataclasses import dataclass

from .reliability_outbox import BackendEvidence, DispatchLease, Intent


class SyntheticProvider:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str, str], str] = {}
        self.attempted_requests = 0
        self.committed_effects = 0
        self.force_unknown_inspection = False

    @staticmethod
    def _key(tenant: str, run: str, request_key: str):
        return tenant, run, request_key

    def apply(self, lease: DispatchLease) -> str:
        """Simulate a provider-side effect; no network or real data."""
        self.attempted_requests += 1
        key = self._key(lease.tenant_id, lease.run_id, lease.request_key)
        if lease.mode.value == "idempotent" and key in self.entries:
            return self.entries[key]
        self.committed_effects += 1
        receipt = "synthetic-receipt-" + str(self.committed_effects)
        self.entries[key] = receipt
        return receipt

    def inspect(self, spec: Intent) -> BackendEvidence:
        if self.force_unknown_inspection:
            return BackendEvidence("unknown")
        receipt = self.entries.get(self._key(
            spec.tenant_id, spec.run_id, spec.request_key
        ))
        if receipt:
            return BackendEvidence("committed", receipt)
        return BackendEvidence("not_applied")
