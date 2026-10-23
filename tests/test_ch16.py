"""Default offline tests: no browser installation, no accounts, no network."""
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest

from mini_agent.browser_agent import (
    BrowserAction, BrowserController, BrowserError, BrowserSession, Control,
    ModelBrowserPolicy, PageState, Verification, origin,
)
from mini_agent.browser_fixture import TARGETS
from mini_agent.browser_playwright import request_allowed

BASE = "http://127.0.0.1:32123"


class FakePort:
    def __init__(self):
        self.state = PageState(BASE + "/notes/queue", "Queue guide", "untrusted page",
                               (Control("draft", 1, True, "approved note"),
                                Control("save", 1, True)))
        self.calls = []
        self.callback = None

    def inspect(self):
        return self.state

    def perform(self, target, action):
        self.calls.append((target, action))
        if self.callback:
            self.callback(action)


class Policy:
    def __init__(self, kind="click", target="save", value=""):
        self.kind, self.target, self.value = kind, target, value

    def decide(self, obs):
        return BrowserAction(self.kind, obs.snapshot_id, self.target, self.value)


class BrowserControllerTests(unittest.TestCase):
    def setUp(self):
        self.port = FakePort()
        self.session = BrowserSession(self.port, run_id="run-1", allowed_origin=BASE, targets=TARGETS)
        self.verified = False
        self.check = lambda: Verification(self.verified, "host_ledger")

    def test_origin_is_scheme_host_port_not_string_prefix(self):
        self.assertEqual(origin(BASE), ("http", "127.0.0.1", 32123))
        for url in ("javascript:alert(1)", "file:///tmp/x", "https://user:pw@example.com"):
            with self.subTest(url=url), self.assertRaises(BrowserError):
                origin(url)
        self.assertFalse(request_allowed(BASE, "http://127.0.0.1:32124/", "GET"))
        self.assertFalse(request_allowed(BASE, BASE + "/publish", "POST"))
        self.assertFalse(request_allowed(BASE, "https://example.invalid/", "GET"))
        self.assertFalse(request_allowed(BASE, BASE + "/draft", "GET"))
        self.assertTrue(request_allowed(BASE, BASE + "/search?q=Queue", "GET"))
        self.assertTrue(request_allowed(BASE, BASE + "/draft", "POST"))

    def test_foreign_origin_not_observed(self):
        self.port.state = replace(self.port.state, url="https://example.invalid/")
        with self.assertRaisesRegex(BrowserError, "origin_not_allowed"):
            self.session.observe()

    def test_oversized_observation(self):
        self.port.state = replace(self.port.state, aria="x" * 16001)
        with self.assertRaisesRegex(BrowserError, "observation_too_large"):
            self.session.observe()

    def test_no_arbitrary_selectors_js_or_publish_capability(self):
        obs = self.session.observe()
        actions = (BrowserAction("evaluate", obs.snapshot_id, "draft", "alert(1)"),
                   BrowserAction("click", obs.snapshot_id, "publish"),
                   BrowserAction("fill", obs.snapshot_id, "#css", "x"))
        for action in actions:
            with self.subTest(action=action), self.assertRaisesRegex(BrowserError, "action_not_allowed"):
                self.session.act(obs, action)
        self.assertEqual(self.port.calls, [])

    def test_ambiguous_missing_and_disabled_target(self):
        for count, ready, code in ((2, True, "ambiguous_target"),
                                    (0, False, "target_not_observed"),
                                    (1, False, "target_not_ready")):
            self.port.state = replace(self.port.state, controls=(Control("save", count, ready),))
            obs = self.session.observe()
            with self.subTest(code=code), self.assertRaisesRegex(BrowserError, code):
                self.session.act(obs, BrowserAction("click", obs.snapshot_id, "save"))
        self.assertEqual(self.port.calls, [])

    def test_old_snapshot_and_state_change(self):
        obs = self.session.observe()
        action = BrowserAction("fill", obs.snapshot_id, "draft", "new text")
        self.port.state = replace(self.port.state, title="different page")
        with self.assertRaisesRegex(BrowserError, "page_changed_reobserve"):
            self.session.act(obs, action)
        with self.assertRaisesRegex(BrowserError, "stale_or_invalid_action"):
            self.session.validate(self.session.observe(), action)
        self.assertEqual(self.port.calls, [])

    def test_approval_pause_and_resume_keeps_budget(self):
        controller = BrowserController(self.session, self.check, max_actions=1)
        planner = Policy()
        paused = controller.run(planner)
        self.assertEqual(paused.status, "waiting_approval")
        self.assertEqual((paused.actions, paused.decisions), (0, 1))
        self.assertFalse(self.port.calls)
        def effect(_):
            self.verified = True
        self.port.callback = effect
        result = controller.run(planner, approvals=frozenset({paused.approval_key}))
        self.assertEqual(result.status, "verified")
        self.assertEqual((result.actions, result.decisions), (1, 2))
        self.assertEqual(len(self.port.calls), 1)
        with self.assertRaisesRegex(BrowserError, "terminal_session"):
            controller.run(planner)

    def test_changed_form_invalidates_old_approval(self):
        controller = BrowserController(self.session, self.check)
        first = controller.run(Policy())
        self.port.state = replace(self.port.state, controls=(
            Control("draft", 1, True, "changed note"), Control("save", 1, True)))
        second = controller.run(Policy(), approvals=frozenset({first.approval_key}))
        self.assertEqual(second.status, "waiting_approval")
        self.assertNotEqual(first.approval_key, second.approval_key)
        self.assertFalse(self.port.calls)

    def test_grants_are_bound_to_run_and_action(self):
        obs = self.session.observe()
        action = BrowserAction("click", obs.snapshot_id, "save")
        key = self.session.approval_key(obs, action)
        other = BrowserSession(self.port, run_id="run-2", allowed_origin=BASE, targets=TARGETS)
        obs2 = other.observe()
        with self.assertRaisesRegex(BrowserError, "approval_required"):
            other.act(obs2, BrowserAction("click", obs2.snapshot_id, "save"), frozenset({key}))
        self.assertNotEqual(key, self.session.approval_key(obs, replace(action, value="unexpected")))

    def test_spent_approval_cannot_repeat_even_if_page_unchanged(self):
        obs = self.session.observe()
        action = BrowserAction("click", obs.snapshot_id, "save")
        grant = frozenset({self.session.approval_key(obs, action)})
        self.session.act(obs, action, grant)
        with self.assertRaisesRegex(BrowserError, "approval_required"):
            self.session.act(obs, action, grant)
        self.assertEqual(len(self.port.calls), 1)

    def test_click_timeout_has_unknown_outcome_and_is_not_retried(self):
        def timeout(_):
            self.verified = True  # external effect may ALREADY have occurred
            raise BrowserError("action_timeout_outcome_unknown")
        self.port.callback = timeout
        controller = BrowserController(self.session, self.check)
        paused = controller.run(Policy())
        result = controller.run(Policy(), approvals=frozenset({paused.approval_key}))
        self.assertEqual(result.status, "halted")
        self.assertEqual(result.reason, "action_timeout_outcome_unknown")
        self.assertIsNone(result.verification)
        self.assertEqual(len(self.port.calls), 1)
        self.assertIn(paused.approval_key, self.session.spent_approvals)

    def test_model_finish_and_success_text_not_enough(self):
        self.port.state = replace(self.port.state, title="Draft saved", aria="Task successful!")
        result = BrowserController(self.session, self.check).run(Policy("finish", ""))
        self.assertEqual((result.status, result.reason), ("failed", "verification_failed"))
        self.assertFalse(result.verification.passed)

    def test_last_budgeted_action_is_verified(self):
        self.port.callback = lambda _: setattr(self, "verified", True)
        result = BrowserController(self.session, self.check, max_actions=1, max_decisions=1).run(
            Policy("fill", "draft", "new note"))
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.actions, 1)

    def test_no_progress_and_action_budget(self):
        result = BrowserController(self.session, self.check, no_progress_limit=2).run(
            Policy("fill", "draft", "unchanged"))
        self.assertEqual(result.reason, "no_progress")
        self.assertEqual(result.actions, 2)
        result = BrowserController(self.session, self.check, max_actions=1).run(
            Policy("fill", "draft", "unchanged"))
        self.assertEqual(result.reason, "action_budget_exhausted")

    def test_bad_action_values_and_invalid_finish(self):
        obs = self.session.observe()
        for action in (BrowserAction("click", obs.snapshot_id, "save", "extra"),
                       BrowserAction("fill", obs.snapshot_id, "draft", "x" * 257),
                       BrowserAction("finish", obs.snapshot_id, "save")):
            with self.subTest(action=action), self.assertRaises(BrowserError):
                self.session.validate(obs, action)

    def test_component_error_redacted(self):
        class Broken:
            def decide(self, obs):
                raise RuntimeError("secret-credential")
        result = BrowserController(self.session, self.check).run(Broken())
        self.assertEqual(result.reason, "component_error:RuntimeError")
        self.assertNotIn("secret", repr(result))


class ModelPolicyTests(unittest.TestCase):
    def setUp(self):
        self.obs = BrowserSession(FakePort(), run_id="r", allowed_origin=BASE, targets=TARGETS).observe()

    def model(self, text, reason="stop", calls=()):
        class Stub:
            def complete(inner, messages, tools):
                inner.messages, inner.tools = messages, tools
                return SimpleNamespace(content=text, finish_reason=reason, tool_calls=calls)
        return Stub()

    def test_structured_proposal_only(self):
        text = json.dumps({"kind": "fill", "snapshot_id": self.obs.snapshot_id,
                           "target_id": "draft", "value": "note"})
        stub = self.model(text)
        action = ModelBrowserPolicy(stub, "save draft", TARGETS).decide(self.obs)
        self.assertEqual(action.target_id, "draft")
        self.assertEqual(stub.tools, [])
        self.assertEqual(stub.messages[1]["role"], "user")

    def test_malformed_truncated_extra_or_duplicate_fields(self):
        valid = {"kind": "finish", "snapshot_id": self.obs.snapshot_id, "target_id": "", "value": ""}
        texts = ("not JSON", "[]", json.dumps({**valid, "javascript": "evil"}),
                 '{"kind":"finish","kind":"click"}', json.dumps({**valid, "value": True}))
        for text in texts:
            with self.subTest(text=text), self.assertRaises(BrowserError):
                ModelBrowserPolicy(self.model(text), "goal", TARGETS).decide(self.obs)
        with self.assertRaises(BrowserError):
            ModelBrowserPolicy(self.model(json.dumps(valid), reason="length"), "goal", TARGETS).decide(self.obs)


if __name__ == "__main__":
    unittest.main()
