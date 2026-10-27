"""Chapter 28: offline EDD hypothesis -> ablation -> gate."""
from pathlib import Path
from decimal import Decimal
from mini_agent.agent_evaluation import load_suite
from mini_agent.benchmark_framework import BenchmarkProtocol,CandidateConfig,run_benchmark
from mini_agent.eval_fixture import KernelFixtureRunner
from mini_agent.evaluation_driven_development import (
    ChangeHypothesis,decide_edd,triage,propose_regression_case,
)


def main():
    suite=load_suite(Path(__file__).resolve().parents[1]/
                     "evals/ch25_offline_suite.json")
    protocol=BenchmarkProtocol(
        suite.suite_id,suite.version,"legacy-model","legacy-prompt",
        "fixture-tools","fixture-env","verifier","inprocess",42,"heldout",3)
    baseline_cfg=CandidateConfig("toy-model-v1","old-prompt")
    candidate_cfg=CandidateConfig("toy-model-v1","new-prompt")
    ablated_cfg=CandidateConfig("toy-model-v1","new-minus-intervention")
    baseline=run_benchmark(suite,protocol,"baseline",KernelFixtureRunner("baseline"),
                           candidate_config=baseline_cfg)
    candidate=run_benchmark(suite,protocol,"candidate",KernelFixtureRunner("candidate"),
                            candidate_config=candidate_cfg)
    ablated=run_benchmark(suite,protocol,"ablated",KernelFixtureRunner("baseline"),
                          candidate_config=ablated_cfg)
    hypothesis=ChangeHypothesis("h1","outcome_mismatch","prompt",candidate_cfg,
                                 max_cost_ratio=Decimal("2"))
    print("Baseline failures:",[(x.failure_code,x.count) for x in triage(baseline)])
    result=decide_edd(baseline,candidate,hypothesis,ablated=ablated)
    print("Decision:",result.action,"reason:",result.reason)
    print("Baseline/candidate/ablated:",result.baseline_rate,
          result.candidate_rate,result.ablated_rate)
    print("Paired improvements/regressions:",result.improvements,result.regressions)
    proposal=propose_regression_case("outcome_mismatch","browser","synthetic-case-001")
    print("Regression intake:",proposal.proposed_split,"review required")
    assert result.action=="adopt"
    print("Synthetic fixture decision ONLY; no real models or deployment.")


if __name__=="__main__":
    main()
