"""Chapter 17: repeatable synthetic multi-agent research team.

Synthetic fixture only. Agent workers are explicit Python rules, not LLMs.
The independent verifier checks task-specific expected evidence against the
host-owned fixture, rather than trusting specialists' 'done' messages.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter

from .multi_agent import (
    AgentIdentity, AgentReply, Artifact, Blackboard, DelegatedTask, Delegation,
    ProposedArtifact, Specialist, Supervisor, TeamResult, Verification,
)


API_SOURCE = "API fixture: POST /v1/tasks requires task_id."
TEST_SOURCE = "Test fixture: missing task_id returns HTTP 400."
EXPECTED_BRIEF = (
    "API: POST /v1/tasks requires task_id.\n"
    "Validation: missing task_id returns HTTP 400."
)


class ActivityMeter:
    """Observe logical overlap of the fixture's independent I/O tasks."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def synthetic_io(self) -> None:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(.02)
        finally:
            self.active -= 1


class FixtureWorker:
    def __init__(self, meter: ActivityMeter) -> None:
        self.meter = meter
        self.received: list[tuple[str, tuple[str, ...]]] = []

    async def act(self, delegation: Delegation, inputs) -> AgentReply:
        self.received.append((delegation.task_id, tuple(sorted(inputs))))
        key = delegation.output_keys[0]
        if key == "api_finding":
            await self.meter.synthetic_io()
            content = inputs["raw_api"].content.removeprefix("API fixture: ")
            citation = ("fixture://api",)
        elif key == "test_finding":
            await self.meter.synthetic_io()
            content = inputs["raw_tests"].content.removeprefix("Test fixture: ")
            citation = ("fixture://tests",)
        elif key == "research_brief":
            content = (
                "API: " + inputs["api_finding"].content + "\n"
                "Validation: " + inputs["test_finding"].content
            )
            citation = ("fixture://api", "fixture://tests")
        else:
            raise ValueError("Unsupported fixture task")
        return AgentReply(
            delegation.task_id,
            (ProposedArtifact(key, content, citation),),
            summary="proposed_unverified_output",
        )


def make_plan(*, specialized: bool) -> tuple[DelegatedTask, ...]:
    first = "api" if specialized else "generalist"
    second = "tests" if specialized else "generalist"
    third = "writer" if specialized else "generalist"
    return (
        DelegatedTask("lookup-api", first, "Read API fixture",
                      ("raw_api",), ("api_finding",)),
        DelegatedTask("lookup-tests", second, "Read tests fixture",
                      ("raw_tests",), ("test_finding",)),
        DelegatedTask("compose", third, "Compose attributed research note",
                      ("api_finding", "test_finding"), ("research_brief",),
                      ("lookup-api", "lookup-tests")),
    )


def make_team(meter: ActivityMeter, *, specialized: bool):
    if specialized:
        workers = {
            "api": Specialist(
                AgentIdentity("api", frozenset({"raw_api"}),
                              frozenset({"api_finding"})),
                FixtureWorker(meter),
            ),
            "tests": Specialist(
                AgentIdentity("tests", frozenset({"raw_tests"}),
                              frozenset({"test_finding"})),
                FixtureWorker(meter),
            ),
            "writer": Specialist(
                AgentIdentity("writer", frozenset({"api_finding", "test_finding"}),
                              frozenset({"research_brief"})),
                FixtureWorker(meter),
            ),
        }
    else:
        workers = {
            "generalist": Specialist(
                AgentIdentity(
                    "generalist",
                    frozenset({"raw_api", "raw_tests", "api_finding", "test_finding"}),
                    frozenset({"api_finding", "test_finding", "research_brief"}),
                ),
                FixtureWorker(meter),
            )
        }
    return workers


def verifier(artifacts) -> Verification:
    """Check host-owned expected text, required artifacts, and source IDs."""
    expected = {
        "raw_api": (API_SOURCE, ("fixture://api",)),
        "raw_tests": (TEST_SOURCE, ("fixture://tests",)),
        "api_finding": ("POST /v1/tasks requires task_id.", ("fixture://api",)),
        "test_finding": ("missing task_id returns HTTP 400.", ("fixture://tests",)),
        "research_brief": (EXPECTED_BRIEF, ("fixture://api", "fixture://tests")),
    }
    ok = all(
        key in artifacts and artifacts[key].content == value
        and artifacts[key].evidence_ids == citations
        for key, (value, citations) in expected.items()
    )
    return Verification(ok, "host_fixture_checked" if ok else "evidence_or_brief_mismatch")


def seeded_board(run_id: str) -> Blackboard:
    board = Blackboard(run_id)
    board.seed("raw_api", API_SOURCE, evidence_ids=("fixture://api",))
    board.seed("raw_tests", TEST_SOURCE, evidence_ids=("fixture://tests",))
    return board


@dataclass(frozen=True)
class Experiment:
    result: TeamResult
    peak_concurrent_io: int
    elapsed_ms: float


async def run_fixture(*, specialized: bool) -> Experiment:
    run_id = "multi" if specialized else "single"
    meter = ActivityMeter()
    board = seeded_board(run_id)
    team = make_team(meter, specialized=specialized)
    supervisor = Supervisor(team, max_parallel=2 if specialized else 1)
    started = perf_counter()
    result = await supervisor.execute(
        make_plan(specialized=specialized), board=board, verifier=verifier
    )
    elapsed_ms = (perf_counter() - started) * 1000
    return Experiment(result, meter.peak, elapsed_ms)
