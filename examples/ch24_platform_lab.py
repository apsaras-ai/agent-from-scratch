"""Chapter 24: offline Agent Platform preflight and progressive rollout.

No Kubernetes, Registry, Argo Rollouts, DNS, real metrics, users, cloud
resources or external tool calls. All resources/metrics are synthetic.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile

from mini_agent.platform_deployment import (
    CanaryPolicy, ReleaseLedger, preflight, reference_kubernetes_objects,
)
from mini_agent.platform_fixture import (
    healthy_metrics, sample_platform, sample_release,
)


def main() -> None:
    platform = sample_platform()
    findings = preflight(platform)
    print("Static preflight findings:", [x.code for x in findings])
    assert not findings

    manifests = reference_kubernetes_objects(platform)
    deployments = [obj for obj in manifests if obj["kind"] == "Deployment"]
    print("Reference Kubernetes Deployment objects:", len(deployments))
    print("Rendered worker RuntimeClass:",
          next(x for x in deployments
               if x["metadata"]["name"] == "sandbox-worker")["spec"]
          ["template"]["spec"]["runtimeClassName"])
    print("No manifest was applied to any cluster.")

    stable, canary = healthy_metrics()
    with tempfile.TemporaryDirectory() as tmp:
        ledger = ReleaseLedger(Path(tmp) / "releases.sqlite")
        state = ledger.initialize(sample_release(), CanaryPolicy())
        print("Rollout initial desired weight:", state.weight, "%")

        # Missing telemetry is not permission to promote.
        _, state = ledger.decide(
            state.release_id, expected_generation=state.generation,
            baseline=None, candidate=canary,
        )
        print("Missing evidence:", state.status, "weight:", state.weight)

        while state.status not in ("promoted", "aborted"):
            judgment, state = ledger.decide(
                state.release_id, expected_generation=state.generation,
                baseline=stable,
                candidate=replace(
                    canary, observed_weight=state.weight,
                    window_id="candidate-" + str(state.weight)
                              + "-" + str(state.generation),
                ),
            )
            print("  decision:", judgment.action,
                  "status:", state.status, "weight:", state.weight)
        assert state.status == "promoted"
        print("Durable decision records:", len(ledger.events(state.release_id)))
        print("This only records desired release decisions, not applied traffic.")

        # A second release with a security signal aborts at the canary.
        second = replace(sample_release(), release_id="release-002")
        halted = ledger.initialize(second, CanaryPolicy())
        _, halted = ledger.decide(
            second.release_id, expected_generation=halted.generation,
            baseline=stable,
            candidate=replace(
                canary, security_incidents=1, release_id=second.release_id
            ),
        )
        print("Security incident:", halted.status,
              "planned traffic weight:", halted.weight)
        assert halted.status == "aborted"

    print("All images, costs and latency numbers are synthetic fixtures.")


if __name__ == "__main__":
    main()
