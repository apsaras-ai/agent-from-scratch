"""Chapter 25: versioned offline Agent Evaluation Harness, no paid models.

The "candidate" uses the REAL Chapter-4 AgentKernel with a deterministic
fake Model. Scores are for six tiny synthetic tasks only and do not indicate
general capability or security. All telemetry prices/latencies are fixtures.
"""
from pathlib import Path

from mini_agent.agent_evaluation import EvalHarness, compare_paired, load_suite
from mini_agent.eval_fixture import KernelFixtureRunner, naive_advisory_judge


def main():
    path=Path(__file__).resolve().parents[1]/"evals"/"ch25_offline_suite.json"
    suite=load_suite(path)
    harness=EvalHarness(suite,advisory=naive_advisory_judge)
    baseline=harness.evaluate("baseline",KernelFixtureRunner("baseline"),
                              split="heldout",trials_per_case=3)
    candidate=harness.evaluate("candidate",KernelFixtureRunner("candidate"),
                               split="heldout",trials_per_case=3)
    for report in (baseline,candidate):
        print(
            f"{report.candidate}: {report.successes}/{len(report.trials)} trials,"
            f" rate={report.rate:.3f},"
            f" observed pass@3={report.pass_at_k(3):.2f},"
            f" observed pass^3={report.pass_all_k(3):.2f},"
            f" cost_per_verified_task={report.cost_per_verified_task_usd}"
        )
        print("  first-trial Wilson 95%:",tuple(
            round(x,3) for x in report.first_trial_wilson_95()
        ))
        print("  failures:", [
            (x.case_id,x.trial_index,x.failure_code)
            for x in report.trials if not x.passed
        ])
    compare=compare_paired(baseline,candidate)
    print("Paired trial improvements:",compare.improved,
          "regressions:",compare.regressed,
          "exact first-trial McNemar p:",compare.mcnemar_exact_p)
    false_positive=next(
        t for t in baseline.trials if t.case_id=="browser-draft"
    )
    print("Naive advisory judge says success:",
          false_positive.advisory_score,
          "but environment outcome passed:",false_positive.passed)
    assert candidate.successes == 5 and baseline.successes == 3
    assert candidate.pass_at_k(3)==1 and candidate.pass_all_k(3)==.5
    print("Dataset SHA-256:",suite.digest()[:16])
    print("No external tools, model APIs, secrets or user data were accessed.")


if __name__=="__main__":
    main()
