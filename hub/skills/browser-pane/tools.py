# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tools provided by the ``browser-pane`` starter skill: look at and drive the page
in GAIA desktop's browser pane, and run a project's dev server there.

They live in a skill so a chat pays for their schemas only once it loads them.
The pane, its site approvals and the dev-server supervisor belong to the agent's
:class:`~gaia.agents.tools.browser_use_tools.BrowserUseToolsMixin`, which also
decides when a call asks: reading a site not yet approved, pressing a control
that may not be undoable, starting a dev server. ``browser_eval`` asks on every
call, which its manifest marks ``requires_confirmation``.

A :class:`_PanePreflight` runs before any prompt. It refuses on an agent whose
gate would not ask before a pane call, in a chat with no pane, and for a script
``browser_eval`` would refuse anyway, so nobody approves a call that cannot run.

A skill tool has no ``self``. Each call reaches the agent running it through
:func:`gaia.skills.context.current`.
"""

from typing import Any, Dict, List, Optional

from gaia.agents.base.tools import tool
from gaia.agents.tools.browser_use_tools import BrowserUseToolsMixin
from gaia.browser.dev_servers import MAX_READY_TIMEOUT_S
from gaia.logger import get_logger
from gaia.skills.context import current

logger = get_logger(__name__)

#: The mixin hook that decides when a pane call asks.
_PANE_GATE_HOOK = "browser_call_needs_confirmation"

_NO_BROWSER_MESSAGE = (
    "Not run: this agent has no browser tools, so it can't drive the browser "
    "pane. Use the GAIA agent in GAIA desktop."
)

_NOT_GATED_MESSAGE = (
    "Not run: this agent does not ask the user before reading a site or acting "
    "in the browser pane, and these tools need it to. Use the GAIA agent, and "
    "tell the user nothing was done in the pane."
)


def _browser() -> BrowserUseToolsMixin:
    """The agent running this tool call, which must hold the browser tools."""
    agent = current().agent
    if not isinstance(agent, BrowserUseToolsMixin):
        raise RuntimeError(_NO_BROWSER_MESSAGE)
    return agent


class _PanePreflight:
    """Refuses a pane call that can't run, before anyone is asked."""

    def __init__(self, tool_name: str):
        self._tool_name = tool_name

    def __call__(self, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            agent = _browser()
        except RuntimeError as exc:
            return {"status": "error", "error": str(exc)}
        hooks = getattr(agent, "confirmation_hooks", None)
        gated = getattr(agent, "_tool_requires_confirmation", None)
        if not callable(hooks) or _PANE_GATE_HOOK not in hooks() or not callable(gated):
            # Fail closed: an agent that would not ask must not drive the pane.
            logger.warning("browser-pane: %s refused, no pane gate", self._tool_name)
            return {"status": "error", "error": _NOT_GATED_MESSAGE}
        refused = agent.pane_tool_refusal(self._tool_name, args)
        if refused:
            return {"status": "error", "error": refused}
        return None


def _run(tool_name: str, **kwargs: Any) -> Any:
    try:
        return _browser().run_pane_tool(tool_name, **kwargs)
    except RuntimeError as exc:
        return f"Error: {exc}"


@tool(atomic=True, preflight=_PanePreflight("browser_screenshot"))
def browser_screenshot(ref: str = "") -> dict:
    """Take a picture of the page open in the browser pane.

    Use to check how a page looks: layout, colors, images. To read its text
    or find its buttons, use browser_snapshot instead.

    Returns image_path. To see what the picture shows, call analyze_image
    with that image_path.

    Args:
        ref: Element ref from browser_snapshot (e.g. 'g2e5') to picture only
             that element. Leave empty for the whole visible page.
    """
    result = _run("browser_screenshot", ref=ref)
    return result if isinstance(result, dict) else {"status": "error", "error": result}


@tool(atomic=True, preflight=_PanePreflight("browser_logs"))
def browser_logs(
    kind: str = "console",
    errors_only: bool = False,
    contains: str = "",
    new_only: bool = False,
) -> str:
    """Read the browser pane page's console messages or network requests.

    Use to find out why a page misbehaves: script errors, failed requests,
    bad status codes.

    Args:
        kind: 'console' for messages and script errors, or 'network' for
              requests and their status codes
        errors_only: Only errors: console errors, failed requests, and
                     responses with status 400 or higher
        contains: Only lines containing this text (case-insensitive)
        new_only: Only what arrived since your last browser_logs call
    """
    return _run(
        "browser_logs",
        kind=kind,
        errors_only=errors_only,
        contains=contains,
        new_only=new_only,
    )


@tool(atomic=True, preflight=_PanePreflight("browser_press"))
def browser_press(key: str, ref: str = "") -> str:
    """Press one key on the page in the browser pane.

    Use Escape to close a dialog or menu, Enter to submit, Tab to go to the
    next field, PageDown or End to scroll down.

    Args:
        key: One of Enter, Escape, Tab, Backspace, Space, PageDown, PageUp,
             Home, End, ArrowUp, ArrowDown, ArrowLeft, ArrowRight
        ref: Element ref to press the key on (e.g. 'g2e5'). Leave empty for
             whatever has focus.
    """
    return _run("browser_press", key=key, ref=ref)


@tool(atomic=True, preflight=_PanePreflight("browser_viewport"))
def browser_viewport(
    preset: str = "", width: int = 0, height: int = 0, color_scheme: str = ""
) -> str:
    """Show the browser pane's page at a phone, tablet or desktop size, or in dark mode.

    Use to check how a page looks on other screens. If the user chose a size
    in the pane's Responsive menu, theirs stays.

    Args:
        preset: 'mobile', 'tablet', 'desktop', or 'fill' to fit the pane again
        width: Exact width in pixels instead of a preset (give height too)
        height: Exact height in pixels instead of a preset
        color_scheme: 'dark' or 'light'. Leave empty for the system setting.
    """
    return _run(
        "browser_viewport",
        preset=preset,
        width=width,
        height=height,
        color_scheme=color_scheme,
    )


@tool(atomic=True, preflight=_PanePreflight("browser_tabs"))
def browser_tabs(switch_to: str = "") -> str:
    """List the tabs open in the browser pane, or switch to one.

    The browser tools read and act in the tab that is showing.

    Args:
        switch_to: A tab id from the list (e.g. 'tab-2') to show that tab.
                   Leave empty to only list them.
    """
    return _run("browser_tabs", switch_to=switch_to)


@tool(atomic=True, preflight=_PanePreflight("browser_dialog"))
def browser_dialog(accept: bool = True) -> str:
    """Answer the box a page in the browser pane is showing (alert, confirm, or "leave this page?").

    While a box is open the page is frozen and other browser tools say so.
    Answer it, then go on.

    Args:
        accept: True presses OK (or Leave); False presses Cancel (or Stay)
    """
    return _run("browser_dialog", accept=accept)


@tool(atomic=True, preflight=_PanePreflight("browser_eval"))
def browser_eval(expression: str) -> str:
    """Run a short JavaScript expression in the browser pane's page and get its value as JSON.

    Only for the user's own app on localhost. Use it to check what the page
    does not show: localStorage, app state, a computed style. To read the
    page's text or buttons, use browser_snapshot instead. Asks the user first.

    Args:
        expression: One JavaScript expression, like
            localStorage.getItem('cart') or document.title
    """
    return _run("browser_eval", expression=expression)


@tool(atomic=True, timeout=300, preflight=_PanePreflight("browser_batch"))
def browser_batch(steps: List[dict]) -> str:
    """Run several browser steps in one call, in order, in the browser pane.

    Use for a known sequence, like filling in a form. Steps can use
    browser_open, browser_click, browser_type, browser_press, browser_back,
    browser_snapshot and browser_screenshot. The batch stops at the first
    step that fails, and before any step that needs the user's approval:
    call that step on its own and GAIA asks. It also stops at a step that
    opens a box on the page (answer it with browser-pane/browser_dialog).

    Args:
        steps: Steps in order, each a tool name and its arguments, e.g.
            [{"tool": "browser_type", "ref": "g2e3", "text": "Ada"},
            {"tool": "browser_click", "ref": "g2e7"}]
    """
    return _run("browser_batch", steps=steps)


@tool(
    atomic=True,
    timeout=MAX_READY_TIMEOUT_S + 60,
    preflight=_PanePreflight("dev_server"),
)
def dev_server(
    action: str,
    name: str = "",
    project: str = "",
    errors_only: bool = False,
    contains: str = "",
) -> str:
    """Run a project's dev server and show it in the browser pane; or list, stop, or read its logs.

    Use this, not run_shell_command or a Python script, to start any web
    server: it keeps running after the reply. A folder of plain HTML/CSS/JS
    needs no setup: browser-pane/dev_server(action="start",
    project="<folder>"). An app with its own dev command needs
    <project>/.gaia/launch.json (the skill shows its format). Starting asks
    the user first. A server already running is reused.

    Args:
        action: 'start' (runs it and opens it in the pane), 'list', 'logs',
                or 'stop'
        name: The server's name in launch.json, like 'web'. Needed when it
              has more than one.
        project: The folder to serve, or the one holding .gaia/launch.json.
                 Leave empty for the current project.
        errors_only: For logs: only error lines
        contains: For logs: only lines containing this text
    """
    return _run(
        "dev_server",
        action=action,
        name=name,
        project=project,
        errors_only=errors_only,
        contains=contains,
    )
