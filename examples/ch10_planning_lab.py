"""Chapter 10: task planning, failure-driven replan and independent verifier."""
from mini_agent.planning import PlanExecutor, PlanStep, ToyRepository


def replan(steps, failed_id, observation):
    if failed_id != "test" or observation.detail != "FAIL":
        return None
    return (
        PlanStep("inspect", "inspect"),
        PlanStep("patch", "patch", ("inspect",)),
        PlanStep("test", "test", ("patch",)),
    )


def main():
    env = ToyRepository()
    initial = (PlanStep("test", "test"),)
    result = PlanExecutor(env, replan=replan, max_actions=5).run(initial)
    print("Status:", result.status)
    print("Reason:", result.reason)
    print("Actions:", env.actions)
    print("Verified:", result.verification.passed if result.verification else None)

    fake = ToyRepository()
    claim = PlanExecutor(fake).run((PlanStep("claim", "claim_success"),))
    print("False claim status:", claim.status)
    print("False claim verified:", claim.verification.passed)


if __name__ == "__main__":
    main()
