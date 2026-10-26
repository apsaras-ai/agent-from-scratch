"""Chapter 26: multi-domain protocol, reproducibility, regression gates."""
import json
import unittest
from dataclasses import replace
from pathlib import Path
from decimal import Decimal

from mini_agent.agent_evaluation import EvalError, load_suite, RunTelemetry
from mini_agent.eval_fixture import KernelFixtureRunner
from mini_agent.benchmark_framework import (
    BenchmarkProtocol, RegressionGate, run_benchmark, gate_candidate,
    assert_reproducible,
)

PATH = Path(__file__).resolve().parents[1] / "evals/ch25_offline_suite.json"


def protocol(split="heldout", n=3):
    return BenchmarkProtocol(
        "ch25-fixture-suite", "v1", "fake-model-v1",
        "prompt-v1", "fixture-tools-v1", "reset-state-v1",
        "outcome-grader-v1", "inprocess-fixture-not-secure",
        42, split, n,
    )


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.suite=load_suite(PATH)
        self.p=protocol()
    def evaluate(self,variant="candidate",p=None):
        return run_benchmark(self.suite,p or self.p,variant,
                             KernelFixtureRunner(variant))

    def test_result_is_stable_under_repeat(self):
        a,b=self.evaluate(),self.evaluate()
        assert_reproducible(a,b)
        self.assertEqual(a.safe_summary(),b.safe_summary())
    def test_domain_micro_and_macro_metrics(self):
        r=self.evaluate()
        self.assertEqual((r.report.successes,len(r.report.trials)),(5,6))
        self.assertEqual(tuple(d.domain for d in r.domains),("browser","coding"))
        self.assertAlmostEqual(r.score,(2/3+1)/2)
        self.assertAlmostEqual(r.report.rate,5/6)
    def test_failure_taxonomy(self):
        r=self.evaluate()
        self.assertEqual(dict(r.failure_counts),{"outcome_mismatch":1})
        browser=r.domains[0]
        self.assertEqual((browser.outcome_failures,browser.policy_violations),(1,0))
    def test_baseline_candidate_regression_gate(self):
        a,b=self.evaluate("baseline"),self.evaluate()
        result=gate_candidate(a,b,RegressionGate(minimum_micro_rate=.8))
        self.assertEqual((result.action,result.paired_improvements,
                          result.paired_regressions),("approve",2,0))
    def test_regression_gate_blocks_reverse_comparison(self):
        a,b=self.evaluate(),self.evaluate("baseline")
        result=gate_candidate(a,b,RegressionGate())
        self.assertEqual(result.action,"block")
        self.assertEqual(result.reason,"task_regression")
    def test_cost_required_without_telemetry(self):
        class UnknownCost:
            def run(self,task,env,trial_index):
                return RunTelemetry("done",0,(),1,None,None)
        b=run_benchmark(self.suite,self.p,"unknown",UnknownCost())
        decision=gate_candidate(self.evaluate(),b,RegressionGate(require_cost_observed=True))
        self.assertEqual((decision.action,decision.reason),
                         ("insufficient_evidence","missing_cost"))
    def test_unsafe_attempt_cannot_be_offset_by_correct_state(self):
        class Unsafe:
            def run(self,task,env,trial_index):
                for r in task.allowed_writes:
                    env.invoke("write",r,"saved" if r=="draft" else "green")
                env.invoke("publish",next(iter(env.snapshot())))
                return RunTelemetry("completed",0,(),1,None,Decimal("0"))
        result=run_benchmark(self.suite,self.p,"unsafe",Unsafe())
        self.assertEqual(gate_candidate(self.evaluate(),result,RegressionGate()).reason,
                         "unsafe_tool_attempt")
    def test_protocol_fingerprint_changes_with_model_or_verifier(self):
        self.assertNotEqual(self.p.digest(),replace(self.p,model_version="new").digest())
        self.assertNotEqual(self.p.digest(),replace(self.p,verifier_version="new").digest())
        self.assertNotEqual(self.p.digest(),replace(self.p,seed=43).digest())
    def test_protocol_mismatch_rejected(self):
        a=self.evaluate()
        b=self.evaluate(p=replace(self.p,tool_version="v2"))
        with self.assertRaises(EvalError):
            gate_candidate(a,b,RegressionGate())
        with self.assertRaises(EvalError):
            assert_reproducible(a,b)
    def test_suite_version_mismatch(self):
        with self.assertRaises(EvalError):
            run_benchmark(self.suite,replace(self.p,benchmark_version="v99"),
                          "candidate",KernelFixtureRunner("candidate"))
    def test_other_splits_have_domain_slices(self):
        regression=self.evaluate(p=protocol("regression",2))
        self.assertEqual({d.domain for d in regression.domains},{"coding","security"})
        self.assertEqual(regression.report.successes,4)
        dev=self.evaluate(p=protocol("dev",2))
        self.assertEqual({d.domain for d in dev.domains},{"research","tool"})
    def test_invalid_configuration_rejected(self):
        for value in (True,-1,2**64):
            with self.subTest(value=value),self.assertRaises(EvalError):
                replace(self.p,seed=value)
        with self.assertRaises(EvalError):
            replace(self.p,split="all")
        with self.assertRaises(EvalError):
            replace(self.p,trials_per_case=0)
        with self.assertRaises(EvalError):
            RegressionGate(max_allowed_regressions=-1)
    def test_privacy_of_safe_summary(self):
        r=self.evaluate()
        encoded=json.dumps(r.safe_summary())
        self.assertNotIn("Save draft",encoded)
        self.assertNotIn("I completed",encoded)
        self.assertNotIn("Do not publish",encoded)
        self.assertEqual(len(r.protocol_digest),64)
    def test_grader_failure_is_preserved_in_counts(self):
        class Broken:
            def run(self,task,env,trial_index):
                raise RuntimeError("PRIVATE_SECRET")
        r=run_benchmark(self.suite,self.p,"broken",Broken())
        self.assertEqual(dict(r.failure_counts),{"runner_failure":6})
        self.assertNotIn("PRIVATE_SECRET",str(r.safe_summary()))
    def test_duplicate_suite_changes_are_not_comparable(self):
        changed=replace(self.suite,version="v2")
        p=replace(self.p,benchmark_version="v2")
        result=run_benchmark(changed,p,"candidate",
                             KernelFixtureRunner("candidate"))
        with self.assertRaises(EvalError):
            assert_reproducible(self.evaluate(),result)


if __name__=="__main__":
    unittest.main()
