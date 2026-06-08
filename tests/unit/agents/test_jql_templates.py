# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the JQL template builder (gaia_agent_jira.jql_templates).

``generate_jql_from_templates`` turns natural language into a JQL string that is
later sent to Atlassian. Because part of the input is user-supplied free text,
the generated JQL is an injection surface. These tests verify both:

1. Correct JQL construction for representative natural-language inputs, and
2. The defensive property that makes the builder safe: user-supplied values are
   captured by *restrictive regex character classes* (e.g. ``[A-Z0-9]+`` for
   project keys, an email-shaped class for assignees) rather than by escaping.
   Anything outside that class terminates the capture, so quote / boolean
   injection chars cannot leak into a value's quoting context.

All tests are pure-function tests — no network, no Atlassian, no LLM.
"""

from __future__ import annotations

from gaia_agent_jira.jql_templates import (
    COMPOSITE_PATTERNS,
    JQL_TEMPLATES,
    LABEL_MAPPINGS,
    ORDER_PATTERNS,
    REGEX_PATTERNS,
    TEAM_PATTERNS,
    generate_jql_from_templates,
)

ORDER_SUFFIX = " ORDER BY updated DESC"


# ---------------------------------------------------------------------------
# Default ordering and no-match fallback
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_unmatched_input_uses_default_query_and_order(self):
        # No template, regex, label, or team matches -> documented default.
        assert (
            generate_jql_from_templates("zzqqxx nonsense")
            == "created >= -30d" + ORDER_SUFFIX
        )

    def test_default_order_appended_when_no_order_keyword(self):
        out = generate_jql_from_templates("bugs")
        assert out.endswith(ORDER_SUFFIX)

    def test_explicit_order_keyword_overrides_default(self):
        out = generate_jql_from_templates("bugs newest")
        assert out.endswith(" ORDER BY created DESC")
        assert "updated DESC" not in out


# ---------------------------------------------------------------------------
# Simple template lookups
# ---------------------------------------------------------------------------


class TestTemplateLookups:
    def test_bug_issuetype(self):
        assert (
            generate_jql_from_templates("show me all bugs")
            == 'issuetype = "Bug"' + ORDER_SUFFIX
        )

    def test_status_template(self):
        assert (
            generate_jql_from_templates("in progress")
            == 'status = "In Progress"' + ORDER_SUFFIX
        )

    def test_assignment_function_template(self):
        assert (
            generate_jql_from_templates("assigned to me")
            == "assignee = currentUser()" + ORDER_SUFFIX
        )

    def test_all_template_values_quote_literal_strings(self):
        # Every literal-string template either quotes its value or uses a
        # JQL function / operator. This is the convention the module relies on
        # for safety. We assert the literal-value templates are quoted.
        for key in ("bug", "story", "task", "epic", "blocker", "critical", "closed"):
            jql = JQL_TEMPLATES[key]
            # The right-hand value is wrapped in double quotes.
            assert '"' in jql, f"template {key!r} should quote its value: {jql!r}"


# ---------------------------------------------------------------------------
# Composite patterns (only reached when no plain template matched)
# ---------------------------------------------------------------------------


class TestCompositePatterns:
    def test_composite_only_when_no_plain_template_matches(self):
        # "critical bugs" contains the plain template substring "bug", which is
        # matched first (the plain-template loop runs before composites and
        # breaks on first hit). Documents the actual precedence.
        out = generate_jql_from_templates("critical bugs")
        assert out == 'issuetype = "Bug"' + ORDER_SUFFIX

    def test_every_composite_key_is_shadowed_by_a_plain_template(self):
        # Observation test (documents current behavior, not a desired guard):
        # every key in COMPOSITE_PATTERNS contains a plain-template substring
        # ("bug", "open", "task", "story", ...) that the earlier plain-template
        # loop matches and breaks on first. As a result the composite branch is
        # never reached for these keys today. If a future change makes a
        # composite reachable, this test will flag the behavior shift.
        for key in COMPOSITE_PATTERNS:
            body = generate_jql_from_templates(key).split(" ORDER BY")[0]
            assert body != COMPOSITE_PATTERNS[key], (
                f"composite {key!r} unexpectedly reached the composite branch; "
                "precedence assumption changed"
            )


# ---------------------------------------------------------------------------
# Regex patterns: project, story points, dates
# ---------------------------------------------------------------------------


class TestRegexPatterns:
    def test_project_key_uppercased(self):
        out = generate_jql_from_templates("issues in proj project")
        assert "project = PROJ" in out

    def test_story_points_comparison(self):
        out = generate_jql_from_templates("story points > 5")
        assert '"Story Points" > 5' in out

    def test_created_after_date_quoted(self):
        out = generate_jql_from_templates("created after 2024-01-15")
        assert 'created >= "2024-01-15"' in out

    def test_assignee_email_quoted(self):
        out = generate_jql_from_templates("assigned to alice@example.com")
        assert 'assignee = "alice@example.com"' in out

    def test_quoted_phrase_becomes_text_search(self):
        out = generate_jql_from_templates('search for "login timeout"')
        assert 'text ~ "login timeout"' in out


# ---------------------------------------------------------------------------
# Labels and teams
# ---------------------------------------------------------------------------


class TestLabelsAndTeams:
    def test_label_mapping_expands(self):
        out = generate_jql_from_templates("security issues")
        # Label set is unordered; assert each expected label is present.
        assert "labels in (" in out
        for label in LABEL_MAPPINGS["security"]:
            assert f'"{label}"' in out

    def test_team_membership_pattern(self):
        out = generate_jql_from_templates("backend team work")
        assert 'assignee in membersOf("backend-team")' in out


# ---------------------------------------------------------------------------
# OR vs AND combination
# ---------------------------------------------------------------------------


class TestCombination:
    def test_or_keyword_joins_with_or(self):
        # Two regex parts joined; presence of " or " switches the joiner.
        out = generate_jql_from_templates("story points > 5 or story points < 1")
        assert " OR " in out
        assert " AND " not in out.split(" ORDER BY")[0]

    def test_default_joins_with_and(self):
        out = generate_jql_from_templates("bugs assigned to bob@example.com")
        body = out.split(" ORDER BY")[0]
        assert " AND " in body


# ---------------------------------------------------------------------------
# SECURITY: injection surface — restrictive capture classes contain the value
# ---------------------------------------------------------------------------


class TestInjectionContainment:
    def test_project_key_injection_chars_dropped(self):
        # The project regex captures only [A-Z0-9]+, so trailing quote / boolean
        # injection chars are not part of the value. `project = PROJ` is emitted
        # unquoted but cannot be poisoned because the value is alphanumeric only.
        out = generate_jql_from_templates('project PROJ" OR 1=1')
        body = out.split(" ORDER BY")[0]
        assert "project = PROJ" in body
        # The injected boolean tail did not become part of the project clause.
        assert "1=1" not in body

    def test_assignee_email_injection_bounded_by_charclass(self):
        # The email char class [a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+ stops at the
        # first non-matching char, so a closing quote / OR cannot land *inside*
        # the assignee value's quotes.
        out = generate_jql_from_templates('assigned to evil@example.com" OR "1"="1')
        # The assignee clause quotes exactly the email, nothing more.
        assert 'assignee = "evil@example.com"' in out
        # The injected boolean did not fuse into the assignee value.
        assert 'assignee = "evil@example.com" OR "1"="1"' not in out

    def test_assignee_value_has_no_unescaped_break_in_clause(self):
        out = generate_jql_from_templates("assigned to user@corp.io")
        # Exactly one assignee clause with a single quoted value.
        assert out.count('assignee = "') == 1
        clause = 'assignee = "user@corp.io"'
        assert clause in out

    def test_story_points_only_accepts_digits(self):
        # The Story Points comparison regex requires \d+, so a non-numeric
        # "value" never reaches it and the injected text is not interpolated
        # into a "Story Points" comparison clause.
        out = generate_jql_from_templates("story points > abc; DROP TABLE")
        assert '"Story Points" >' not in out
        assert "DROP TABLE" not in out

    def test_no_shell_or_jql_metachars_leak_for_garbage_input(self):
        # Pure garbage with metacharacters falls through to the safe default.
        out = generate_jql_from_templates(";`$(){}[]<>")
        assert out == "created >= -30d" + ORDER_SUFFIX


# ---------------------------------------------------------------------------
# Structural sanity of the static tables
# ---------------------------------------------------------------------------


class TestStaticTables:
    def test_regex_patterns_are_callable_pairs(self):
        for pattern, generator in REGEX_PATTERNS:
            assert isinstance(pattern, str)
            assert callable(generator)

    def test_order_patterns_start_with_order_by(self):
        for clause in ORDER_PATTERNS.values():
            assert clause.startswith("ORDER BY ")

    def test_composite_patterns_combine_conditions(self):
        # Each composite has at least one boolean joiner or function call.
        for jql in COMPOSITE_PATTERNS.values():
            assert " AND " in jql or " OR " in jql or "(" in jql

    def test_team_patterns_use_membersof(self):
        for jql in TEAM_PATTERNS.values():
            assert "membersOf(" in jql
