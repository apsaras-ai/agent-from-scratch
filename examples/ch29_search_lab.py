"""Chapter 29: Best-of-N, self-consistency, beam and MCTS budget lab.

The tasks are synthetic arithmetic programs; a host-owned verifier replays
selected programs, and every candidate/transition/verification is bounded.
All costs are teaching units, NOT actual token billing.
"""
from mini_agent.inference_search import (
    SearchBudget, SynthesisTask, compare_strategies,
    derive_search_seed, run_search,
)


def main() -> None:
    tasks=(
        SynthesisTask("warmup",1,7,4),
        SynthesisTask("search-17",2,17,5),
        SynthesisTask("search-19",1,19,5),
        SynthesisTask("search-23",2,23,6),
        SynthesisTask("search-27",3,27,6),
    )
    budget=SearchBudget(proposals=130,transitions=160,
                        verifications=40,candidates=36)
    print("Hard per-task ceilings:",budget.proposals,budget.transitions,
          budget.verifications,budget.candidates)
    rows=compare_strategies(tasks,budget=budget,seed=29)
    for row in rows:
        print(
            row.method,"verified",f"{row.verified}/{row.tasks}",
            "rate",round(row.success_rate,3),
            "proposal_calls",row.total_proposals,
            "host_verifier_calls",row.total_verifications,
            "synthetic_cost",row.total_synthetic_cost,
        )
    chosen=run_search(tasks[0],"mcts",budget=budget,
                      seed=derive_search_seed(29,tasks[0].task_id,0))
    print("Example MCTS:",chosen.reason,"program:",chosen.program,
          "verified:",chosen.success)
    assert all(0<=r.verified<=r.tasks for r in rows)
    assert all(r.total_proposals <= r.tasks*budget.proposals for r in rows)
    print("No real LLM, arbitrary code execution or external tool effects.")


if __name__=="__main__":
    main()
