"""Real Chromium + loopback HTTP integration. Explicit opt-in; no paid model.

RUN_BROWSER_TESTS=1 python -m unittest discover -s tests -p test_ch16_browser.py -v
Missing browser binaries are failures in the opt-in job, NOT silent skips.
"""
from contextlib import ExitStack
from dataclasses import asdict
import json
import os
from types import SimpleNamespace
import unittest

from mini_agent.browser_agent import (
    BrowserAction, BrowserController, BrowserError, BrowserSession, Control,
    ModelBrowserPolicy, Observation, PageState,
)
from mini_agent.browser_fixture import DRAFT_TEXT, GOAL, TARGETS, FixtureBrowserPolicy, fixture_site
from mini_agent.browser_playwright import fixture_browser


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "opt-in real browser integration")
class RealBrowserTests(unittest.TestCase):
    def launch(self, *, fake_success=False):
        stack = ExitStack()
        self.addCleanup(stack.close)
        base, state = stack.enter_context(fixture_site(fake_success=fake_success))
        port = stack.enter_context(fixture_browser(
            base, TARGETS, executable_path=os.getenv("BROWSER_EXECUTABLE")))
        session = BrowserSession(port, run_id="integration", allowed_origin=base, targets=TARGETS)
        return base, state, port, session

    def test_real_flow_pauses_then_saves_exactly_once(self):
        _, state, port, session = self.launch()
        controller = BrowserController(session, state.verify, max_actions=5)
        planner = FixtureBrowserPolicy()
        paused = controller.run(planner)
        self.assertEqual(paused.status, "waiting_approval")
        self.assertEqual(paused.actions, 4)
        self.assertEqual(state.drafts, [])
        self.assertIn('button "Save draft"', session.observe().state.aria)
        done = controller.run(planner, approvals=frozenset({paused.approval_key}))
        self.assertEqual((done.status, done.actions), ("verified", 5))
        self.assertEqual(state.drafts, [{"document": "queue", "text": DRAFT_TEXT}])
        self.assertEqual(state.published, 0)
        self.assertEqual(port.blocked, [])

    def test_fake_success_banner_fails_independent_verifier(self):
        _, state, port, session = self.launch(fake_success=True)
        controller = BrowserController(session, state.verify)
        planner = FixtureBrowserPolicy()
        paused = controller.run(planner)
        done = controller.run(planner, approvals=frozenset({paused.approval_key}))
        self.assertIn("Draft saved", port.page.title())
        self.assertEqual(state.drafts, [])
        self.assertEqual((done.status, done.reason), ("failed", "verification_failed"))

    def test_dom_change_rejects_stale_action(self):
        _, state, port, session = self.launch()
        obs = session.observe()
        port.page.get_by_role("textbox", name="Search notes").fill("Different")
        with self.assertRaisesRegex(BrowserError, "page_changed_reobserve"):
            session.act(obs, BrowserAction("click", obs.snapshot_id, "search"))
        self.assertEqual(state.drafts, [])

    def test_role_locator_survives_element_replacement(self):
        _, _, port, session = self.launch()
        obs = session.observe()
        # Test-owned mutation, never Javascript supplied by an agent.
        port.page.get_by_role("textbox", name="Search notes").evaluate(
            "el => el.replaceWith(el.cloneNode(true))")
        after = session.act(obs, BrowserAction("fill", obs.snapshot_id, "query", "Queue"))
        self.assertEqual(next(c.value for c in after.state.controls if c.target_id == "query"), "Queue")

    def test_duplicate_accessible_names_do_not_choose_first(self):
        base, state, port, session = self.launch()
        port.page.goto(base + "/notes/queue")
        port.page.get_by_role("button", name="Save draft", exact=True).evaluate(
            "el => el.after(el.cloneNode(true))")
        obs = session.observe()
        action = BrowserAction("click", obs.snapshot_id, "save")
        with self.assertRaisesRegex(BrowserError, "ambiguous_target"):
            session.act(obs, action, frozenset({session.approval_key(obs, action)}))
        self.assertEqual(state.drafts, [])

    def test_publish_request_blocked_even_outside_planner(self):
        base, state, port, session = self.launch()
        port.page.goto(base + "/notes/queue")
        # Intentionally bypass the controller in a negative transport test.
        # This should fail at the route gate, without reaching the server.
        from playwright.sync_api import Error
        try:
            port.page.get_by_role("button", name="Publish", exact=True).click()
        except Error:
            pass
        self.assertTrue(port.blocked)
        self.assertEqual(state.published, 0)
        with self.assertRaisesRegex(BrowserError, "network_request_blocked"):
            session.observe()

    def test_model_protocol_adapter_with_real_browser(self):
        _, state, _, session = self.launch()
        class FakeModel:
            def complete(self, messages, tools):
                raw = json.loads(messages[1]["content"])["observation"]
                s = raw["state"]
                obs = Observation(raw["snapshot_id"], PageState(
                    s["url"], s["title"], s["aria"], tuple(Control(**c) for c in s["controls"])))
                proposal = FixtureBrowserPolicy().decide(obs)
                return SimpleNamespace(content=json.dumps(asdict(proposal)),
                                       finish_reason="stop", tool_calls=())
        planner = ModelBrowserPolicy(FakeModel(), GOAL, TARGETS)
        controller = BrowserController(session, state.verify)
        paused = controller.run(planner)
        done = controller.run(planner, approvals=frozenset({paused.approval_key}))
        self.assertEqual(done.status, "verified")

    def test_fresh_context_has_no_cookies_from_previous_session(self):
        with fixture_site() as (base, _):
            with fixture_browser(base, TARGETS, executable_path=os.getenv("BROWSER_EXECUTABLE")) as first:
                first.context.add_cookies([{"name": "test-session", "value": "one", "url": base}])
                self.assertTrue(first.context.cookies())
            with fixture_browser(base, TARGETS, executable_path=os.getenv("BROWSER_EXECUTABLE")) as second:
                self.assertEqual(second.context.cookies(), [])


if __name__ == "__main__":
    unittest.main()
