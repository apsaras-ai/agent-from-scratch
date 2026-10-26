"""Ch27: durable campaign and regression Dashboard. No real model or network."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from mini_agent.agent_evaluation import EvalError, load_suite
from mini_agent.benchmark_framework import BenchmarkProtocol,RegressionGate
from mini_agent.eval_fixture import KernelFixtureRunner
from mini_agent.eval_campaign import CampaignSpec,CampaignStore,dashboard_html

SUITE=Path(__file__).resolve().parents[1]/"evals/ch25_offline_suite.json"


def settings(split="heldout"):
    protocol=BenchmarkProtocol(
        "ch25-fixture-suite","v1","toy-model-v1","prompt-v1",
        "tools-v1","environment-v1","verifier-v1",
        "inprocess-not-sandbox",42,split,3)
    return CampaignSpec("campaign-001",protocol,"baseline","candidate")


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=CampaignStore(Path(self.tmp.name)/"campaigns.db")
        self.suite=load_suite(SUITE)
        self.spec=settings()

    def tearDown(self):
        self.tmp.cleanup()

    def run_eval(self, *,spec=None,gate=None,revision=0):
        return self.store.evaluate(
            spec or self.spec,self.suite,
            KernelFixtureRunner("baseline"),KernelFixtureRunner("candidate"),
            gate=gate or RegressionGate(minimum_micro_rate=.8),
            expected_revision=revision)

    def test_created_and_reloaded(self):
        first=self.store.initialize(self.spec)
        self.assertEqual((first.status,first.revision),("created",0))
        self.assertEqual(self.store.initialize(self.spec),first)
        fresh=CampaignStore(self.store.path)
        self.assertEqual(fresh.load(self.spec.campaign_id),first)
        self.assertEqual(len(self.store.events(self.spec.campaign_id)),1)

    def test_summary_is_based_on_actual_environment(self):
        self.store.initialize(self.spec)
        result=self.run_eval()
        self.assertEqual(result.status,"approve")
        self.assertEqual((result.baseline_rate,result.candidate_rate),(.5,5/6))
        self.assertEqual((result.paired_improvements,result.paired_regressions),(2,0))
        self.assertEqual(result.revision,1)

    def test_gate_block_and_unknown_cost(self):
        self.store.initialize(self.spec)
        result=self.run_eval(gate=RegressionGate(minimum_micro_rate=.99))
        self.assertEqual(result.status,"block")

    def test_duplicate_campaign_id_other_protocol_rejected(self):
        self.store.initialize(self.spec)
        with self.assertRaises(EvalError):
            self.store.initialize(replace(self.spec,candidate_id="another"))
        with self.assertRaises(EvalError):
            self.store.initialize(replace(self.spec,protocol=settings("dev").protocol))

    def test_stale_revision_and_terminal_are_rejected(self):
        self.store.initialize(self.spec)
        self.run_eval()
        with self.assertRaises(EvalError):
            self.run_eval()
        with self.assertRaises(EvalError):
            self.run_eval(revision=1)
        self.assertEqual(len(self.store.events(self.spec.campaign_id)),2)

    def test_invalid_generation_rejected(self):
        self.store.initialize(self.spec)
        with self.assertRaises(EvalError):
            self.run_eval(revision=True)

    def test_concurrent_controllers_only_one_writes(self):
        self.store.initialize(self.spec)
        a,b=CampaignStore(self.store.path),CampaignStore(self.store.path)
        def run(s):
            try:
                return s.evaluate(
                    self.spec,self.suite,
                    KernelFixtureRunner("baseline"),
                    KernelFixtureRunner("candidate"),
                    gate=RegressionGate(minimum_micro_rate=.8),
                    expected_revision=0).status
            except EvalError:
                return "fenced"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(run,(a,b)))
        self.assertCountEqual(results,["approve","fenced"])

    def test_dataset_split_mismatch_is_rejected(self):
        self.store.initialize(self.spec)
        wrong=replace(self.spec,protocol=settings("dev").protocol)
        with self.assertRaises(EvalError):
            self.run_eval(spec=wrong)
        self.assertEqual(self.store.load(self.spec.campaign_id).status,"created")

    def test_runner_failure_does_not_lead_to_approval(self):
        self.store.initialize(self.spec)
        class Broken:
            def run(self,*args):
                raise RuntimeError("SECRET MUST NOT APPEAR")
        record=self.store.evaluate(
            self.spec,self.suite,KernelFixtureRunner("baseline"),Broken(),
            gate=RegressionGate(minimum_micro_rate=.8))
        self.assertEqual(record.status,"block")
        self.assertNotIn("SECRET",repr(record))

    def test_campaign_spec_validation(self):
        with self.assertRaises(EvalError):
            CampaignSpec("../escape",self.spec.protocol,"baseline","candidate")
        with self.assertRaises(EvalError):
            CampaignSpec("good",self.spec.protocol,"same","same")

    def test_read_only_dashboard_is_safely_escaped(self):
        self.store.initialize(self.spec)
        state=self.run_eval()
        html=dashboard_html(state)
        self.assertIn("<table>",html)
        self.assertIn("83",html)
        self.assertNotIn("<script",html)
        self.assertNotIn("grounded",html)
        self.assertNotIn("synthetic-message",html)
        forged=replace(state,campaign_id="<img src=x onerror=alert(1)>")
        safe=dashboard_html(forged)
        self.assertIn("&lt;img",safe)
        self.assertNotIn("<img",safe)

    def test_created_dashboard_has_unknown_and_no_telemetry(self):
        state=self.store.initialize(self.spec)
        html=dashboard_html(state)
        self.assertIn("Unknown",html)
        self.assertNotIn("None",html)

    def test_events_are_metadata_only(self):
        self.store.initialize(self.spec)
        self.run_eval()
        text=repr(self.store.events(self.spec.campaign_id))
        self.assertNotIn("Save draft",text)
        self.assertNotIn("grounded",text)

    def test_campaign_invalid_file_location(self):
        with self.assertRaises(EvalError):
            CampaignStore(":memory:")


if __name__=="__main__":
    unittest.main()
