"""Real Chromium against a local fixture; no LLM/network account required.

pip install -e '.[browser]'
python -m playwright install chromium
python examples/ch16_browser_lab.py
"""
import os
from uuid import uuid4

from mini_agent.browser_agent import BrowserController, BrowserSession
from mini_agent.browser_fixture import DRAFT_TEXT, TARGETS, FixtureBrowserPolicy, fixture_site
from mini_agent.browser_playwright import fixture_browser


def main() -> None:
    with fixture_site() as (base, state):
        with fixture_browser(base, TARGETS, executable_path=os.getenv("BROWSER_EXECUTABLE")) as port:
            session = BrowserSession(port, run_id=uuid4().hex, allowed_origin=base, targets=TARGETS)
            controller = BrowserController(session, state.verify, max_actions=5)
            planner = FixtureBrowserPolicy()
            paused = controller.run(planner)
            assert paused.status == "waiting_approval" and not state.drafts
            print("Before approval:", paused.status, "drafts=", len(state.drafts))
            # Host approval ONLY for this known local fixture and exact draft.
            # A real product requires authenticated user consent, not this line.
            assert session.observe().state.url.endswith("/notes/queue")
            assert any(c.target_id == "draft" and c.value == DRAFT_TEXT
                       for c in session.observe().state.controls)
            result = controller.run(planner, approvals=frozenset({paused.approval_key}))
            print("After approval:", result.status)
            print("Actions:", result.actions)
            print("Draft records:", len(state.drafts), "Published:", state.published)
            print("Evidence:", result.verification.evidence if result.verification else result.reason)
            assert result.status == "verified" and result.actions == 5
            print("Real browser + local HTTP + scripted policy; not general GUI autonomy.")


if __name__ == "__main__":
    main()
