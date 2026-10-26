"""Chapter 26: reproducible synthetic multi-domain benchmark and gate."""
import json
from pathlib import Path
from mini_agent.agent_evaluation import load_suite
from mini_agent.eval_fixture import KernelFixtureRunner
from mini_agent.benchmark_framework import (
    BenchmarkProtocol, RegressionGate, run_benchmark,
    gate_candidate, assert_reproducible,
)


def main():
    suite=load_suite(Path(__file__).resolve().parents[1]/
                     "evals/ch25_offline_suite.json")
    config=BenchmarkProtocol(
        suite.suite_id,suite.version,"fake-model-v1","prompt-v1",
        "toy-tools-v1","isolated-fixture-v1","outcome-grader-v1",
        "inprocess-not-a-sandbox",42,"heldout",3,
    )
    baseline=run_benchmark(suite,config,"baseline",
                           KernelFixtureRunner("baseline"))
    candidate=run_benchmark(suite,config,"candidate",
                            KernelFixtureRunner("candidate"))
    repeat=run_benchmark(suite,config,"candidate",
                         KernelFixtureRunner("candidate"))
    assert_reproducible(candidate,repeat)
    gate=gate_candidate(baseline,candidate,
                        RegressionGate(minimum_micro_rate=.8))
    print(json.dumps({
        "baseline":baseline.safe_summary(),
        "candidate":candidate.safe_summary(),
        "gate":gate.__dict__,
    },ensure_ascii=False,indent=2))
    assert gate.action=="approve"
    assert candidate.report.successes==5
    print("Synthetic offline fixture only, no real benchmark or model calls.")


if __name__=="__main__":
    main()
