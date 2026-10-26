"""Ch27: persistent two-version evaluation + static dashboard (offline)."""
from pathlib import Path
import tempfile
from mini_agent.agent_evaluation import load_suite
from mini_agent.eval_fixture import KernelFixtureRunner
from mini_agent.benchmark_framework import BenchmarkProtocol, RegressionGate
from mini_agent.eval_campaign import CampaignSpec, CampaignStore, dashboard_html


def main():
    suite=load_suite(Path(__file__).resolve().parents[1]/
                     "evals/ch25_offline_suite.json")
    protocol=BenchmarkProtocol(
        suite.suite_id,suite.version,"toy-model-v1","prompt-v1",
        "tool-v1","environment-v1","verifier-v1",
        "inprocess-only",42,"heldout",3)
    campaign=CampaignSpec("chapter-27",protocol,"baseline","candidate")
    with tempfile.TemporaryDirectory() as scratch:
        store=CampaignStore(Path(scratch)/"campaign.sqlite")
        before=store.initialize(campaign)
        result=store.evaluate(
            campaign,suite,KernelFixtureRunner("baseline"),
            KernelFixtureRunner("candidate"),
            gate=RegressionGate(minimum_micro_rate=.8),
            expected_revision=before.revision)
        static=Path(scratch)/"dashboard.html"
        static.write_text(dashboard_html(result),encoding="utf-8")
        print("Status:",result.status,"reason:",result.reason)
        print("Baseline:",result.baseline_rate,"Candidate:",result.candidate_rate)
        print("Paired improvements:",result.paired_improvements)
        print("Dashboard generated:",static.exists(),"bytes:",static.stat().st_size)
        print("Audit event count:",len(store.events(campaign.campaign_id)))
        assert result.status=="approve"
    print("No actual deployment, external model or network call occurred.")


if __name__=="__main__":
    main()
