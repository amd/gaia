# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GuideToolsMixin — show a recipe, workout, how-to or checklist as a card.

Each tool returns a ``guide`` render card (``docs/spec/agent-ui-query-sse-contract.md``
§4.3) that the Agent UI draws from the tool result, so the model's reply only
has to add a tip or two.
"""

from typing import Any, Dict, List, Literal, NamedTuple, Optional

GuideType = Literal["recipe", "workout", "howto", "checklist"]

MAX_TITLE_CHARS = 120
MAX_LINE_CHARS = 500
MAX_ITEMS = 100
MAX_STEPS = 60
MIN_SERVINGS = 1
MAX_SERVINGS = 99


class _GuideShape(NamedTuple):
    """How one guide type's tool arguments map onto the card's two lists."""

    tool: str
    items_arg: str
    # None when the type has no steps (a checklist).
    steps_arg: Optional[str]


_SHAPES: Dict[str, _GuideShape] = {
    "recipe": _GuideShape("show_recipe", "ingredients", "steps"),
    "workout": _GuideShape("show_workout", "equipment", "exercises"),
    "howto": _GuideShape("show_how_to", "materials", "steps"),
    "checklist": _GuideShape("show_checklist", "items", None),
}

#: The tools this mixin registers, in registration order.
GUIDE_TOOL_NAMES = tuple(shape.tool for shape in _SHAPES.values())


def _error(message: str) -> Dict[str, Any]:
    return {"ok": False, "error": message}


def _check_lines(tool: str, arg: str, lines: Any, cap: int) -> Optional[str]:
    """What is wrong with one list argument, or None when it is valid."""
    if not isinstance(lines, list):
        return (
            f"{tool}: {arg} must be a list of strings, one line per entry; got "
            f"{type(lines).__name__}. Call {tool} again with {arg} as a list."
        )
    if len(lines) > cap:
        return (
            f"{tool}: {arg} has {len(lines)} lines and the limit is {cap}. "
            f"Combine or drop lines so at most {cap} remain."
        )
    for i, line in enumerate(lines):
        if not isinstance(line, str):
            return (
                f"{tool}: {arg}[{i}] is {type(line).__name__}, not a string. "
                f"Write every entry of {arg} as text."
            )
        if not line.strip():
            return f"{tool}: {arg}[{i}] is empty. Remove it or write the line out."
        if len(line) > MAX_LINE_CHARS:
            return (
                f"{tool}: {arg}[{i}] is {len(line)} characters and the limit is "
                f"{MAX_LINE_CHARS}. Split it into shorter lines."
            )
    return None


_SHOWN_MESSAGE = (
    "The card is now on the user's screen with every line. Reply with one or "
    "two short sentences of tips. Do not repeat the list or the steps."
)


class GuideToolsMixin:
    """
    Mixin providing guide-card tools.

    Tools provided:
    - show_recipe: A recipe with servings, ingredients and steps
    - show_workout: A workout with exercises and equipment
    - show_how_to: Repair, install or setup instructions with materials
    - show_checklist: A shopping, packing or to-do list

    The tools only validate and package what the model sends. A bad argument
    comes back as ``{"ok": False, "error": ...}`` naming the fix, so the model
    can call again.
    """

    def register_guide_tools(self) -> None:
        """Register the guide tools into _TOOL_REGISTRY."""
        from gaia.agents.base.tools import tool

        @tool(display_label="Showing recipe")
        def show_recipe(
            title: str,
            servings: int,
            ingredients: List[str],
            steps: List[str],
            description: str = "",
            notes: str = "",
        ) -> Dict[str, Any]:
            """Show a recipe card when the user asks for a recipe or how to cook or bake a dish.

            The card shows everything. After calling, reply with one or two short sentences of tips. Do not repeat the list or steps.

            Args:
                title: The dish, e.g. "Banana bread".
                servings: How many people it serves, 1 to 99, e.g. 4.
                ingredients: One ingredient per line, amount and unit first, e.g. "2 cups flour", "1 whole chicken (4 lb)", "salt to taste". Write the full recipe yourself; do not ask for ingredients.
                steps: One step per line with durations inline, e.g. "Simmer 15 minutes". A step may start with a short title: "Make the dough: mix flour and water".
                description: Optional one-sentence intro.
                notes: Optional tips, swaps or storage advice.
            """
            return self._guide_card(
                "recipe",
                title=title,
                items=ingredients,
                steps=steps,
                description=description,
                notes=notes,
                servings=servings,
            )

        @tool(display_label="Showing workout")
        def show_workout(
            title: str,
            exercises: List[str],
            equipment: Optional[List[str]] = None,
            description: str = "",
            notes: str = "",
        ) -> Dict[str, Any]:
            """Show a workout card when the user asks for a workout, exercise routine or training plan.

            The card shows everything. After calling, reply with one or two short sentences of tips. Do not repeat the list or steps.

            Args:
                title: The workout, e.g. "20-minute upper body".
                exercises: One exercise per line, e.g. "Push-ups: 3 sets of 12, rest 45 seconds".
                equipment: Optional, one item per line, e.g. "Resistance band".
                description: Optional one-sentence intro.
                notes: Optional form cues or safety advice.
            """
            return self._guide_card(
                "workout",
                title=title,
                items=[] if equipment is None else equipment,
                steps=exercises,
                description=description,
                notes=notes,
            )

        @tool(display_label="Showing how-to")
        def show_how_to(
            title: str,
            steps: List[str],
            materials: Optional[List[str]] = None,
            description: str = "",
            notes: str = "",
        ) -> Dict[str, Any]:
            """Show step-by-step instructions when the user asks how to fix, repair, install or set up something.

            The card shows everything. After calling, reply with one or two short sentences of tips. Do not repeat the list or steps.

            Args:
                title: The task, e.g. "Replace a bike inner tube".
                steps: One step per line, e.g. "Remove the wheel: open the brake and undo the axle nut".
                materials: Optional tools and parts, one per line, e.g. "Tire levers".
                description: Optional one-sentence intro.
                notes: Optional warnings or tips.
            """
            return self._guide_card(
                "howto",
                title=title,
                items=[] if materials is None else materials,
                steps=steps,
                description=description,
                notes=notes,
            )

        @tool(display_label="Showing checklist")
        def show_checklist(
            title: str,
            items: List[str],
            description: str = "",
        ) -> Dict[str, Any]:
            """Show a checklist card when the user asks for a shopping list, packing list or to-do list.

            The card shows everything. After calling, reply with one or two short sentences of tips. Do not repeat the list or steps.

            Args:
                title: The list, e.g. "Weekend camping".
                items: One entry per line, e.g. "Sleeping bag".
                description: Optional one-sentence intro.
            """
            return self._guide_card(
                "checklist",
                title=title,
                items=items,
                steps=[],
                description=description,
            )

    def _guide_card(
        self,
        guide_type: GuideType,
        *,
        title: Any,
        items: Any,
        steps: Any,
        description: Any = "",
        notes: Any = "",
        servings: Any = None,
    ) -> Dict[str, Any]:
        """Validate one guide and wrap it as a ``guide`` render card."""
        shape = _SHAPES[guide_type]
        tool = shape.tool

        if (
            not isinstance(title, str)
            or not title.strip()
            or len(title) > MAX_TITLE_CHARS
        ):
            got = (
                f"{len(title)} characters"
                if isinstance(title, str)
                else type(title).__name__
            )
            return _error(
                f"{tool} needs a title of 1 to {MAX_TITLE_CHARS} characters; got "
                f"{got}. Send a short name."
            )
        for name, value in (("description", description), ("notes", notes)):
            if not isinstance(value, str):
                return _error(
                    f"{tool}: {name} must be text; got {type(value).__name__}. "
                    'Send a string, or "" to leave it out.'
                )
        if guide_type == "recipe" and (
            isinstance(servings, bool)
            or not isinstance(servings, int)
            or not MIN_SERVINGS <= servings <= MAX_SERVINGS
        ):
            return _error(
                f"show_recipe needs servings as a whole number from {MIN_SERVINGS} "
                f"to {MAX_SERVINGS}; got {servings!r}. Send e.g. servings=4."
            )

        problem = _check_lines(tool, shape.items_arg, items, MAX_ITEMS)
        if problem:
            return _error(problem)
        if shape.steps_arg is not None:
            problem = _check_lines(tool, shape.steps_arg, steps, MAX_STEPS)
            if problem:
                return _error(problem)
            if not steps:
                return _error(
                    f"{tool} needs at least one entry in {shape.steps_arg}. "
                    "Send one line per step."
                )
        elif not items:
            return _error(
                f"{tool} needs at least one entry in {shape.items_arg}. "
                "Send one line per entry."
            )

        return {
            "ok": True,
            # Read by the model right before it answers; the docstring alone
            # did not stop a small model re-typing the card under it.
            "message": _SHOWN_MESSAGE,
            "data": {
                "kind": "guide",
                "guideType": guide_type,
                "title": title,
                "description": description,
                "notes": notes,
                "servings": servings if guide_type == "recipe" else None,
                "items": list(items),
                "steps": list(steps),
            },
        }
