"""Chapter 16: Playwright port for the TRUSTED loopback fixture only.

No user browser, stored cookies, login, arbitrary URLs or model Javascript.
HTTP request routing is a test guard, NOT a complete egress security boundary:
redirect chains, other protocols, browser processes and hostile pages require
OS/network isolation. The supplied fixture issues no redirects or websockets.
"""
from __future__ import annotations

from contextlib import contextmanager
from urllib.parse import urlsplit

from .browser_agent import BrowserAction, BrowserError, Control, PageState, Target, origin

# Fixed application contract, not derived from page-provided instructions.
REQUESTS = frozenset({("GET", "/"), ("GET", "/search"),
                      ("GET", "/notes/queue"), ("GET", "/favicon.ico"), ("POST", "/draft")})


def request_allowed(base: str, url: str, method: str) -> bool:
    try:
        return origin(url) == origin(base) and (method, urlsplit(url).path) in REQUESTS
    except BrowserError:
        return False


class PlaywrightBrowserPort:
    def __init__(self, page, context, base: str, targets: tuple[Target, ...],
                 blocked: list[str], timeout_ms: int):
        self.page, self.context, self.base, self.targets = page, context, base, targets
        self.blocked, self.timeout_ms = blocked, timeout_ms

    def _locator(self, target: Target):
        return self.page.get_by_role(target.role, name=target.name, exact=True)

    def inspect(self) -> PageState:
        if self.blocked:
            raise BrowserError("network_request_blocked")
        if len(self.context.pages) != 1:
            raise BrowserError("unexpected_page")
        controls = []
        for target in self.targets:
            locator = self._locator(target)
            count = locator.count()
            ready = count == 1 and locator.is_visible() and locator.is_enabled()
            value = locator.input_value() if count == 1 and "fill" in target.verbs else None
            controls.append(Control(target.target_id, count, ready, value))
        return PageState(self.page.url, self.page.title(),
                         self.page.locator("body").aria_snapshot(), tuple(controls))

    def perform(self, target: Target, action: BrowserAction) -> None:
        from playwright.sync_api import Error, TimeoutError
        locator = self._locator(target)
        if locator.count() != 1:
            raise BrowserError("target_changed_before_action")
        try:
            if action.kind == "fill":
                locator.fill(action.value, timeout=self.timeout_ms)
            elif action.kind == "click":
                locator.click(timeout=self.timeout_ms)
            else:
                raise BrowserError("action_not_allowed")
            self.page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        except TimeoutError as exc:
            raise BrowserError("action_timeout_outcome_unknown") from exc
        except Error as exc:
            raise BrowserError("browser_action_failed_outcome_unknown") from exc


@contextmanager
def fixture_browser(base: str, targets: tuple[Target, ...], *,
                    executable_path: str | None = None, timeout_ms: int = 3000):
    """New ephemeral context and a dedicated browser, closed on every path.

    executable_path is trusted host configuration (e.g. test environment),
    never sourced from the page or the model. No OS sandbox guarantee is made.
    """
    scheme, host, _ = origin(base)
    if scheme != "http" or host != "127.0.0.1" or urlsplit(base).path not in {"", "/"}:
        raise BrowserError("fixture_requires_loopback_origin")
    if type(timeout_ms) is not int or timeout_ms < 1:
        raise ValueError("timeout_ms must be positive")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Install .[browser] and run: python -m playwright install chromium") from exc
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=executable_path)
        try:
            context = browser.new_context(service_workers="block", accept_downloads=False,
                                          permissions=[], viewport={"width": 1000, "height": 700})
            try:
                blocked: list[str] = []
                page = context.new_page()
                page.set_default_timeout(timeout_ms)

                def gate(route):
                    req = route.request
                    try:
                        same_page = req.frame.page == page
                    except Exception:
                        same_page = False
                    if same_page and request_allowed(base, req.url, req.method):
                        route.continue_()
                    else:
                        blocked.append("blocked_request")
                        route.abort()

                context.route("**/*", gate)
                def block_socket(ws):
                    blocked.append("blocked_websocket")
                    ws.close()
                context.route_web_socket("**/*", block_socket)
                page.on("dialog", lambda dialog: dialog.dismiss())
                page.goto(base + "/", wait_until="domcontentloaded", timeout=timeout_ms)
                yield PlaywrightBrowserPort(page, context, base, targets, blocked, timeout_ms)
            finally:
                context.close()
        finally:
            browser.close()
