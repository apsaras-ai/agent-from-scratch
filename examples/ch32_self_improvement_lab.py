"""Chapter 32: an audit-gated prompt/memory/workflow improvement cycle.

This is an entirely synthetic, in-memory instructional simulation.
A host-approved flag below is a fixture, NOT authenticated authorization.
"""
from mini_agent.self_improvement import (
    AgentConfig, ImprovementSession, example_suite,
)


def main() -> None:
    session = ImprovementSession(example_suite())
    initial = session.evaluate(AgentConfig(), "dev")
    print("Chapter 32: constrained self-improvement without model weight changes")
    print("  Baseline prompt=shortcut, memory=off, workflow=direct")
    print(f"  Development baseline: {initial.passes}/{initial.total}")
    print("  Synthetic cost units are arbitrary, NOT USD or provider Usage.")

    search = session.search(max_rounds=6, max_proposals=40)
    for record in search.trace:
        candidate = record.proposal.config
        print(
            f"  round={record.round_index} "
            f"prompt={candidate.prompt} "
            f"memory={','.join(candidate.memory_rules) or 'none'} "
            f"workflow={candidate.workflow} "
            f"dev={record.dev_passes}/6 "
            f"cost={record.dev_cost:.3f} "
            f"gate={record.decision.reason}"
        )
    print()
    print(f"  Winner DEV: {search.winner_dev_passes}/6")
    print(f"  Proposals evaluated: {search.candidate_evaluations}")
    print("  Candidate cannot access audit cases during search.")
    print("  Active configuration before audit:", session.registry.active.digest()[:12])

    audit = session.sealed_audit()
    print(
        f"  One-shot synthetic audit: baseline={audit.baseline_passes}/6, "
        f"candidate={audit.candidate_passes}/6, "
        f"cost={audit.average_cost:.3f}, gate={audit.reason}"
    )
    print("  Successful audit does not by itself activate the candidate.")
    assert session.registry.active == AgentConfig()
    assert audit.approved_by_grader
    assert audit.candidate_passes == 6

    # Explicit trusted-host approval is simulated ONLY in this in-memory demo.
    activated = session.activate(
        audit, expected_revision=0, approved_by_trusted_caller=True,
    )
    print("  Simulated host-approved config activation: revision", activated.revision)
    assert session.registry.active == search.winner
    restored = session.registry.rollback(
        0, expected_revision=1, approved_by_trusted_caller=True,
    )
    print("  Simulated host-approved rollback: revision", restored.revision)
    assert session.registry.active == AgentConfig()
    print("  Final: baseline restored; no production release or external tools.")


if __name__ == "__main__":
    main()
