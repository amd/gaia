Verify a gaia-coder self-fix PR addresses the feedback it claims to.

<feedback_record>
  <id>{{fb_id}}</id>
  <em_body>{{verbatim_em_wording}}</em_body>
  <fix_class>{{class}}</fix_class>
  <candidate_files>{{triaged_paths}}</candidate_files>
  <success_criterion>{{from_plan_stage_3}}</success_criterion>
</feedback_record>

<pr>
  <title>{{title}}</title>
  <body>{{body}}</body>
  <diff>{{unified_diff}}</diff>
  <regression_test_path>{{test_path}}</regression_test_path>
</pr>

<test_run_results>
  <on_coder_branch>{{pytest_result_on_coder}}</on_coder_branch>
  <on_branch>{{pytest_result_on_fix_branch}}</on_branch>
</test_run_results>

Each check must be pass:
  1. regression_test fails on coder-branch AND passes on branch.
  2. diff touches ≥1 file from candidate_files (root cause, not just symptom).
  3. PR body cites feedback_id AND quotes em_body (verbatim or close paraphrase).
  4. PR body states how the diff addresses em_body in the author's own words.

Respond with JSON only, matching this schema:

{
  "checks": [{"name": "...", "verdict": "pass|fail", "evidence": "..."}, ...],
  "overall": "pass|request-changes",
  "blockers": ["..."]
}
