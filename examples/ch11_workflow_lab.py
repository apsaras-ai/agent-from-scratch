"""Chapter 11 offline DAG workflow with independent parallel nodes and approval."""
import asyncio
from mini_agent.workflow import Node, Workflow


async def main():
    async def inspect(_): return "ok"
    async def search(_): return "found"
    async def decision(state): return state["inspect"] == "ok"
    async def write(_): return "patch staged"
    async def report(state): return "report:" + state["search"]

    wf = Workflow((
        Node("inspect", inspect),
        Node("search", search),
        Node("decision", decision, ("inspect",), route="write"),
        Node("write", write, ("decision",), approval=True),
        Node("report", report, ("search",)),
    ), max_parallel=2)
    result = await wf.run()
    print("Before approval:", result.status, result.approval_for)
    result = await wf.run(result, approved="write")
    print("After approval:", result.status)
    print("Results:", sorted(result.results))
    print("Events:", result.events)


if __name__ == "__main__":
    asyncio.run(main())
