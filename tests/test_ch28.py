"""Ch28 EDD: pre-registration, ablation, hard gates and safe intake."""
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from mini_agent.agent_evaluation import EvalError, load_suite
from mini_agent.benchmark_framework import (
    BenchmarkProtocol,CandidateConfig,run_benchmark,
)
from mini_agent.eval_fixture import KernelFixtureRunner
from mini_agent.evaluation_driven_development import (
    ChangeHypothesis,decide_edd,triage,propose_regression_case,
)

PATH=Path(__file__).resolve().parents[1]/"evals/ch25_offline_suite.json"


class EDDTests(unittest.TestCase):
    def setUp(self):
        suite=load_suite(PATH)
        self.protocol=BenchmarkProtocol(
            suite.suite_id,suite.version,"legacy-model","legacy-prompt",
            "tool-v1","environment-v1","verifier-v1","inprocess",
            42,"heldout",3,
        )
        self.base_cfg=CandidateConfig("baseline-model","baseline-prompt")
        self.good_cfg=CandidateConfig("candidate-model","improved-prompt")
        self.ablate_cfg=CandidateConfig("candidate-model","without-change")
        self.baseline=run_benchmark(suite,self.protocol,"baseline",
            KernelFixtureRunner("baseline"),candidate_config=self.base_cfg)
        self.good=run_benchmark(suite,self.protocol,"candidate",
            KernelFixtureRunner("candidate"),candidate_config=self.good_cfg)
        self.ablate=run_benchmark(suite,self.protocol,"ablated",
            KernelFixtureRunner("baseline"),candidate_config=self.ablate_cfg)
        self.hyp=ChangeHypothesis("hypothesis-1","outcome_mismatch",
                                  "prompt",self.good_cfg,max_cost_ratio=Decimal("2"))

    def test_triage_uses_failure_codes_only(self):
        self.assertEqual([(x.failure_code,x.count) for x in triage(self.baseline)],
                         [("outcome_mismatch",3)])

    def test_accepted_hypothesis_needs_ablation(self):
        x=decide_edd(self.baseline,self.good,self.hyp,ablated=self.ablate)
        self.assertEqual((x.action,x.reason),("adopt","all_pre_registered_gates_satisfied"))
        self.assertEqual((x.improvements,x.regressions),(2,0))
        self.assertEqual(x.ablated_rate,.5)
        self.assertEqual(x.candidate_rate,5/6)

    def test_missing_ablation_is_not_approval(self):
        x=decide_edd(self.baseline,self.good,self.hyp,ablated=None)
        self.assertEqual((x.action,x.reason),("inconclusive","missing_ablation"))

    def test_ablation_matching_candidate_does_not_prove_change(self):
        x=decide_edd(self.baseline,self.good,self.hyp,ablated=self.good)
        self.assertEqual((x.action,x.reason),("inconclusive","ablation_does_not_support_effect"))

    def test_cost_guardrail_is_effective(self):
        hyp=replace(self.hyp,max_cost_ratio=Decimal("1"))
        x=decide_edd(self.baseline,self.good,hyp,ablated=self.ablate)
        # Candidate toy fixture has cheaper cached tokens, so it still passes.
        self.assertEqual(x.action,"adopt")
        self.assertLess(x.cost_ratio,1)

    def test_wrong_candidate_configuration_rejected(self):
        with self.assertRaises(EvalError):
            decide_edd(self.baseline,self.good,replace(
                self.hyp,candidate_config=self.base_cfg),ablated=self.ablate)

    def test_wrong_protocol_ablation_rejected(self):
        other=replace(self.ablate,protocol_digest="incorrect")
        with self.assertRaises(EvalError):
            decide_edd(self.baseline,self.good,self.hyp,ablated=other)

    def test_reverse_comparison_is_rejected_by_quality(self):
        hyp=replace(self.hyp,candidate_config=self.base_cfg)
        x=decide_edd(self.good,self.baseline,hyp,ablated=self.ablate)
        self.assertEqual(x.action,"reject")

    def test_safety_violation_blocks_regardless_of_quality(self):
        from dataclasses import replace as r
        trial=self.good.report.trials[0]
        fake=r(self.good,report=r(self.good.report,trials=(
            r(trial,policy_ok=False,passed=False,failure_code="policy_violation"),
            *self.good.report.trials[1:])))
        x=decide_edd(self.baseline,fake,self.hyp,ablated=self.ablate)
        self.assertEqual((x.action,x.reason),("reject","candidate_policy_violation"))

    def test_missing_cost_is_inconclusive(self):
        from dataclasses import replace as r
        trial=self.good.report.trials[0]
        fake=r(self.good,report=r(self.good.report,trials=(
            r(trial,cost_usd=None),*self.good.report.trials[1:])))
        x=decide_edd(self.baseline,fake,self.hyp,ablated=self.ablate)
        self.assertEqual((x.action,x.reason),("inconclusive","missing_cost_evidence"))

    def test_hypothesis_values_fail_closed(self):
        with self.assertRaises(EvalError):
            replace(self.hyp,max_cost_ratio=Decimal("NaN"))
        with self.assertRaises(EvalError):
            replace(self.hyp,intervention="unsafe-shell")
        with self.assertRaises(EvalError):
            replace(self.hyp,max_regressions=True)

    def test_regression_proposal_is_review_only(self):
        a=propose_regression_case("outcome_mismatch","browser","synthetic-001")
        self.assertEqual(a.proposed_split,"regression")
        self.assertEqual(len(a.source_digest),64)
        self.assertNotIn("synthetic-001",repr(a))
        with self.assertRaises(EvalError):
            propose_regression_case("outcome_mismatch","browser","private user email")

if __name__=="__main__":
    unittest.main()
