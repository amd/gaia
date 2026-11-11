---
name: guides
description: Show a recipe, workout, how-to, checklist or trip itinerary as an interactive card. Use for recipes and cooking or baking, workouts, fixing, installing or setting something up, shopping, packing or to-do lists, and travel plans.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    requirements:
      python: ">=3.10"
    tools:
      - name: show_recipe
        description: Show a recipe card with servings, ingredients and steps.
        parameters:
          title:
            type: string
            required: true
          servings:
            type: integer
            required: true
          ingredients:
            type: array
            required: true
          steps:
            type: array
            required: true
          description:
            type: string
            required: false
            default: ""
          notes:
            type: string
            required: false
            default: ""
        returns:
          type: object
        atomic: true
      - name: show_workout
        description: Show a workout card with exercises and optional equipment.
        parameters:
          title:
            type: string
            required: true
          exercises:
            type: array
            required: true
          equipment:
            type: array
            required: false
          description:
            type: string
            required: false
            default: ""
          notes:
            type: string
            required: false
            default: ""
        returns:
          type: object
        atomic: true
      - name: show_how_to
        description: Show step-by-step repair, install or setup instructions.
        parameters:
          title:
            type: string
            required: true
          steps:
            type: array
            required: true
          materials:
            type: array
            required: false
          description:
            type: string
            required: false
            default: ""
          notes:
            type: string
            required: false
            default: ""
        returns:
          type: object
        atomic: true
      - name: show_checklist
        description: Show a shopping, packing or to-do checklist card.
        parameters:
          title:
            type: string
            required: true
          items:
            type: array
            required: true
          description:
            type: string
            required: false
            default: ""
        returns:
          type: object
        atomic: true
      - name: show_itinerary
        description: Show a day-by-day trip itinerary with an optional packing list.
        parameters:
          title:
            type: string
            required: true
          days:
            type: array
            required: true
          packing:
            type: array
            required: false
          description:
            type: string
            required: false
            default: ""
          notes:
            type: string
            required: false
            default: ""
        returns:
          type: object
        atomic: true
    provenance:
      source: starter-pack
---

# Guides

Draws the answer as a `guide` card in the Agent UI and the TUI: a recipe the
user can scale and cook along with, a workout with timers, how-to steps to tick
off, a checklist, or a day-by-day itinerary. The card shows every line, so the
written reply stays short.

## Procedure

1. **Pick the tool that matches the request.**
   - A dish to cook or bake → `guides/show_recipe` (title, servings,
     ingredients, steps).
   - An exercise routine or training plan → `guides/show_workout`.
   - Fixing, repairing, installing or setting something up →
     `guides/show_how_to`.
   - A shopping, packing or to-do list → `guides/show_checklist`.
   - A trip plan or day-by-day schedule → `guides/show_itinerary`, one line per
     day, the day and place first.
2. **Write the whole guide yourself.** One line per ingredient, item or step,
   amounts and durations inline ("2 cups flour", "Simmer 15 minutes"). Do not
   ask the user for the ingredients or the steps of a well-known dish or task.
3. **If the call returns `"ok": false`, fix what its `error` names and call the
   same tool again.** Never paste the guide as text instead — a failed call
   shows no card.
4. **After the card is shown, reply with one or two short sentences of tips.**
   Do not repeat the list or the steps: the user already sees them.

## Notes

- Lines are shown as plain text, never as HTML or Markdown, so write words, not
  markup.
- Limits: a title up to 120 characters, up to 100 items, 60 steps, and 500
  characters per line. Servings run from 1 to 99.
