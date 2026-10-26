"""Chapter 25: independent outcomes, policy checks, cost coverage and statistics.

All trials run synthetic tools and a deterministic fake model via REAL
AgentKernel. No real LLM, browser, shell, network, or secrets.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from mini_agent.agent_evaluation import (
    AdvisoryJudgment, EvalCase, EvalError, EvalHarness, EvalReport, EvalSuite,
    FixtureEnvironment, PublicTask, RunTelemetry, ToolEvent,
    compare_paired, load_suite, wilson_interval,
)
from mini_agent.cost_optimization import RateCard, TokenUsage
from mini_agent.eval_fixture import KernelFixtureRunner, naive_advisory_judge


SUITE = Path(__file__).resolve().parents[1] / "evals" / "ch25_offline_suite.json"


class DatasetTests(unittest.TestCase):
    def test_read_versioned_synthetic_fixture(self):
        s = load_suite(SUITE)
        self.assertEqual((s.suite_id,s.version), ("ch25-fixture-suite","v1"))
        self.assertEqual(len(s.cases),6)
        self.assertEqual(len(s.cases_for("dev")),2)
        self.assertEqual(len(s.cases_for("regression")),2)
        self.assertEqual(len(s.cases_for("heldout")),2)
        self.assertEqual(len(s.digest()),64)

    def test_digest_stable_and_sensitive_to_ground_truth(self):
        s = load_suite(SUITE)
        self.assertEqual(s.digest(),load_suite(SUITE).digest())
        changed = replace(s.cases[0],expected_state={"brief":"wrong"})
        other = replace(s,cases=(changed,*s.cases[1:]))
        self.assertNotEqual(s.digest(),other.digest())
        self.assertNotEqual(s.digest(),replace(s,version="v2").digest())

    def test_duplicate_case_id_fails_closed(self):
        s = load_suite(SUITE)
        with self.assertRaises(EvalError):
            replace(s,cases=(s.cases[0],s.cases[0]))

    def test_case_validation_and_private_expected_keys(self):
        s=load_suite(SUITE)
        c=s.cases[0]
        self.assertNotIn("expected_state",
                         PublicTask(c.case_id,c.category,c.instruction,
                                    c.allowed_writes).__dict__)
        with self.assertRaises(EvalError):
            replace(c,expected_state={"missing":"a"})
        with self.assertRaises(EvalError):
            replace(c,allowed_writes=("unapproved",))
        with self.assertRaises(EvalError):
            replace(c,forbidden_actions=("arbitrary_shell",))
        with self.assertRaises(EvalError):
            replace(c,category="unknown")
        with self.assertRaises(EvalError):
            replace(c,split="test")
        with self.assertRaises(EvalError):
            replace(c,case_id="../escape")
        with self.assertRaises(EvalError):
            replace(c,initial_state={"brief":42})

    def test_loader_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"suite.json"
            p.write_text('{"suite_id":"a","suite_id":"b","version":"v1","cases":[]}')
            with self.assertRaises(EvalError):
                load_suite(p)

    def test_loader_rejects_nonfinite_json(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"suite.json"
            p.write_text('{"suite_id":"a","version":"v1","cases":NaN}')
            with self.assertRaises(EvalError):
                load_suite(p)

    def test_loader_rejects_missing_fields(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"suite.json"
            data=json.loads(SUITE.read_text(encoding="utf-8"))
            del data["cases"][0]["expected_state"]
            p.write_text(json.dumps(data),encoding="utf-8")
            with self.assertRaises(EvalError):
                load_suite(p)

    def test_loader_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"symlink.json"
            p.symlink_to(SUITE)
            with self.assertRaises(EvalError):
                load_suite(p)

    def test_empty_split_and_unknown_split_denied(self):
        s=load_suite(SUITE)
        with self.assertRaises(EvalError):
            s.cases_for("production")
        modified=replace(s,cases=tuple(c for c in s.cases if c.split=="dev"))
        with self.assertRaises(EvalError):
            modified.cases_for("heldout")


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.case=load_suite(SUITE).cases[0]
        self.env=FixtureEnvironment(self.case,max_actions=2)

    def test_actual_state_changes_only_via_approved_write(self):
        self.assertEqual(self.env.invoke("read","brief"),"empty")
        self.assertEqual(self.env.invoke("write","brief","grounded"),"written")
        self.assertEqual(self.env.snapshot(),{"brief":"grounded"})
        self.assertEqual([e.outcome for e in self.env.events()],
                         ["executed","executed"])

    def test_unknown_tool_and_wrong_resource_denied(self):
        e=FixtureEnvironment(self.case)
        self.assertIsNone(e.invoke("shell","brief"))
        self.assertIsNone(e.invoke("write","other","bad"))
        self.assertEqual(e.snapshot(),{"brief":"empty"})
        self.assertEqual([x.outcome for x in e.events()],
                         ["unknown_tool","resource_denied"])

    def test_forbidden_publish_is_not_executed(self):
        e=FixtureEnvironment(self.case)
        self.assertIsNone(e.invoke("publish","brief","leak"))
        self.assertEqual(e.snapshot(),{"brief":"empty"})
        self.assertEqual(e.events()[0].outcome,"policy_denied")

    def test_action_budget_exceeded_is_recorded(self):
        self.env.invoke("read","brief")
        self.env.invoke("read","brief")
        self.env.invoke("read","brief")
        self.assertEqual(self.env.events()[-1].outcome,"action_budget_exceeded")

    def test_state_snapshot_cannot_modify_real_env(self):
        snap=self.env.snapshot()
        snap["brief"]="forged"
        self.assertEqual(self.env.snapshot(),{"brief":"empty"})

    def test_environment_not_reused_across_trials(self):
        case=self.case
        a=FixtureEnvironment(case)
        b=FixtureEnvironment(case)
        a.invoke("write","brief","grounded")
        self.assertEqual(b.snapshot(),{"brief":"empty"})


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.suite=load_suite(SUITE)
        self.harness=EvalHarness(self.suite,advisory=naive_advisory_judge)

    def baseline(self,split="heldout",n=3):
        return self.harness.evaluate(
            "baseline",KernelFixtureRunner("baseline"),
            split=split,trials_per_case=n,
        )

    def candidate(self,split="heldout",n=3):
        return self.harness.evaluate(
            "candidate",KernelFixtureRunner("candidate"),
            split=split,trials_per_case=n,
        )

    def test_real_agent_kernel_fake_model_traces_and_state(self):
        report=self.candidate(split="dev",n=1)
        self.assertEqual(report.successes,2)
        self.assertEqual(report.rate,1)
        self.assertTrue(all(t.outcome_ok and t.policy_ok for t in report.trials))
        self.assertTrue(all(t.model_calls==2 and t.tool_calls==1
                            for t in report.trials))

    def test_heldout_repeated_trials_and_reliability_diverge(self):
        report=self.candidate()
        self.assertEqual(len(report.trials),6)
        self.assertEqual(report.successes,5)
        self.assertAlmostEqual(report.rate,5/6)
        self.assertEqual(report.pass_at_k(3),1)
        self.assertEqual(report.pass_all_k(3),.5)
        self.assertEqual(report.pass_at_k(1),1)
        self.assertEqual(report.pass_all_k(1),1)

    def test_baseline_claims_done_without_real_world_state(self):
        report=self.baseline()
        failed=[r for r in report.trials if r.case_id=="browser-draft"]
        self.assertEqual(len(failed),3)
        self.assertTrue(all(not r.outcome_ok and not r.passed for r in failed))
        self.assertTrue(all(r.failure_code=="outcome_mismatch" for r in failed))
        self.assertTrue(all(r.advisory_score==1.0 for r in failed))

    def test_candidate_has_paired_improvement_not_proof_of_real_llm_gain(self):
        comparison=compare_paired(self.baseline(),self.candidate())
        self.assertEqual(comparison.count,6)
        self.assertEqual((comparison.improved,comparison.regressed,
                          comparison.tied),(2,0,4))
        self.assertAlmostEqual(comparison.delta_success_rate,2/6)
        # McNemar uses only independent first trials, not correlated repeats.
        self.assertAlmostEqual(comparison.mcnemar_exact_p,1.0)

    def test_comparison_same_data_required(self):
        a=self.baseline(n=2)
        b=self.candidate(n=3)
        with self.assertRaises(EvalError):
            compare_paired(a,b)
        with self.assertRaises(EvalError):
            compare_paired(a,self.candidate(split="dev",n=2))

    def test_error_rate_is_not_hidden_by_excluding_failed_trials(self):
        class Broken:
            def run(self,task,env,trial_index):
                raise RuntimeError("SECRET_DO_NOT_DISPLAY")
        r=self.harness.evaluate("broken",Broken(),split="dev",trials_per_case=2)
        self.assertEqual(len(r.trials),4)
        self.assertEqual(r.rate,0)
        self.assertTrue(all(t.failure_code=="runner_failure" for t in r.trials))
        self.assertNotIn("SECRET_DO_NOT_DISPLAY",repr(r))

    def test_verifier_success_separate_from_security(self):
        class Malicious:
            def run(self,task,env,trial_index):
                if task.case_id=="research-brief":
                    env.invoke("write","brief","grounded")
                    env.invoke("publish","brief","unauthorized")
                return RunTelemetry("done",0,(),1,None,Decimal("0"))
        r=self.harness.evaluate("malicious",Malicious(),split="dev")
        row=next(t for t in r.trials if t.case_id=="research-brief")
        self.assertTrue(row.outcome_ok)
        self.assertFalse(row.policy_ok)
        self.assertFalse(row.passed)
        self.assertEqual(row.failure_code,"policy_violation")

    def test_runner_cannot_change_expected_state_by_claiming_success(self):
        class ClaimsDone:
            def run(self,task,env,trial):
                return RunTelemetry('{"verified":true}',0,(),2,None,Decimal("0"))
        report=self.harness.evaluate("claimsdone",ClaimsDone(),split="dev")
        self.assertEqual(report.successes,0)
        self.assertTrue(all(r.outcome_ok is False for r in report.trials))

    def test_cost_unknown_is_not_zero(self):
        class UnknownCost:
            def run(self,task,env,index):
                env.invoke("write",task.allowed_writes[0],"grounded")
                return RunTelemetry("done",1,(),10,None,None)
        # This candidate handles first dev case but not the second.
        r=self.harness.evaluate("unknowncost",UnknownCost(),split="dev")
        self.assertIsNone(r.total_cost_usd)
        self.assertIsNone(r.cost_per_verified_task_usd)

    def test_cost_per_verified_task_charges_failures(self):
        report=self.candidate(n=3)
        self.assertIsNotNone(report.total_cost_usd)
        self.assertIsNotNone(report.cost_per_verified_task_usd)
        self.assertEqual(report.cost_per_verified_task_usd,
                         report.total_cost_usd/Decimal(report.successes))

    def test_advisory_failure_does_not_override_deterministic_grader(self):
        def exploding(task, text):
            raise RuntimeError("grader unavailable")
        report=EvalHarness(self.suite,advisory=exploding).evaluate(
            "candidate",KernelFixtureRunner("candidate"),
            split="dev",trials_per_case=1,
        )
        self.assertEqual(report.successes,2)
        self.assertTrue(all(t.advisory_score is None for t in report.trials))

    def test_advisory_score_invalid_is_ignored_not_task_failure(self):
        def invalid(task,text):
            return AdvisoryJudgment(float("nan"),"fake")
        report=EvalHarness(self.suite,advisory=invalid).evaluate(
            "candidate",KernelFixtureRunner("candidate"),split="dev",
        )
        self.assertEqual(report.successes,2)
        self.assertTrue(all(x.advisory_score is None for x in report.trials))

    def test_report_contains_no_prompts_or_tool_arguments(self):
        report=self.candidate(split="regression",n=1)
        s=repr(report)
        self.assertNotIn("Do not publish.",s)
        self.assertNotIn("private",s)
        self.assertNotIn("I completed",s)
        self.assertEqual(report.successes,2)

    def test_budget_of_k_must_be_recorded(self):
        r=self.candidate(n=3)
        with self.assertRaises(EvalError):
            r.pass_at_k(4)
        with self.assertRaises(EvalError):
            r.pass_all_k(0)

    def test_wilson_is_first_trial_across_distinct_tasks(self):
        r=self.candidate()
        low,high=r.first_trial_wilson_95()
        self.assertLess(low,1.0)
        self.assertEqual(high,1.0)
        self.assertGreater(low,0)

    def test_missing_runner_and_invalid_trial_count_rejected(self):
        with self.assertRaises(EvalError):
            self.harness.evaluate("candidate",object(),split="dev")
        with self.assertRaises(EvalError):
            self.harness.evaluate("candidate",KernelFixtureRunner(),split="dev",
                                  trials_per_case=0)

    def test_evaluation_dataset_digest_bound_to_report(self):
        r=self.baseline(n=1)
        self.assertEqual(r.suite_digest,self.suite.digest())
        self.assertEqual(r.suite_version,"v1")


class MetricsTests(unittest.TestCase):
    def test_wilson_all_success_and_no_success(self):
        a,b=wilson_interval(0,10)
        self.assertEqual(a,0.0)
        self.assertGreater(b,0)
        a,b=wilson_interval(10,10)
        self.assertGreater(a,0)
        self.assertEqual(b,1.0)

    def test_wilson_half_success(self):
        a,b=wilson_interval(5,10)
        self.assertAlmostEqual(a,0.23659,places=4)
        self.assertAlmostEqual(b,0.76341,places=4)

    def test_wilson_invalid_parameters(self):
        for args in ((0,0),(11,10),(-1,10),(True,10)):
            with self.subTest(args=args),self.assertRaises(EvalError):
                wilson_interval(*args)

    def test_usage_unknown_price_is_unknown_not_zero(self):
        t=RunTelemetry("done",1,(TokenUsage(100,20),),10,None,Decimal("0"))
        self.assertIsNone(t.total_cost())
        no_calls=RunTelemetry("done",0,(),1,None,Decimal("0"))
        self.assertEqual(no_calls.total_cost(),Decimal("0"))

    def test_usage_explicit_rate_price(self):
        rate=RateCard(Decimal("2"),Decimal("8"),Decimal(".5"))
        t=RunTelemetry("done",1,(TokenUsage(100,20,80),),
                       10,rate,Decimal(".001"))
        self.assertEqual(t.total_cost(),Decimal(".00124"))

    def test_partial_usage_unknown(self):
        rate=RateCard(Decimal("2"),Decimal("8"),Decimal(".5"))
        self.assertIsNone(RunTelemetry("done",2,(TokenUsage(1,1),),
                                       1,rate,Decimal("0")).total_cost())

    def test_telemetry_input_validation(self):
        for kwargs in (
            {"model_calls":True},
            {"model_calls":0,"usages":(TokenUsage(1,1),)},
            {"latency_ms":float("nan")},
            {"external_cost_usd":Decimal("-1")},
        ):
            with self.subTest(kwargs=kwargs),self.assertRaises(EvalError):
                RunTelemetry("done",**{
                    "model_calls":1,"usages":(TokenUsage(1,1),),
                    "latency_ms":1,"rate":None,
                    "external_cost_usd":Decimal("0"),**kwargs,
                })

    def test_advisory_score_validation(self):
        for score in (float("nan"),float("inf"),-0.01,1.01):
            with self.assertRaises(EvalError):
                AdvisoryJudgment(score,"reason")

if __name__=="__main__":
    unittest.main()
