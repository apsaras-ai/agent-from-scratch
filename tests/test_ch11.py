import asyncio
import unittest
from mini_agent.workflow import Node, Workflow, WorkflowError


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_and_approval(self):
        active = peak = 0
        async def read(_):
            nonlocal active, peak
            active += 1
            peak = max(active, peak)
            await asyncio.sleep(.01)
            active -= 1
            return "read"
        async def approve(_): return "written"
        w = Workflow((Node("a", read), Node("b", read),
                      Node("write", approve, ("a", "b"), approval=True)),
                     max_parallel=2)
        run = await w.run()
        self.assertEqual(run.status, "waiting_approval")
        self.assertEqual(peak, 2)
        self.assertEqual(run.results.keys(), {"a", "b"})
        self.assertEqual((await w.run(run, approved="wrong")).status, "waiting_approval")
        done = await w.run(run, approved="write")
        self.assertEqual(done.status, "completed")
        self.assertEqual(done.results["write"], "written")

    async def test_branch_skip(self):
        async def no(_): return False
        async def skip(_): raise AssertionError("Must not execute")
        wf = Workflow((Node("condition", no, route="branch"),
                       Node("branch", skip, ("condition",)),
                       Node("dependent", skip, ("branch",))))
        result = await wf.run()
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.skipped, {"branch", "dependent"})

    async def test_failure_and_budget(self):
        async def bad(_): raise RuntimeError("failure")
        w = Workflow((Node("bad", bad),))
        self.assertEqual((await w.run()).status, "failed")
        async def ok(_): return 1
        w = Workflow((Node("a", ok), Node("b", ok, ("a",))),
                     max_steps=1)
        self.assertIn("step_budget_exhausted", (await w.run()).events)

    async def test_invalid_graph(self):
        async def noop(_): return True
        with self.assertRaises(WorkflowError):
            Workflow((Node("a", noop, ("missing",)),))
        with self.assertRaises(WorkflowError):
            Workflow((Node("a", noop, ("b",)), Node("b", noop, ("a",))))


if __name__ == "__main__":
    unittest.main()
