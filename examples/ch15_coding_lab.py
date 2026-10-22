"""Real file editing/tests on a trusted fixture; no LLM, network, or API key."""
import argparse
from pathlib import Path
import tempfile

from mini_agent.coding_agent import CodingAgent, TrustedPythonTests
from mini_agent.coding_fixture import ACCEPTANCE, ISSUE, TARGETED, FixturePlanner, make_fixture


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patch-output", type=Path, help="Optional new UTF-8 diff file (never overwrite)")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ch15-source-") as folder:
        source = make_fixture(Path(folder))
        result = CodingAgent(FixturePlanner(), TrustedPythonTests()).run(
            source, ISSUE, targeted=TARGETED, acceptance=ACCEPTANCE,
        )
        print(f"Baseline: tests={result.baseline.tests} failures={result.baseline.failures}")
        for attempt in result.attempts:
            print(f"Attempt {attempt.number}: {attempt.reason}")
        print("Status:", result.status)
        print("Source unchanged:", source.source_unchanged())
        print(result.patch, end="")
        assert result.status == "verified" and source.source_unchanged()
        if args.patch_output:
            with args.patch_output.open("x", encoding="utf-8", newline="\n") as output:
                output.write(result.patch)
        print("Trusted fixture only: real edits/tests, scripted planner, not a sandbox.")


if __name__ == "__main__":
    main()
