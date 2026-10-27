"""Regression tests for evaluation protocol, seeds and trial checkpoints."""
import tempfile
from pathlib import Path
from dataclasses import replace
import unittest

from mini_agent.agent_evaluation import EvalError, load_suite
from mini_agent.benchmark_framework import (
    BenchmarkProtocol, CandidateConfig, RegressionGate, run_benchmark, trial_seed
)
from mini_agent.eval_fixture import KernelFixtureRunner
from mini_agent.eval_campaign import CampaignSpec, CampaignStore

SUITE=Path(__file__).resolve().parents[1]/"evals/ch25_offline_suite.json"


def protocol():
    return BenchmarkProtocol(
        "ch25-fixture-suite","v1","legacy-default","legacy-prompt",
        "fixture-tools-v1","reset-v1","verifier-v1","inprocess",42,"heldout",3
    )


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.suite=load_suite(SUITE)
        self.protocol=protocol()
        self.base=CandidateConfig("model-a","prompt-a")
        self.candidate=CandidateConfig("model-b","prompt-b")

    def test_distinct_candidate_model_prompt_config_same_shared_protocol(self):
        a=run_benchmark(self.suite,self.protocol,"baseline",
                        KernelFixtureRunner("baseline"),candidate_config=self.base)
        b=run_benchmark(self.suite,self.protocol,"candidate",
                        KernelFixtureRunner("candidate"),candidate_config=self.candidate)
        self.assertEqual(a.protocol_digest,b.protocol_digest)
        self.assertNotEqual(a.candidate_config_digest,b.candidate_config_digest)
        self.assertEqual(b.report.successes,5)

    def test_seed_passed_to_context_aware_runner(self):
        runner=KernelFixtureRunner("candidate")
        run_benchmark(self.suite,self.protocol,"candidate",runner,
                      candidate_config=self.candidate)
        context=runner.last_trial_context
        self.assertEqual(context.seed,trial_seed(self.protocol,context.case_id,context.trial_index))
        self.assertEqual(context.candidate_config,self.candidate)
        self.assertEqual(trial_seed(self.protocol,"browser-draft",1),
                         trial_seed(self.protocol,"browser-draft",1))
        self.assertNotEqual(trial_seed(self.protocol,"browser-draft",1),
                            trial_seed(self.protocol,"browser-draft",2))

    def test_nan_success_threshold_rejected(self):
        for bad in (float("nan"),float("inf"),-0.01):
            with self.assertRaises(EvalError):
                RegressionGate(minimum_micro_rate=bad)

    def test_campaign_config_changes_digest(self):
        a=CampaignSpec("study-1",self.protocol,"baseline","candidate",
                       self.base,self.candidate)
        b=replace(a,candidate_config=CandidateConfig("model-c","prompt-b"))
        self.assertNotEqual(a.digest(),b.digest())

    def test_checkpoint_survives_abrupt_termination_and_resumes(self):
        class InterruptAfterFirst(KernelFixtureRunner):
            def __init__(self):
                super().__init__("baseline")
                self.calls=0
            def run(self,*args):
                self.calls+=1
                if self.calls==2:
                    raise KeyboardInterrupt("synthetic crash")
                return super().run(*args)

        class Counting(KernelFixtureRunner):
            def __init__(self,variant):
                super().__init__(variant)
                self.calls=0
            def run(self,*args):
                self.calls+=1
                return super().run(*args)

        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"campaign.sqlite"
            store=CampaignStore(path)
            spec=CampaignSpec("study-1",self.protocol,"baseline","candidate",
                              self.base,self.candidate)
            store.initialize(spec)
            with self.assertRaises(KeyboardInterrupt):
                store.evaluate(spec,self.suite,InterruptAfterFirst(),
                               KernelFixtureRunner("candidate"),
                               gate=RegressionGate(minimum_micro_rate=.8))
            stored=store.trial_rows(spec.campaign_id)
            self.assertEqual(len(stored),1)
            self.assertEqual(store.load(spec.campaign_id).status,"created")
            restarted=CampaignStore(path)
            base=Counting("baseline")
            candidate=Counting("candidate")
            finished=restarted.evaluate(spec,self.suite,base,candidate,
                                       gate=RegressionGate(minimum_micro_rate=.8))
            self.assertEqual(finished.status,"approve")
            self.assertEqual(base.calls,2)
            self.assertEqual(candidate.calls,3)
            self.assertEqual(len(restarted.trial_rows(spec.campaign_id)),6)
            self.assertEqual(finished.baseline_rate,.5)

    def test_checkpoint_dataset_change_is_rejected(self):
        class InterruptAfterFirst(KernelFixtureRunner):
            def __init__(self):
                super().__init__("baseline")
                self.calls=0
            def run(self,*args):
                self.calls+=1
                if self.calls==2:
                    raise KeyboardInterrupt()
                return super().run(*args)
        with tempfile.TemporaryDirectory() as tmp:
            store=CampaignStore(Path(tmp)/"checkpoint.db")
            spec=CampaignSpec("study-2",self.protocol,"baseline","candidate")
            store.initialize(spec)
            with self.assertRaises(KeyboardInterrupt):
                store.evaluate(spec,self.suite,InterruptAfterFirst(),
                               KernelFixtureRunner("candidate"),
                               gate=RegressionGate())
            modified=replace(self.suite,cases=(
                replace(self.suite.cases[0],instruction="Changed task."),
                *self.suite.cases[1:],
            ))
            with self.assertRaises(EvalError):
                store.evaluate(spec,modified,KernelFixtureRunner("baseline"),
                               KernelFixtureRunner("candidate"),
                               gate=RegressionGate())


if __name__=="__main__":
    unittest.main()
