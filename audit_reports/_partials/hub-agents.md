# Audit slice: hub agent packages (`hub/agents/*/`)

**Headline: the hub agents are the best-tested surface in the repo, and the one real
contract bug I found is a published-doc drift, not a mocked-payload bug.**

The email agent already does the thing the Lemonade `{"spec": "flm:npu"}` bug needed:
it regenerates its OpenAPI document from the real FastAPI app and fails CI if the
committed artifact drifts (`test_rest_contract.py:210`), and it asserts an *exact* set
equality on the documented route list (`test_rest_contract.py:175`). Most "mocks" here
are hand-written **fakes injected through `app.dependency_overrides`**, with the real
handler, real pydantic validation, and real HTTP status mapping running underneath —
that is a genuinely different thing from mocking `_send_request`.

But one shipped spec surface is guarded only against *itself*, and it has already
drifted: **`POST /v1/email/agent/autonomy/undo` is a real, documented endpoint that the
shipped `specification.html` does not mention at all.** That is the #1841 pattern
CLAUDE.md warns about, and a self-referential drift guard is exactly what let it through.

Scope read: `hub/agents/{email,gaia,chat,connectors-demo,hello-world,word-count}/python/tests/`
(149 files) plus `hub/agents/{email,gaia}/npm/test/` (19 files).

---

## 1. Findings table

### DANGEROUS

| # | file:line | test / artifact | evidence | why it is dangerous | proposed replacement |
|---|---|---|---|---|---|
| D1 | `hub/agents/email/python/tests/test_spec_html_artifact.py:26` | `test_committed_spec_html_artifact_is_up_to_date` | `assert spec_html.check_artifact()` — regenerates `specification.html` from `spec_html.py` and byte-compares | The guard only proves **generator == artifact**. It never enumerates the real router. `agent_routes.py:648` registers `@router.post("/autonomy/undo")`; `SPEC.md` documents it (2 hits) and `SKILL.md` documents it (1 hit); `spec_html.py` and `specification.html` mention it **0 times**. The HTML spec served live at `GET /v1/email/spec` omits a real endpoint, and this test is green. | Enumerate the real routers and assert the rendered spec covers every non-`include_in_schema=False` path: `assert {r.path for r in agent_routes.router.routes} <= rendered_paths`. One assertion closes the whole class. |
| D2 | `hub/agents/email/python/tests/test_outlook_graph_query_shape.py:39` and `:53` | `test_list_messages_search_escapes_inner_quotes`, `..._escapes_backslash` | `assert captured["params"].get("$search") == '"from:\\"Acme Corp\\""'` | The request-building **is** real (`httpx.MockTransport` captures what `LiveOutlookBackend` actually emitted), so a regression *would* be caught — this is not a hollow assertion. The danger is narrower and real: the expected KQL escaping is a **hand-derived literal with no external ground truth**. Nothing has confirmed Microsoft Graph accepts `\"` as an inner-quote escape inside `$search`. If the escaping convention is wrong, the test enshrines it. Exactly #1655: self-consistent, never validated against the server. | `NEEDS-LIVE-CHECK:` one integration test against a real Graph tenant asserting the escaped query returns 200 rather than `400 InvalidRequest`. Gate it like `require_lemonade`. |
| D3 | `hub/agents/email/npm/test/client.test.ts:350` and `:483` | `archive + unarchive paths and types`, calendar preview→create | `expect(seen).toEqual(["http://x/v1/email/confirm", "http://x/v1/email/archive", "http://x/v1/email/unarchive"])` | The typed npm client's **URL paths are asserted against a hand-written list under a `vi.fn()` fake fetch**. `openapi.email.json` sits in the same repo listing the authoritative paths, and nothing cross-checks the two. A server-side path rename regenerates the OpenAPI artifact (D-guard passes), leaves the TS client pointing at the old path, and this test still passes. The one npm test that spawns the real sidecar (`query-integration.test.ts`) covers **only `/query`** — not confirm/archive/unarchive/calendar. | Import `openapi.email.json` in the vitest suite and assert every path the client emits is a key in `spec.paths`. Cheap, no server needed, and kills the whole drift class. |
| D4 | `hub/agents/gaia/python/tests/test_stdio.py:1142` | `test_the_parser_accepts_the_spellings_the_go_side_pins` | `stdio.build_parser().parse_args(["--use-claude","--claude-model",...,"--bypass-permissions","--json-events","--dev"])` | A **cross-language argv contract asserted on one side only.** Go pins the same strings as independent literals (`tui/internal/client/factory.go:43` `const BypassPermissionsFlag = "--bypass-permissions"`, `:53` `ClaudeModelFlag`). Neither side reads the other. The test's own docstring (`:1136-1139`) names the failure — "a rename passes every other Python test here and fails at spawn as a generic `exited (code 2)`" — then asserts against a literal the test itself typed. Renaming in `stdio.py` forces an edit here; nothing forces the Go edit. | Emit the flag names from one source (a small generated JSON both sides read), or add a test that greps `factory.go` for each `build_parser()` option string. The latter is ~15 lines and needs no build changes. |

### JUSTIFIED (leave alone — reasons stated)

| file | why justified |
|---|---|
| `email/python/tests/test_rest_contract.py` (~69 hits) | Not mocks in the risky sense. Fakes (`_FakeSearchBackend`, `_FakeMailbox`, `_FolderMailbox`, `_FakeSendBackend`) are injected via `app.dependency_overrides` / module seams; the **real** FastAPI app, real pydantic `_Strict` validation, and real status-code mapping all run. Critically the fakes **record outgoing calls and the tests assert their shape** — `assert fake.calls == [{"query": None, "label_ids": ["INBOX"], ...}]` (`:374`) — with a comment naming the divergence it guards (live Gmail with no `labelIds` returns ALL mail). This is the boundary-validity discipline the Lemonade bug lacked. |
| `email/.../test_gmail_batch_429_retry_2716.py`, `test_gmail_metadata_batch_2643.py`, `test_outlook_metadata_2643.py` | `httpx.MockTransport` at the socket layer; the real request builders produce the captured URL/params/body. Live Gmail/Graph 429 and batch-multipart scenarios are impractical to trigger on demand. |
| `email/.../test_doc_consistency.py` | No mocks. Reads `SCORECARD.md` front-matter and greps `README.md`/`EVALUATION.md` prose for score claims — a real cross-doc drift guard, and the model D1 should follow. Deliberately excludes `CHANGELOG.md` with a stated reason (historical record). |
| `gaia/.../test_caller_auth.py`, `email/.../test_caller_auth.py` | Real `build_app()` over `TestClient`; bearer gate, Host allowlist (DNS rebinding), and Origin rejection genuinely exercised. Not a mocked gate. |
| `gaia|email/.../test_publish_to_r2_by_reference.py` | Mocks boto3/HTTP transport only. Digest encoding, `Key` construction, and `request_checksum_calculation` config are produced by real code and asserted on the received payload. |
| `gaia/.../test_gen_binaries_lock.py` | Validates the generator against the **real committed** `binaries.lock.json` read from disk, not a literal. |
| `connectors-demo/.../test_connectors_demo.py` | The strongest pattern in the slice: `_CapturingGet` records real outgoing headers/params and asserts `Authorization: Bearer`, `maxResults`, `per_page`, RFC3339 stamps. A dropped bearer token would fail. |
| `hello-world`, `word-count` | Teaching templates. Tool logic tested for real; only the LLM client is stubbed. Appropriate. |
| `chat/.../test_chat_agent.py`, `test_*dependency_floor.py` | No mocks — real imports, manifest/pyproject read from disk and cross-validated. |

### MASKING / UNNECESSARY

**None I can ground.** I went looking and the email agent has explicit cold-state coverage:
`test_zero_connector_construction_2418.py` (agent must construct with **no** mailbox
connected and no injected fake), `test_google_access_not_configured.py` (fresh BYO Google
Cloud project, Gmail API not enabled → actionable 403), `test_mailbox_onboarding_2469.py`,
`test_connection_status_2401.py`. Route-level resolvers are tested from empty:
`test_get_search_backend_no_mailbox_fails_loud_503` (`test_rest_contract.py:466`),
`test_prescan_no_mailbox_connected_fails_loud` (`:1179`). On the gaia side the Lemonade-down
path is tested (`test_stdio.py:740`, `:794`), not just the healthy stub.

Reporting zero here rather than padding the count. The sibling audit slices cover the
in-core code where I'd expect the cold-state gaps to actually live.

---

## 2. Contract-vs-doc drift check

| agent | shipped spec artifacts | machine-checked? | verdict |
|---|---|---|---|
| **email** | `openapi.email.json`, `specification.html`, `CONTRACT.md`, npm `SPEC.md` / `SKILL.md` / `README.md` / `SCORECARD.md` | Partly | See below |
| **gaia** | npm `SPEC.md`, `SKILL.md`, `binaries.lock.json` | lock only | `SPEC.md` (523 lines) has **no** drift guard. Same class as D1, not yet demonstrated to have drifted. |
| chat / connectors-demo / hello-world / word-count | `README.md` only | n/a | No published request/response contract to drift. |

**Email — what is guarded:** `API_VERSION == SCHEMA_VERSION` (`:111`); `AGENT_VERSION` vs
installed dist metadata (`:122`); every contract pydantic model's property + required set
vs the exported spec (`:161`); the committed `openapi.email.json` byte-identical to a fresh
export (`:210`); `specification.html` byte-identical to its generator (`test_spec_html_artifact.py:26`);
eval score consistent across `SCORECARD.md`/`README.md`/`EVALUATION.md`.

**Email — the hole (D1), confirmed drift:** the stateful `/v1/email/agent/*` surface is
mounted by `agent_routes.py` but deliberately excluded from `export_openapi.build_app()`
(`export_openapi.py:56-71` mounts only `api_routes` + `connection_intake_routes`), and
`test_documented_routes_match_expected_set` asserts **exact** set equality, so the OpenAPI
artifact provably contains zero `/agent/*` paths. That leaves `SPEC.md` and
`specification.html` as the only integrator-facing description of 12 endpoints — and they
disagree:

```
SPEC.md agent endpoints (12):  session, session/{id}, session/{id}/history, memory,
                               memory/{id}, autonomy, autonomy/{id}, autonomy/run,
                               autonomy/undo, confirm-tool, cancel, query
specification.html (11):       ...same, MINUS autonomy/undo
real router (12):              agent_routes.py:648 -> @router.post("/autonomy/undo")
```

Mitigating fact worth stating plainly: the `/agent/*` **behaviour** is well tested.
`test_email_agent_routes.py` drives the real routes through `TestClient` with a fake agent
that exercises the real `SSEOutputHandler`, and it asserts the exact status codes `SPEC.md`
claims — overlapping turn 409 (`:266`), bad autonomy level 400 (`:472`), autonomy-off 409
(`:495`), uninitialised memory 409 (`:415`). I checked those four claims and **`SPEC.md` is
currently accurate on all of them.** The drift is in the *endpoint list*, not the semantics.
So: one real bug, not a rotten surface.

---

## 3. Coverage gaps — does anything start the real agent?

| agent | real end-to-end test? | where |
|---|---|---|
| **email** | **Yes, several.** | `tests/integration/test_email_rest_api_e2e.py`, `test_email_thin_client.py`, `test_email_rest_http_corpus.py`, `test_email_agent_live_gmail.py`, `test_email_agent_triage.py`, `test_email_corpus_alignment.py`, `test_email_bench_throughput.py`. Plus `hub/agents/email/npm/test/query-integration.test.ts`, which **spawns the real Python sidecar** and drives the typed client over real HTTP (version handshake, bearer gate, canonical event sequence, mid-run cancel). It correctly randomises to port 8200–8700 with an explicit `// never 4001`. |
| **gaia** | Partial. | `tests/integration/test_daemon_sidecars.py` covers sidecar supervision. No test spawns `gaia_agent.stdio` as a real child process and drives a turn over the pipe — the transport is tested in-process via `io.StringIO` only. That is why D4 (the Go argv contract) has no backstop. |
| **chat** | Yes. | `tests/integration/test_chat_hub_wheel_journey.py`, `test_chat_rag_pdf_e2e.py`, `test_chat_rag_pdf_chat_e2e.py`, `test_chat_ui_integration.py`. |
| connectors-demo / hello-world / word-count | No, and fine. | Teaching templates. |

**The one gap that matters:** no test spawns the real `gaia` stdio agent as a subprocess with
the argv the Go TUI actually passes. `query-integration.test.ts` proves the pattern is
affordable — the email agent already does exactly this.

---

## 4. Remediation, cheapest first

1. **D1 — one assertion, fixes a live bug.** In `test_spec_html_artifact.py`, enumerate
   `agent_routes.router.routes` + `api_routes.router.routes` and assert the rendered HTML
   covers every schema-visible path. Then add the missing `/autonomy/undo` section to
   `spec_html.py` and regenerate. *This is a real shipped-doc defect, not a test smell.*
2. **D3 — one shared fixture converts the whole npm suite.** Load `openapi.email.json` once
   in vitest setup; assert every URL the client emits is a key in `spec.paths`. No server,
   no new dependency.
3. **D4 — ~15 lines.** A test that greps `tui/internal/client/factory.go` for every option
   string `stdio.build_parser()` defines. Add a subprocess smoke test that spawns the real
   stdio agent with the Go argv and asserts one JSON event comes back.
4. **D2 — needs live access.** Gate a Graph integration test the way `require_lemonade`
   gates Lemonade. Lowest priority: the request-building is genuinely exercised, so this
   only closes the "is our escaping convention actually right" question.
5. **Shared-fixture leverage:** items 1 and 2 are both "assert the doc against the code that
   generates it" — the pattern `test_doc_consistency.py` already established. Extending that
   one file's approach to the spec artifacts converts D1 and D3 together.

**`NEEDS-LIVE-CHECK:` commands (not run — no servers started):**

```bash
# D1 — confirm the shipped HTML spec omits the endpoint (static, safe to run)
grep -c "autonomy/undo" hub/agents/email/python/specification.html   # observed: 0
grep -n "autonomy/undo" hub/agents/email/python/gaia_agent_email/agent_routes.py  # observed: 648

# D2 — requires a real Microsoft Graph tenant + token
#   assert LiveOutlookBackend.list_messages(query='from:"Acme Corp"') returns 200, not 400
# D4 — spawn the real stdio agent with the Go argv
python -m gaia_agent.stdio --json-events --bypass-permissions   # then write one query line to stdin
```

---

## Summary

**Counts:** DANGEROUS 4 · MASKING 0 · UNNECESSARY 0 · JUSTIFIED 9 file-groups
(~500+ of the ~714 mock hits in this slice).

The hub agents largely already practise what CLAUDE.md preaches — fakes injected behind
real handlers, outgoing call shapes asserted, OpenAPI regenerated and byte-compared, cold
states (no mailbox, no connector, API not enabled, Lemonade down) explicitly tested. I did
not find a second `{"spec": "flm:npu"}`-style locked-in wrong payload, and I am not going to
invent MASKING findings to balance the table.

**Top 3 DANGEROUS:**

1. **D1 — `specification.html` ships without `/v1/email/agent/autonomy/undo`.** A real,
   documented, implemented endpoint is missing from the HTML spec served to integrators at
   `GET /v1/email/spec`. `test_spec_html_artifact.py:26` is green because it only compares
   the generator to its own output — it never asks the router what exists. **This is a live
   defect, not a hypothetical.**
2. **D3 — the npm client's REST URLs are asserted against a hand-written list under a fake
   fetch** (`client.test.ts:350`, `:483`), while `openapi.email.json` sits unread in the same
   repo. A path rename passes both sides' tests and breaks integrators. Only `/query` has a
   real-sidecar test.
3. **D4 — the Go↔Python argv contract is pinned twice, checked never.**
   `factory.go:43` and `test_stdio.py:1142` each assert their own literal; a one-sided rename
   surfaces as `exited (code 2)` at spawn. The test's docstring predicts this exact failure.
