# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deck blueprints, as data.

The source of truth for ``templates/blueprints/*.json``: those files are
exported from this list by ``util/gen_slide_deck_assets.py`` for people and
other tools to read, and a unit test keeps the two identical. The skill reads
this module, never the files, so its code touches no file system.
"""

# fmt: off
BLUEPRINTS = [
    {
        "name": "decision",
        "aliases": [
            "comparison",
            "options",
            "decision_paper",
            "recommendation",
            "trade_off"
        ],
        "title": "Decision / comparison",
        "description": "Comparison of options leading to a decision: recommendation first, the options against criteria, trade-offs, risks and the decision needed.",
        "default_slides": 6,
        "min_slides": 1,
        "max_slides": 10,
        "one_slide": "options",
        "storyline": "Recommendation first, then the evidence: the same criteria for every option, scored the same way. End with exactly what you need decided and by when.",
        "slots": [
            {
                "id": "title",
                "type": "title",
                "priority": 1,
                "title": "<Decision>: <recommended option>",
                "guidance": "Decision topic, audience, date."
            },
            {
                "id": "recommendation",
                "type": "bullets",
                "priority": 1,
                "title": "<We recommend X because Y>",
                "guidance": "The recommendation, its main reason, cost and timing.",
                "kind": "executive_summary"
            },
            {
                "id": "options",
                "type": "table",
                "priority": 1,
                "title": "<How the options compare on what matters>",
                "guidance": "Options as columns, 3-6 criteria as rows, with values not adjectives."
            },
            {
                "id": "criteria_chart",
                "type": "chart",
                "priority": 3,
                "title": "<The criterion that decides it>",
                "guidance": "The deciding criterion per option as sorted bars.",
                "repeat": 3,
                "grow": 1.5,
                "spec": {
                    "chart_type": "bar",
                    "intent": "compare"
                }
            },
            {
                "id": "tradeoffs",
                "type": "two_column",
                "priority": 2,
                "title": "<What we gain and what we give up>",
                "guidance": "Left: gains of the recommendation; right: costs and what we forgo."
            },
            {
                "id": "risks",
                "type": "bullets",
                "priority": 3,
                "title": "<What could go wrong and how we would know>",
                "guidance": "Top risks with an early signal and a mitigation each."
            },
            {
                "id": "decision_needed",
                "type": "bullets",
                "priority": 1,
                "title": "<The decision we need today>",
                "guidance": "The exact decision, by whom, by when, and what happens next."
            },
            {
                "id": "next_steps",
                "type": "table",
                "title": "Next steps: who does what by when",
                "guidance": "Columns Action | Owner | Due. One row per commitment; a named owner and a date on every row.",
                "spec": {
                    "columns": [
                        "Action",
                        "Owner",
                        "Due"
                    ]
                },
                "priority": 3
            }
        ]
    },
    {
        "name": "executive_summary",
        "aliases": [
            "briefing",
            "exec_brief",
            "board_update",
            "memo_deck"
        ],
        "title": "Executive summary / briefing",
        "description": "Short briefing for senior readers: the answer first (situation, complication, resolution), the evidence behind it, and the decision or next step.",
        "default_slides": 5,
        "min_slides": 1,
        "max_slides": 11,
        "one_slide": "exec_summary",
        "storyline": "SCQA: situation, complication, question, answer — the answer goes on the first content slide. Keep body text to about 30 words a slide.",
        "slots": [
            {
                "id": "title",
                "type": "title",
                "priority": 1,
                "title": "<Topic>: <the answer in a few words>",
                "guidance": "Title, audience and date."
            },
            {
                "id": "exec_summary",
                "type": "bullets",
                "priority": 1,
                "title": "<The answer as one sentence>",
                "guidance": "Situation, complication, answer, in 3-4 lines with the key numbers.",
                "kind": "executive_summary"
            },
            {
                "id": "evidence",
                "type": "chart",
                "priority": 2,
                "title": "<The fact that proves the answer>",
                "guidance": "One chart per supporting fact; source and period on each.",
                "repeat": 4,
                "grow": 1.0,
                "spec": {
                    "chart_type": "auto"
                }
            },
            {
                "id": "recommendation",
                "type": "bullets",
                "priority": 2,
                "title": "<What we recommend and what it achieves>",
                "guidance": "The recommendation, its cost and its payoff."
            },
            {
                "id": "next_steps",
                "type": "table",
                "title": "Next steps: who does what by when",
                "guidance": "Columns Action | Owner | Due. One row per commitment; a named owner and a date on every row.",
                "spec": {
                    "columns": [
                        "Action",
                        "Owner",
                        "Due"
                    ]
                },
                "priority": 3
            },
            {
                "id": "appendix_detail",
                "type": "table",
                "priority": 6,
                "title": "<Supporting detail>",
                "guidance": "Backup tables.",
                "repeat": 3,
                "min_deck": 8
            }
        ]
    },
    {
        "name": "one_slide_framework",
        "aliases": [
            "framework",
            "one_pager",
            "canvas",
            "model",
            "2x2",
            "one_slide"
        ],
        "title": "One-slide framework",
        "description": "A single framework slide (2x2 matrix, pyramid, cycle, process, layered stack or canvas grid), optionally with a cover, an explanation and an example.",
        "default_slides": 1,
        "min_slides": 1,
        "max_slides": 4,
        "one_slide": "framework",
        "storyline": "The framework carries the message: label every box with a short noun phrase and one line of explanation; the title says what the framework shows.",
        "variants": {
            "2x2": {
                "framework": {
                    "spec": {
                        "diagram": "matrix"
                    },
                    "guidance": "Four quadrants (top-left, top-right, bottom-left, bottom-right) with x_axis and y_axis labels; optional points with x, y in 0..1."
                }
            },
            "pyramid": {
                "framework": {
                    "spec": {
                        "diagram": "pyramid"
                    },
                    "guidance": "3-5 levels, top first, each with a one-line description."
                }
            },
            "cycle": {
                "framework": {
                    "spec": {
                        "diagram": "cycle"
                    },
                    "guidance": "3-8 stages in order; optional centre label."
                }
            },
            "process": {
                "framework": {
                    "spec": {
                        "diagram": "process"
                    },
                    "guidance": "3-6 steps, each a verb phrase with one line of detail."
                }
            },
            "stack": {
                "framework": {
                    "spec": {
                        "diagram": "stack"
                    },
                    "guidance": "Layers from top to bottom, each with a one-line role."
                }
            },
            "canvas": {
                "framework": {
                    "spec": {
                        "diagram": "grid"
                    },
                    "guidance": "Cells with row/col/rowspan/colspan for a canvas; a title and 1-3 bullets per cell."
                }
            }
        },
        "slots": [
            {
                "id": "title",
                "type": "title",
                "priority": 3,
                "title": "<Framework name>",
                "guidance": "Cover slide."
            },
            {
                "id": "framework",
                "type": "diagram",
                "priority": 1,
                "title": "<What the framework shows, as a sentence>",
                "guidance": "The framework itself.",
                "spec": {
                    "diagram": "matrix"
                }
            },
            {
                "id": "explanation",
                "type": "bullets",
                "priority": 2,
                "title": "<How to use it>",
                "guidance": "How to read and apply the framework, 3-4 points."
            },
            {
                "id": "example",
                "type": "two_column",
                "priority": 4,
                "title": "<The framework applied to a case>",
                "guidance": "Left: the case; right: where it lands and what follows."
            }
        ]
    },
    {
        "name": "pitch",
        "aliases": [
            "pitch_deck",
            "investor",
            "proposal",
            "sales_pitch"
        ],
        "title": "Pitch / proposal",
        "description": "Pitch or proposal deck: problem, solution, market, traction, model, competition, team, financials and the ask.",
        "default_slides": 10,
        "min_slides": 1,
        "max_slides": 13,
        "one_slide": "solution",
        "storyline": "Problem, then why now, then the solution and proof it works; end on a specific ask. Each slide makes one claim the next slide builds on.",
        "slots": [
            {
                "id": "title",
                "type": "title",
                "priority": 1,
                "title": "<Name>: <one-line value proposition>",
                "guidance": "Name, tagline, presenter, date."
            },
            {
                "id": "why_now",
                "type": "bullets",
                "priority": 4,
                "title": "<Why now>",
                "guidance": "The change (technology, regulation, behaviour) that makes this possible now."
            },
            {
                "id": "problem",
                "type": "bullets",
                "priority": 1,
                "title": "<The problem, stated as the customer feels it>",
                "guidance": "Who has the problem, how often, what it costs them; one number."
            },
            {
                "id": "solution",
                "type": "bullets",
                "priority": 1,
                "title": "<How we solve it, in one sentence>",
                "guidance": "What it does and the outcome for the customer; no feature list."
            },
            {
                "id": "market",
                "type": "kpi",
                "priority": 2,
                "title": "<How big the opportunity is>",
                "guidance": "2-3 metrics such as total, serviceable and obtainable market, with sources."
            },
            {
                "id": "product",
                "type": "diagram",
                "priority": 3,
                "title": "<How it works in three steps>",
                "guidance": "A 3-5 step process or a product picture.",
                "spec": {
                    "diagram": "process"
                }
            },
            {
                "id": "traction",
                "type": "chart",
                "priority": 2,
                "title": "<Proof that it is working>",
                "guidance": "Growth in users/revenue/pilots over time; source and period.",
                "repeat": 2,
                "spec": {
                    "chart_type": "auto",
                    "intent": "trend"
                }
            },
            {
                "id": "business_model",
                "type": "diagram",
                "priority": 3,
                "title": "<How it makes money>",
                "guidance": "Pricing and revenue streams as 2-4 boxes.",
                "spec": {
                    "diagram": "boxes"
                }
            },
            {
                "id": "competition",
                "type": "table",
                "priority": 3,
                "title": "<Why we win against the alternatives>",
                "guidance": "Alternatives as columns, 3-5 criteria as rows."
            },
            {
                "id": "team",
                "type": "diagram",
                "priority": 4,
                "title": "<Why this team will deliver>",
                "guidance": "2-4 people with one relevant credential each.",
                "spec": {
                    "diagram": "boxes"
                }
            },
            {
                "id": "financials",
                "type": "chart",
                "priority": 3,
                "title": "<Where the plan gets in 3 years>",
                "guidance": "Revenue and cost (or margin) by year; label projections as such.",
                "spec": {
                    "chart_type": "column"
                }
            },
            {
                "id": "ask",
                "type": "kpi",
                "priority": 1,
                "title": "<What we are asking for and what it buys>",
                "guidance": "The amount or decision, what it funds, and the milestone it reaches."
            }
        ]
    },
    {
        "name": "project_status",
        "aliases": [
            "status_update",
            "status_report",
            "steering_committee",
            "programme_update"
        ],
        "title": "Project status update",
        "description": "Project or programme status: overall status (schedule, budget, scope), milestones, progress, risks and issues, decisions needed, next steps.",
        "default_slides": 6,
        "min_slides": 1,
        "max_slides": 9,
        "one_slide": "status_summary",
        "storyline": "Lead with the overall status and what changed since last time; end with the decisions you need. State status in words (On track / At risk / Late), not only colours.",
        "slots": [
            {
                "id": "title",
                "type": "title",
                "priority": 1,
                "title": "<Project> status: <date>",
                "guidance": "Project, reporting period, owner."
            },
            {
                "id": "status_summary",
                "type": "kpi",
                "priority": 1,
                "title": "<Overall status in one sentence>",
                "guidance": "Tiles for Overall, Schedule, Budget, Scope with a word status and the change since last report.",
                "kind": "executive_summary"
            },
            {
                "id": "milestones",
                "type": "timeline",
                "priority": 2,
                "title": "<Where the milestones stand>",
                "guidance": "Milestones with dates; mark done and late ones in the label."
            },
            {
                "id": "progress",
                "type": "chart",
                "priority": 3,
                "title": "<Progress against plan>",
                "guidance": "Planned vs actual (completion, burn-up or spend) over time.",
                "repeat": 3,
                "grow": 1.5,
                "spec": {
                    "chart_type": "line",
                    "intent": "trend"
                }
            },
            {
                "id": "risks",
                "type": "table",
                "priority": 2,
                "title": "<The risks and issues that need attention>",
                "guidance": "Risk | Impact | Owner | Mitigation; the top 3-5 only."
            },
            {
                "id": "decisions",
                "type": "bullets",
                "priority": 3,
                "title": "<Decisions we need from you>",
                "guidance": "Each decision, the options and the date it is needed by.",
                "merge_into": "status_summary"
            },
            {
                "id": "next_steps",
                "type": "table",
                "title": "Next steps: who does what by when",
                "guidance": "Columns Action | Owner | Due. One row per commitment; a named owner and a date on every row.",
                "spec": {
                    "columns": [
                        "Action",
                        "Owner",
                        "Due"
                    ]
                },
                "priority": 2
            }
        ]
    },
    {
        "name": "quarterly_review",
        "aliases": [
            "qbr",
            "business_review",
            "monthly_review",
            "performance_review"
        ],
        "title": "Quarterly / business review",
        "description": "Periodic performance review (QBR, monthly or annual business review): results against plan and prior periods, drivers, risks, next steps.",
        "default_slides": 12,
        "min_slides": 1,
        "max_slides": 24,
        "one_slide": "exec_summary",
        "storyline": "Answer first: the exec summary states the 3-5 takeaways; each later section proves one. Every chart compares against plan or the prior period and names its source and period.",
        "slots": [
            {
                "id": "title",
                "type": "title",
                "priority": 1,
                "title": "<Team or company> <period> review",
                "guidance": "Title, subtitle with the period, presenter and date."
            },
            {
                "id": "agenda",
                "type": "agenda",
                "priority": 4,
                "title": "Agenda",
                "guidance": "The section names, in order.",
                "min_deck": 8
            },
            {
                "id": "exec_summary",
                "type": "bullets",
                "priority": 1,
                "title": "<The period's headline result as a sentence>",
                "guidance": "3-5 takeaways, one per later section, each with its number and change ('Revenue $12.4M, +12% vs Q2').",
                "kind": "executive_summary"
            },
            {
                "id": "kpis",
                "type": "kpi",
                "priority": 2,
                "title": "<What the headline metrics say>",
                "guidance": "1-4 headline metrics with the change vs the prior period; source and period on the slide."
            },
            {
                "id": "section_results",
                "type": "section",
                "priority": 6,
                "title": "Results",
                "guidance": "Section divider.",
                "min_deck": 11
            },
            {
                "id": "trend",
                "type": "chart",
                "priority": 2,
                "title": "<How the key metric moved over the periods>",
                "guidance": "One metric over 4-8 periods (line for many periods, columns for few); the title states the change; highlight the series that carries the message.",
                "repeat": 6,
                "grow": 1.0,
                "spec": {
                    "chart_type": "auto",
                    "intent": "trend"
                }
            },
            {
                "id": "breakdown",
                "type": "chart",
                "priority": 3,
                "title": "<Which segment drove the change>",
                "guidance": "The metric by region/product/segment, bars sorted largest first.",
                "repeat": 3,
                "grow": 2.0,
                "spec": {
                    "chart_type": "bar",
                    "intent": "ranking"
                }
            },
            {
                "id": "variance",
                "type": "table",
                "priority": 3,
                "title": "<Where results beat or missed plan>",
                "guidance": "Plan vs actual vs variance per line item; at most ~7 rows x 5 columns; numbers right-aligned."
            },
            {
                "id": "section_outlook",
                "type": "section",
                "priority": 6,
                "title": "Outlook",
                "guidance": "Section divider.",
                "min_deck": 11
            },
            {
                "id": "risks",
                "type": "two_column",
                "priority": 3,
                "title": "<The risks that could change the outlook>",
                "guidance": "Left: risks with likelihood/impact; right: mitigation and owner.",
                "merge_into": "exec_summary"
            },
            {
                "id": "outlook",
                "type": "bullets",
                "priority": 4,
                "title": "<What we expect next period and why>",
                "guidance": "Forecast or targets for the next period, with the assumption behind each.",
                "merge_into": "exec_summary"
            },
            {
                "id": "next_steps",
                "type": "table",
                "title": "Next steps: who does what by when",
                "guidance": "Columns Action | Owner | Due. One row per commitment; a named owner and a date on every row.",
                "spec": {
                    "columns": [
                        "Action",
                        "Owner",
                        "Due"
                    ]
                },
                "priority": 2
            },
            {
                "id": "appendix",
                "type": "section",
                "priority": 7,
                "title": "Appendix",
                "guidance": "Section divider before the backup slides.",
                "min_deck": 14
            },
            {
                "id": "appendix_detail",
                "type": "table",
                "priority": 7,
                "title": "<Supporting detail>",
                "guidance": "Full tables the main slides summarised.",
                "repeat": 4,
                "grow": 1.0,
                "min_deck": 14
            }
        ]
    },
    {
        "name": "research_findings",
        "aliases": [
            "research",
            "findings",
            "study_results",
            "analysis",
            "survey_results"
        ],
        "title": "Research findings",
        "description": "Research or analysis readout: headline findings, method, one finding per slide with its evidence, quotes, implications, limitations, next steps.",
        "default_slides": 9,
        "min_slides": 1,
        "max_slides": 17,
        "one_slide": "exec_summary",
        "storyline": "Headline findings first; then the method in one slide so the audience trusts the rest; then one finding per slide, each title the finding itself.",
        "slots": [
            {
                "id": "title",
                "type": "title",
                "priority": 1,
                "title": "<Study>: <the headline finding>",
                "guidance": "Study name, team, date."
            },
            {
                "id": "exec_summary",
                "type": "bullets",
                "priority": 1,
                "title": "<What we found, in one sentence>",
                "guidance": "3-5 findings with their numbers; the implication of each.",
                "kind": "executive_summary"
            },
            {
                "id": "method",
                "type": "bullets",
                "priority": 3,
                "title": "<How we found it: sample and period>",
                "guidance": "Sample size, period, method, and what it can and cannot show."
            },
            {
                "id": "finding",
                "type": "chart",
                "priority": 1,
                "title": "<Finding, stated as a sentence>",
                "guidance": "One finding per slide with the chart that proves it; source, n and period on the slide.",
                "repeat": 6,
                "grow": 0.75,
                "spec": {
                    "chart_type": "auto"
                }
            },
            {
                "id": "voice",
                "type": "quote",
                "priority": 4,
                "title": "<What participants said>",
                "guidance": "A representative quote with who said it (role, not name).",
                "repeat": 2,
                "grow": 2.0
            },
            {
                "id": "implications",
                "type": "bullets",
                "priority": 2,
                "title": "<What this means for us>",
                "guidance": "The decisions or changes the findings point to."
            },
            {
                "id": "limitations",
                "type": "bullets",
                "priority": 3,
                "title": "<What the findings do not show>",
                "guidance": "Sample limits, bias, open questions."
            },
            {
                "id": "next_steps",
                "type": "table",
                "title": "Next steps: who does what by when",
                "guidance": "Columns Action | Owner | Due. One row per commitment; a named owner and a date on every row.",
                "spec": {
                    "columns": [
                        "Action",
                        "Owner",
                        "Due"
                    ]
                },
                "priority": 2
            },
            {
                "id": "appendix_detail",
                "type": "table",
                "priority": 6,
                "title": "<Supporting data>",
                "guidance": "Detailed tables.",
                "repeat": 3,
                "min_deck": 12
            }
        ]
    },
    {
        "name": "roadmap",
        "aliases": [
            "timeline",
            "plan",
            "strategy_roadmap",
            "launch_plan"
        ],
        "title": "Roadmap / timeline",
        "description": "Roadmap or plan over time: the goal, workstreams by period, key milestones, dependencies, risks and next steps.",
        "default_slides": 5,
        "min_slides": 1,
        "max_slides": 9,
        "one_slide": "roadmap",
        "storyline": "Where we are going, the route by period, the dates that matter, and what the plan depends on.",
        "slots": [
            {
                "id": "title",
                "type": "title",
                "priority": 1,
                "title": "<Plan name> <period>",
                "guidance": "Plan, owner, date."
            },
            {
                "id": "goal",
                "type": "bullets",
                "priority": 2,
                "title": "<Where the plan gets us>",
                "guidance": "The end state and the 2-3 measures of success."
            },
            {
                "id": "roadmap",
                "type": "timeline",
                "priority": 1,
                "title": "<How the workstreams line up by period>",
                "guidance": "Lanes per workstream, bars per initiative, periods as columns (quarters or months).",
                "repeat": 3,
                "grow": 2.0,
                "spec": {
                    "periods": [
                        "P1",
                        "P2",
                        "P3",
                        "P4"
                    ]
                }
            },
            {
                "id": "milestones",
                "type": "timeline",
                "priority": 2,
                "title": "<The dates that matter>",
                "guidance": "4-8 milestones with dates."
            },
            {
                "id": "dependencies",
                "type": "diagram",
                "priority": 3,
                "title": "<What the plan depends on>",
                "guidance": "A flow of the critical dependencies.",
                "spec": {
                    "diagram": "flow"
                }
            },
            {
                "id": "risks",
                "type": "bullets",
                "priority": 4,
                "title": "<What could delay it>",
                "guidance": "Top risks with mitigations."
            },
            {
                "id": "next_steps",
                "type": "table",
                "title": "Next steps: who does what by when",
                "guidance": "Columns Action | Owner | Due. One row per commitment; a named owner and a date on every row.",
                "spec": {
                    "columns": [
                        "Action",
                        "Owner",
                        "Due"
                    ]
                },
                "priority": 3
            }
        ]
    },
    {
        "name": "training",
        "aliases": [
            "explainer",
            "tutorial",
            "workshop",
            "onboarding",
            "lesson"
        ],
        "title": "Training / explainer",
        "description": "Training session or explainer: objectives, concepts one per slide, a process, worked examples, a check for understanding and a summary; the script lives in the speaker notes.",
        "default_slides": 10,
        "min_slides": 2,
        "max_slides": 16,
        "one_slide": "summary",
        "storyline": "Tell them what they will learn, teach one idea per slide with an example, check, then summarise. Put the spoken script in the notes, not on the slide.",
        "slots": [
            {
                "id": "title",
                "type": "title",
                "priority": 1,
                "title": "<Topic>: <what the learner will be able to do>",
                "guidance": "Course name, presenter, date."
            },
            {
                "id": "objectives",
                "type": "agenda",
                "priority": 1,
                "title": "What you will be able to do",
                "guidance": "3-5 learning objectives, each starting with a verb."
            },
            {
                "id": "concept",
                "type": "bullets",
                "priority": 2,
                "title": "<One idea, stated as a sentence>",
                "guidance": "One concept per slide, 3-4 short points; the explanation in the notes.",
                "repeat": 6,
                "grow": 1.0
            },
            {
                "id": "process",
                "type": "diagram",
                "priority": 2,
                "title": "<How the steps fit together>",
                "guidance": "The procedure as 3-6 steps.",
                "spec": {
                    "diagram": "process"
                }
            },
            {
                "id": "example",
                "type": "two_column",
                "priority": 3,
                "title": "<A worked example>",
                "guidance": "Left: the situation; right: what to do and why.",
                "repeat": 3,
                "grow": 1.5
            },
            {
                "id": "framework",
                "type": "diagram",
                "priority": 4,
                "title": "<The model that ties it together>",
                "guidance": "A cycle, pyramid or 2x2 that organises the concepts.",
                "spec": {
                    "diagram": "cycle"
                }
            },
            {
                "id": "check",
                "type": "bullets",
                "priority": 4,
                "title": "Check your understanding",
                "guidance": "2-4 questions; answers in the notes."
            },
            {
                "id": "summary",
                "type": "bullets",
                "priority": 1,
                "title": "<The three things to remember>",
                "guidance": "The key points, mirroring the objectives."
            },
            {
                "id": "resources",
                "type": "bullets",
                "priority": 5,
                "title": "Where to learn more",
                "guidance": "Links, documents and contacts."
            }
        ]
    }
]
# fmt: on
