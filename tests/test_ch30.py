"""Ch30: trusted process/outcome verification, reward audit and feedback fitting.

The entire lab uses a safe symbolic arithmetic DSL. Tests cannot modify
files or network via candidate actions; no real LLM weights are trained.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest

from mini_agent.inference_search import SynthesisTask
from mini_agent.feedback_learning import (
    FeedbackCase, FeedbackError, FeedbackRecord, FeedbackSuite,
    TabularFeedbackPolicy, TrustedArithmeticVerifier, _probability,
    audit_reward_model, biased_length_reward, collect_train_feedback,
    compare_proxy_and_host, evaluate_one_step_policy,
    fit_tabular_feedback_policy, load_feedback_suite,
)

DATASET=Path(__file__).resolve().parents[1]/"evals"/"ch30_feedback_suite.json"


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.task=SynthesisTask("two-step",1,7,4)
        self.oracle=TrustedArithmeticVerifier()

    def test_valid_program_has_independent_outcome(self):
        report=self.oracle.verify(self.task,("add3","add3"),claimed_final=7)
        self.assertTrue(report.process_ok)
        self.assertTrue(report.outcome_ok)
        self.assertEqual(report.final_state,7)
        self.assertEqual(report.step_labels,(True,True))
        self.assertIsNone(report.first_error_step)

    def test_valid_process_but_wrong_outcome(self):
        r=self.oracle.verify(self.task,("add1","add1"))
        self.assertTrue(r.process_ok)
        self.assertFalse(r.outcome_ok)
        self.assertEqual(r.final_state,3)
        self.assertEqual(r.reason,"target_not_reached")

    def test_claimed_state_cannot_replace_replay(self):
        r=self.oracle.verify(self.task,("add1",),claimed_final=7)
        self.assertFalse(r.outcome_ok)
        self.assertEqual(r.final_state,2)
        self.assertEqual(r.step_labels,(True,))
        self.assertNotEqual(r.reason,"verified")

    def test_first_bad_step_is_located_without_executing_later(self):
        r=self.oracle.verify(self.task,("add3","publish","add3"))
        self.assertFalse(r.process_ok)
        self.assertFalse(r.outcome_ok)
        self.assertEqual(r.first_error_step,2)
        self.assertEqual(r.step_labels,(True,False))
        self.assertEqual(r.final_state,4)

    def test_invalid_out_of_range_transition_detected(self):
        task=SynthesisTask("bound",0,2,3,max_abs_value=5)
        r=self.oracle.verify(task,("sub1",))
        self.assertEqual(r.first_error_step,1)
        self.assertFalse(r.process_ok)
        self.assertEqual(r.reason,"invalid_or_out_of_range_transition")

    def test_steps_over_budget_fail(self):
        task=replace(self.task,max_steps=1)
        r=self.oracle.verify(task,("add3","add3"))
        self.assertFalse(r.process_ok)
        self.assertEqual(r.first_error_step,2)

    def test_exact_empty_program_on_solved_task(self):
        task=replace(self.task,target=1)
        r=self.oracle.verify(task,())
        self.assertTrue(r.process_ok)
        self.assertTrue(r.outcome_ok)
        self.assertEqual(r.step_labels,())

    def test_nonfinite_or_invalid_claims_rejected(self):
        with self.assertRaises(FeedbackError):
            self.oracle.verify(self.task,("add1",),claimed_final=True)
        with self.assertRaises(FeedbackError):
            self.oracle.verify(self.task,["add1"])
        with self.assertRaises(FeedbackError):
            self.oracle.verify(self.task,tuple(["add1"]*33))

    def test_verify_is_deterministic_by_path(self):
        a=self.oracle.verify(self.task,("add3","add3"))
        b=self.oracle.verify(self.task,("add3","add3"))
        self.assertEqual(a,b)
        self.assertNotEqual(a.path_digest,
                            self.oracle.verify(self.task,("add1",)).path_digest)


class RewardAuditTests(unittest.TestCase):
    def setUp(self):
        self.task=SynthesisTask("reward",1,4,4)
        self.good=("add3",)
        self.long_bad=("add1","add1","add1","add1")

    def test_proxy_reward_hacks_length_not_outcome(self):
        self.assertLess(biased_length_reward(self.task,self.good),
                        biased_length_reward(self.task,self.long_bad))
        result=compare_proxy_and_host(self.task,(self.good,self.long_bad))
        self.assertEqual(result.proxy_selected,self.long_bad)
        self.assertFalse(result.proxy_verified)
        self.assertEqual(result.verified_selected,self.good)
        self.assertTrue(result.verified_success)

    def test_reward_precision_false_positive_and_brier(self):
        data=[(self.task,self.good,0.25),
              (self.task,self.long_bad,1.0)]
        audit=audit_reward_model(data,threshold=.5)
        self.assertEqual((audit.tp,audit.fp,audit.tn,audit.fn),(0,1,0,1))
        self.assertEqual(audit.total,2)
        self.assertEqual(audit.positives,1)
        self.assertEqual(audit.precision,0)
        self.assertEqual(audit.false_positive_rate,1.0)
        self.assertGreater(audit.brier,0.7)
        self.assertGreater(audit.expected_calibration_error,0.8)

    def test_perfect_scores_have_zero_calibration_error(self):
        data=[(self.task,self.good,1.0),
              (self.task,self.long_bad,0.0)]
        a=audit_reward_model(data)
        self.assertEqual((a.tp,a.fp,a.tn,a.fn),(1,0,1,0))
        self.assertEqual(a.brier,0)
        self.assertEqual(a.expected_calibration_error,0)

    def test_no_positive_predictions_precision_is_unknown(self):
        a=audit_reward_model([(self.task,self.good,0.1)])
        self.assertIsNone(a.precision)
        self.assertIsNone(a.false_positive_rate)

    def test_bad_probability_rejected_not_cast_bool(self):
        for p in (True,float("nan"),float("inf"),-0.01,1.01,"1"):
            with self.subTest(p=p),self.assertRaises(FeedbackError):
                _probability(p)
        with self.assertRaises(FeedbackError):
            audit_reward_model([(self.task,self.good,0.5)],bins=1)
        with self.assertRaises(FeedbackError):
            audit_reward_model([])

    def test_proxy_is_not_allowed_to_return_nan(self):
        with self.assertRaises(FeedbackError):
            compare_proxy_and_host(
                self.task,(self.good,),
                proxy=lambda *args:float("nan"),
            )

    def test_proxy_cannot_make_unsafe_action_verifiable(self):
        invalid=("publish","publish","publish","publish")
        x=compare_proxy_and_host(self.task,(invalid,self.good))
        self.assertEqual(x.proxy_selected,invalid)
        self.assertFalse(x.proxy_verified)
        self.assertTrue(x.verified_success)


class FeedbackSuiteTests(unittest.TestCase):
    def setUp(self):
        self.suite=load_feedback_suite(DATASET)

    def test_versioned_dataset_and_disjoint_tasks(self):
        self.assertEqual(self.suite.suite_id,"ch30-verified-feedback")
        self.assertEqual(len(self.suite.cases),12)
        self.assertEqual(len(self.suite.for_split("train")),6)
        self.assertEqual(len(self.suite.for_split("heldout")),6)
        self.assertEqual(len(self.suite.digest()),64)

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(FeedbackError):
            replace(self.suite,cases=self.suite.cases+(self.suite.cases[0],))

    def test_cross_split_duplicate_problem_rejected(self):
        train=self.suite.cases[0]
        hidden=FeedbackCase(
            replace(train.task,task_id="leak"),"heldout"
        )
        with self.assertRaises(FeedbackError):
            replace(self.suite,cases=self.suite.cases+(hidden,))

    def test_dataset_digest_detects_change(self):
        original=self.suite.digest()
        c=self.suite.cases[0]
        modified=replace(self.suite,cases=(
            replace(c,task=replace(c.task,target=c.task.target+2)),
            *self.suite.cases[1:],
        ))
        self.assertNotEqual(modified.digest(),original)

    def test_dataset_loader_rejects_malformed_json_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"suite.json"
            p.write_text('{"suite_id":"a","suite_id":"b","version":"v1","cases":[]}')
            with self.assertRaises(FeedbackError):
                load_feedback_suite(p)
            p.write_text('{"suite_id":"a","version":"v1","cases":NaN}')
            with self.assertRaises(FeedbackError):
                load_feedback_suite(p)
            p.write_text(json.dumps({"suite_id":"a","version":"v1",
                                     "cases":[{"task_id":"x"}]}))
            with self.assertRaises(FeedbackError):
                load_feedback_suite(p)

    def test_loader_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"linked.json"
            p.symlink_to(DATASET)
            with self.assertRaises(FeedbackError):
                load_feedback_suite(p)

    def test_feedback_case_requires_one_step_and_valid_split(self):
        with self.assertRaises(FeedbackError):
            FeedbackCase(SynthesisTask("multi",1,4,3),"train")
        with self.assertRaises(FeedbackError):
            FeedbackCase(self.suite.cases[0].task,"production")

    def test_feedback_suite_requires_both_splits(self):
        with self.assertRaises(FeedbackError):
            replace(self.suite,cases=tuple(
                c for c in self.suite.cases if c.split=="train"
            ))


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.suite=load_feedback_suite(DATASET)
        self.records=collect_train_feedback(self.suite)

    def test_feedback_samples_are_trusted_and_train_only(self):
        self.assertEqual(len(self.records),24)
        self.assertTrue(all(x.source=="independent_host_verifier"
                            for x in self.records))
        self.assertTrue(all(x.suite_digest==self.suite.digest()
                            for x in self.records))
        self.assertEqual(sum(x.label for x in self.records),6)

    def test_policy_learns_delta_features_and_generalizes_to_unseen_ids(self):
        baseline=evaluate_one_step_policy(self.suite.for_split("heldout"))
        model=fit_tabular_feedback_policy(self.suite,self.records)
        candidate=evaluate_one_step_policy(
            self.suite.for_split("heldout"),policy=model
        )
        self.assertEqual((baseline.verified,baseline.trials),(3,6))
        self.assertEqual((candidate.verified,candidate.trials),(6,6))
        self.assertEqual(candidate.illegal_actions,0)
        self.assertEqual(model.suite_digest,self.suite.digest())

    def test_policy_is_not_retrained_on_heldout(self):
        task=self.suite.for_split("heldout")[0]
        proof=TrustedArithmeticVerifier().verify(task,("add1",))
        forged=FeedbackRecord(task.task_id,"add1",proof.outcome_ok,
                              proof.process_ok,proof.path_digest,
                              self.suite.digest(),"independent_host_verifier")
        with self.assertRaises(FeedbackError):
            fit_tabular_feedback_policy(self.suite,self.records+(forged,))

    def test_wrong_label_is_detected_independently(self):
        actual=self.records[0]
        tampered=replace(actual,label=not actual.label)
        with self.assertRaises(FeedbackError):
            fit_tabular_feedback_policy(self.suite,(tampered,))

    def test_wrong_path_digest_detected(self):
        rec=self.records[0]
        with self.assertRaises(FeedbackError):
            fit_tabular_feedback_policy(self.suite,(
                replace(rec,path_digest="0"*64),
            ))

    def test_old_suite_digest_cannot_be_reused(self):
        rec=self.records[0]
        with self.assertRaises(FeedbackError):
            fit_tabular_feedback_policy(self.suite,(
                replace(rec,suite_digest="0"*64),
            ))

    def test_duplicate_feedback_rejected(self):
        rec=self.records[0]
        with self.assertRaises(FeedbackError):
            fit_tabular_feedback_policy(self.suite,(rec,rec))

    def test_learning_policy_must_be_one_step(self):
        model=fit_tabular_feedback_policy(self.suite,self.records)
        with self.assertRaises(FeedbackError):
            model.choose(SynthesisTask("multi",1,4,3))

    def test_unsupervised_or_empty_feedback_rejected(self):
        with self.assertRaises(FeedbackError):
            fit_tabular_feedback_policy(self.suite,())
        with self.assertRaises(FeedbackError):
            fit_tabular_feedback_policy(self.suite,("some model opinion",))

    def test_wrong_feedback_source_rejected(self):
        with self.assertRaises(FeedbackError):
            replace(self.records[0],source="self_reported")

    def test_unknown_delta_uses_prior_not_fake_success(self):
        model=fit_tabular_feedback_policy(self.suite,self.records)
        task=SynthesisTask("unseen",10,8,1,max_abs_value=128)
        action=model.choose(task)
        r=TrustedArithmeticVerifier().verify(task,(action,))
        self.assertIn(action,task.allowed_ops)
        self.assertEqual(r.outcome_ok,action=="sub1" and False)

    def test_training_and_heldout_task_id_disjoint(self):
        self.assertFalse({
            t.task_id for t in self.suite.for_split("train")
        }.intersection(t.task_id for t in self.suite.for_split("heldout")))


if __name__ == "__main__":
    unittest.main()
