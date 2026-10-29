# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CalculatorToolsMixin — show a tip or loan calculator as a card.

Small local models get arithmetic wrong, so the ``calculator`` render card
(``docs/spec/agent-ui-query-sse-contract.md`` §4.3) does the math in the Agent
UI. The model only supplies the starting numbers, which the person can change.
"""

import math
from typing import Any, Dict, Literal, NamedTuple, Optional

CalculatorType = Literal["tip", "loan"]

MAX_TITLE_CHARS = 120

#: The tools this mixin registers, in registration order.
CALCULATOR_TOOL_NAMES = ("show_tip_calculator", "show_loan_calculator")

_SHOWN_MESSAGE = (
    "The calculator is now on the user's screen and works out the numbers as "
    "they change them. Reply with one or two short sentences. Do not do the "
    "math in text or state the results."
)


class _Field(NamedTuple):
    """One numeric argument: its wire key, allowed range and an example."""

    wire: str
    low: float
    high: float
    example: str
    low_open: bool = False
    whole: bool = False


_FIELDS: Dict[str, Dict[str, _Field]] = {
    "show_tip_calculator": {
        "bill": _Field("bill", 0, 1e9, "84.5", low_open=True),
        "tip_percent": _Field("tipPercent", 0, 100, "18"),
        "people": _Field("people", 1, 50, "2", whole=True),
    },
    "show_loan_calculator": {
        "amount": _Field("amount", 0, 1e10, "350000", low_open=True),
        "annual_rate_percent": _Field("annualRatePercent", 0, 50, "6.5"),
        "years": _Field("years", 0, 50, "30", low_open=True),
        "down_payment": _Field("downPayment", 0, 1e10, "0"),
    },
}


def _error(message: str) -> Dict[str, Any]:
    return {"ok": False, "error": message}


def _check_field(tool: str, arg: str, field: _Field, value: Any) -> Optional[str]:
    """What is wrong with one numeric argument, or None when it is valid."""
    is_number = (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )
    if is_number and field.whole and value != int(value):
        is_number = False
    above_low = is_number and (
        value > field.low if field.low_open else value >= field.low
    )
    if above_low and value <= field.high:
        return None
    kind = "a whole number" if field.whole else "a number"
    floor = f"above {field.low:g}" if field.low_open else f"from {field.low:g}"
    return (
        f"{tool} needs {arg} as {kind} {floor} to {field.high:g}; got {value!r}. "
        f"Send e.g. {arg}={field.example}."
    )


def _calculator_card(
    calculator: CalculatorType, tool: str, title: Any, args: Dict[str, Any]
) -> Dict[str, Any]:
    """Validate one calculator's starting numbers and wrap them as a card."""
    if not isinstance(title, str) or len(title) > MAX_TITLE_CHARS:
        got = f"{len(title)} characters" if isinstance(title, str) else repr(title)
        return _error(
            f"{tool}: title must be text of at most {MAX_TITLE_CHARS} characters; "
            f'got {got}. Send a short name, or "" to leave it out.'
        )
    inputs: Dict[str, Any] = {}
    for arg, field in _FIELDS[tool].items():
        value = args[arg]
        problem = _check_field(tool, arg, field, value)
        if problem:
            return _error(problem)
        inputs[field.wire] = int(value) if field.whole else value
    if calculator == "loan" and inputs["downPayment"] >= inputs["amount"]:
        return _error(
            f"{tool} needs down_payment less than amount ({inputs['amount']:g}); "
            f"got {inputs['downPayment']:g}. Send the price as amount and the "
            "part paid up front as down_payment, or down_payment=0."
        )
    return {
        "ok": True,
        # Read by the model right before it answers, so it leaves the numbers
        # to the card instead of getting them wrong in text.
        "message": _SHOWN_MESSAGE,
        "data": {
            "kind": "calculator",
            "calculator": calculator,
            "title": title,
            "inputs": inputs,
        },
    }


class CalculatorToolsMixin:
    """
    Mixin providing calculator-card tools.

    Tools provided:
    - show_tip_calculator: Tip and per-person split for a bill
    - show_loan_calculator: Monthly payment, total paid and interest on a loan

    The tools only validate and package the starting numbers. A bad argument
    comes back as ``{"ok": False, "error": ...}`` naming the fix, so the model
    can call again.
    """

    def register_calculator_tools(self) -> None:
        """Register the calculator tools into _TOOL_REGISTRY."""
        from gaia.agents.base.tools import tool

        @tool(display_label="Showing tip calculator")
        def show_tip_calculator(
            bill: float,
            tip_percent: float = 18,
            people: int = 1,
            title: str = "",
        ) -> Dict[str, Any]:
            """Show a tip calculator when the user asks how much to tip, or to split the bill.

            The card does the math. After calling, reply with one or two short sentences. Do not do the math in text.

            Args:
                bill: The bill before tip, e.g. 84.50.
                tip_percent: Tip percentage, 0 to 100, e.g. 20.
                people: How many people split it, 1 to 50, e.g. 3.
                title: Optional name, e.g. "Dinner at Nopa".
            """
            return _calculator_card(
                "tip",
                "show_tip_calculator",
                title,
                {"bill": bill, "tip_percent": tip_percent, "people": people},
            )

        @tool(display_label="Showing loan calculator")
        def show_loan_calculator(
            amount: float,
            annual_rate_percent: float,
            years: float,
            down_payment: float = 0,
            title: str = "",
        ) -> Dict[str, Any]:
            """Show a loan calculator when the user asks about a mortgage, car loan, monthly payment or loan interest.

            The card does the math. After calling, reply with one or two short sentences. Do not do the math in text.

            Args:
                amount: The price or loan amount before any down payment, e.g. 350000.
                annual_rate_percent: Yearly interest rate in percent, 0 to 50, e.g. 6.5.
                years: Loan term in years, up to 50, e.g. 30.
                down_payment: Optional amount paid up front, less than amount, e.g. 70000.
                title: Optional name, e.g. "House on Elm Street".
            """
            return _calculator_card(
                "loan",
                "show_loan_calculator",
                title,
                {
                    "amount": amount,
                    "annual_rate_percent": annual_rate_percent,
                    "years": years,
                    "down_payment": down_payment,
                },
            )
