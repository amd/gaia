# Multiple email and calendar accounts

Status: draft plan, 2026-11-16. Checked against main at e69dd5fc0.

## What users get

Today a person can connect one Google account and one personal Microsoft account. If they
connect both, GAIA only ever reads and sends from Gmail. A second Gmail sign-in silently
replaces the first.

After this work:

- A person can connect several Google and Microsoft accounts at once, for example a personal
  Gmail, a second Gmail and an Outlook account.
- "What's new in my inbox?" and "Am I free at 10?" look at every connected account. Each
  result says which account it came from.
- A reply always goes out from the account that received the message. A new email or new
  event names its account on the approval card before anything is sent.
- Someone with one account sees exactly what they see today, plus one quiet "Add another
  account" link.

## One case, walked through

Jane has jane@gmail.com, jane.work@gmail.com and jane@outlook.com connected. jane@gmail.com
is her default.

1. She asks "anything from Sam this week?" GAIA searches all three accounts. Two hits come back,
   each labelled with its account.
2. She says "reply to the second one, say Thursday works". That message came from
   jane.work@gmail.com, so the reply is drafted from there. The approval card shows
   "From: jane.work@gmail.com" on its own line. If the account changes after she approves, the
   send is refused, the same way it is today.
3. She asks "am I free Thursday at 2?" GAIA checks the main calendar of all three accounts. If
   one of them can't be read (for example its sign-in expired), the answer is "I couldn't check
   jane@outlook.com" instead of "you're free".
4. She says "email Priya the deck". No account is named, so GAIA uses her default,
   jane@gmail.com, and the card says so. With no default set and more than one account, GAIA
   asks which account first.
5. jane@outlook.com's sign-in expires. Settings shows that one row as "Sign-in expired" with a
   Reconnect button. The other two keep working. Reconnecting checks that the same account came
   back. If she picks a different account in the browser, GAIA adds it as a new account instead
   of quietly replacing the old one.

## Decisions this plan makes

- **An account is identified by the provider's stable id, not the email address.** Addresses can
  change. Google already gives us its id in the sign-in reply. Microsoft gives it from the same
  profile call we already make, once we ask for one more field.
- **The account connected today stays where it is.** It becomes that provider's default account.
  New accounts get their own slots. Nothing is moved, so there is no risky migration, and an
  older GAIA keeps working with the default account.
- **Permissions stay per connector, and survive until the last account goes.** An agent granted
  Gmail access may use every connected Google account. Disconnecting one Gmail keeps the grants,
  so the other keeps working; disconnecting the last one clears them, as today. Per-account
  grants were considered and dropped: the grants file, its consent dialog and every caller key
  on the connector, and an agent that reads "my inbox" needs all accounts anyway. Splitting
  them would add a second consent step per account for no protection a user asked for.
- **Reads cover all accounts. Writes name one.** Replies, forwards and event edits take their
  account from the message or event they act on. They never guess.
- **Message and event ids carry their account.** A short account tag is added to every id the
  tools hand out. An id without one is refused, so a reply from the wrong account is impossible
  by design.
- **One default sending account.** It is set automatically to the first account connected, and the
  user can change it.
- **No separate on/off switches for mail and calendar per account in the first version.** Every
  connected account is used for both. This can be added later if people ask for it.
- **The small model tier is unchanged.** Gemma 4 E4B does not get mail or calendar tools today, and
  this work does not change that.

## Decisions Tyler or Kalin need to make

1. **Google's testing mode.** Until GAIA's Google app is published and verified, every Google
   sign-in expires after 7 days and each project allows 100 test users. With several accounts,
   people reconnect several accounts every week. Publishing needs Google's review of the Gmail
   permissions, which costs time and money. This is the same open question as #2104.
2. **Work and school Microsoft accounts.** Mail and calendar ignore them today (#7876). This plan
   makes adding them cheap, because the tools will already handle a list of accounts. Including
   them needs a test Microsoft 365 tenant and a product decision.
3. **Changing the "Gmail always wins" behavior.** People who have Gmail and Outlook connected today
   will start seeing Outlook results too. That is the point of the change, but it is a visible
   change and belongs in the release notes.

## The work, in four PRs

### PR 1: saving several accounts safely (backend only, no visible change)

Status: built on `claude/multi-account-store`. A person with one account sees no change.

**How accounts are stored.**

- An account is named by the provider's stable id: Google's id_token `sub`, Microsoft's Graph
  `/me` `id` (asked for alongside the address). The address is only a label.
- The first account of a provider stays in the keyring entry it uses today, `<provider>:default`.
  Later accounts get `<provider>:id:<stable id>`. Nothing moves, and an older GAIA still reads the
  first account.
- A list of accounts sits next to `grants.json`, at `~/.gaia/connectors/accounts.json`. It holds
  each account's slot, stable id, address, state (`pending`, `ok`, `reauth_required`) and kind,
  plus the default slot. No secrets. A first account saved before this list existed is read
  from its keyring entry, so an old install works without a migration step.
- A sign-in writes its list entry as `pending` first, then the token, then marks it `ok`. A crash
  in between leaves a listed account with status `error` that can be removed or signed into again.
- The stable id of an account saved before this change is filled in on its next refresh (Google
  from the refresh's id_token, Microsoft from Graph `/me`), with its address if it was stored as
  `default`. Until then it is matched by address, but only when the account kinds (personal or
  work) don't conflict. A lone old account stored without an address is updated by a reconnect
  and kept beside an added account. If the id it learns is already stored in another slot, the
  two merge into one. The token the refresh just proved is kept unless the other slot was granted
  scopes it lacks (the user signed in again to add calendar); then that later sign-in is kept.
  The survivor takes the default flag. The merge waits for a later refresh while a sign-in or
  another refresh holds either account, and one id is never written to two slots. When the
  account list can't be written, the refresh still saves its new token and records the id
  later. The list is written before the token, so a crash between them
  is finished by the next refresh. A token refused its profile (401 or 403) is asked once per
  process; a network error is retried on the next refresh.
- A sign-in whose reply names no account id fails loudly and saves nothing.
- The same stable id updates the stored account in place. A different id is added; it never
  overwrites another account, even with the same address.
- Refusing a refresh token (`invalid_grant`) deletes that account's token and marks it
  `reauth_required`; the account stays listed so the user can reconnect it. A good refresh or
  sign-in marks it `ok` again.

**Locks.** Each account has its own refresh lock, so two accounts refresh at the same time. Adding,
removing or forwarding an account also takes the provider's lock, always before the account's.

**Default account.** The first account connected is the default. Every call that names no account
uses it. It can be changed. If the default is removed, the oldest remaining account takes over.

**Permissions.** Grants stay per connector (see the decision above). Removing one account keeps
them; removing the last clears them and the MCP activations, as a full disconnect always has.

**Forwarded sign-ins.** GAIA keeps one OAuth client per provider. With no account or one account
stored, a forward saves or replaces it, as today. With several, it must name one of them by
address, and it must use the client the others use; otherwise it is refused loudly instead of
leaving those accounts unable to refresh. The recovery record a forward leaves
names the slot it replaced.

**Contract for PRs 2 to 4.**

Python, `gaia.connectors`:

```python
@dataclass(frozen=True)
class Account:
    connector_id: str
    account_id: str          # stable id; "default" for an old account not refreshed yet
    email: str
    status: Literal["ok", "reauth_required", "error"]
    is_default: bool
    slot: str                # keyring slot; internal
    account_type: Optional[str] = None   # Microsoft: "personal" / "work"
    def to_dict(self) -> dict  # {account_id, email, status, is_default}

list_accounts(connector_id) -> List[Account]           # oldest first
set_default_account(connector_id, account) -> Account
get_access_token(*, provider, scopes, agent_id=None, account=None) -> str
get_access_token_with_expiry(..., account=None) -> Tuple[str, float]
get_access_token_sync(..., account=None) / get_access_token_with_expiry_sync(..., account=None)
get_connection(provider, account=None) -> Optional[dict]
list_connections() -> List[dict]   # one row per ok account: provider, account_id, is_default,
                                   # account_email, scopes, connected_at, account_type
revoke_connection_async(provider, account=None, *, all_accounts=False) -> dict
revoke_connection(provider, account=None, *, all_accounts=False) -> dict
    # revoke_supported, revoked_remotely, revoke_error, account_id, account_email,
    # remaining_accounts; with all_accounts also "accounts": [per-account results]
start_authorization(provider_id, scopes, grant_agents=None, *, add_account=False, account=None)
```

`account` is a stable id or an address; `None` is the default account. A value that matches
nothing, or several accounts, raises `UnknownAccountError` with `choices` (the addresses).
`add_account=True` shows the account picker (Google `prompt=select_account consent`, Microsoft
`prompt=select_account`). `account=` reconnects: it sends `login_hint`, and for Microsoft
`domain_hint` when the account kind is known. Passing both is an error. The sign-in result and
the `connector.oauth.completed` event carry `account_id`, `added` and `is_default`; a reconnect
also carries `requested_account_id`, so a caller can say when a different account came back.
The device-code flow takes the same `add_account` and `account` (`start_device_flow` returns
an `intent` for `poll_device_flow`), so a device sign-in with `--add` never replaces an old
account stored without an address.

Agent UI routes, `/api/connectors`:

- `GET /{id}` and the list rows keep `account_id` (the default account's address, as today) and
  add `accounts: [{account_id, email, status, is_default}]`. `configured` is true when any
  account is `ok`.
- `DELETE /{id}` removes the only account. With several it refuses: 409
  `{"error": "account_choice_required", "connector_id", "choices": [...], "message"}`.
  `DELETE /{id}?all=true` removes every account. MCP connectors are unchanged.
- `DELETE /{id}/accounts/{account}` removes one (204). Grants stay unless it was the last.
- `PATCH /{id}/accounts/{account}` with `{"is_default": true}` returns `{"accounts": [...]}` and
  emits `connector.account.changed`. `false` is a 400.
- `POST /{id}/authorize` and `POST /{id}/authorize-device` accept `add_account` and `account`.
  Reconnecting goes through these routes with `account`; there is no separate reauthorize route.
- An unknown account is a 404 `{"error": "unknown_account", "choices": [...]}`.
- `GET /v1/connections/{provider}?account=` picks an account; `DELETE /v1/connections/{provider}`
  removes every account.

CLI, `gaia connectors`:

- `list` / `status`: a line per account under a connector that has several, or one whose
  sign-in expired. `--json` rows gain `accounts`.
- `connect <id> --add` or `--account <address>`, with or without `--device`. A plain `connect`
  that signs in a different account says it was added and that the default is unchanged.
- `disconnect <id> --account <address>` or `--all`. With several accounts and neither flag it
  refuses (exit 5) and lists them.
- `set-default <id> <account>`.

Uninstall removes every account of every provider.

Not in PR 1: the device-code flow has no account picker or sign-in hint (Microsoft's device
page always asks), so `--add` and `--account` there only decide where the sign-in is saved.
Client credentials per account were not built; forwards that would need them are refused.

### PR 2: mail and calendar tools use every account

- Replace the "first provider wins" choice with a list of usable accounts. Reading tools (inbox,
  search, waiting on you, folders) ask every account and merge the results by time. Each
  account gets a fair share of the limit, so one busy inbox can't crowd out the others. Every
  item names its account.
- Add an optional `account` argument for narrowing a read ("just my work Gmail", or "outlook").
- Account tags on message, thread and event ids. Read, reply, forward, update and cancel take
  the account from the id.
- New emails and drafts use the default account, or the account the user named. With more than
  one account and no default, GAIA refuses and asks. The approval card and the check that the
  account didn't change both use the account the call is bound to.
- Calendar: availability checks the main calendar of every account, and says "couldn't check"
  instead of "free" when any one can't be read. Listing merges and labels events.
  `create_calendar_event`'s account argument now chooses the calendar instead of only guarding
  it.
- Per-account "is this me?" checks for reply-all, "am I the organizer?" and phishing warnings.
- Mail-read budgets per turn should account for several inboxes.
- Update the skill instructions (inbox triage, send email, calendar) and the tool descriptions
  that say "one account".
- Evals: give the fake mailbox a second Gmail account and a fake Outlook account, and build
  the missing calendar fixture. New scenarios:
  - reply from the second account;
  - a search that only hits the second account;
  - the same sender in two accounts;
  - a conflict that is only on Outlook;
  - a send with no account named;
  - an email trying to trick the agent into switching sender.

  Run `gaia_email` before and after, or name it for the next release candidate's eval run.

### PR 3: Settings, chat cards and approvals (after Kalin's connector page PRs land)

Kalin's #7577 (new Connectors page) and #8281 (step-by-step setup) rewrite the same screen and
translation files. Build this inside his new layout once both merge. PR 1 and PR 2 don't touch
those files and can start now.

- One account: unchanged, plus "Add another Google account" in the open card.
- Two or more: the tile says "2 accounts". The open card lists each account with its status:
  Connected, "Sign-in expired" with a Reconnect button, or "Not working". It also shows a
  "Default" choice and its own Disconnect, whose confirmation names the address. A long address
  is cut in the middle and shown in full on hover.
- Add the missing "sign-in expired" state. Today a dead sign-in still says "Connected" until a
  tool fails.
- Chat connect card: when one account fails, offer "Reconnect jane@..." for that account, plus
  "Use a different account". Never offer to connect the provider again while another account
  works.
- Labels in chat show which account each mail or event came from, only when there are two or
  more accounts.
- Email approval: a clear "From" line above the text being approved. To change the sender, the
  card offers "Send from a different account": it turns this request down and asks again with
  the new account. The card never edits a request after it was approved.
- Calendar approval: the server always fills in the calendar line. Today it shows only if the
  model passed one.
- Arabic: translate the hard-coded "From:" and "To:" labels too. Show addresses
  left-to-right. Add a plural for the account count. Check layout with a long address, two
  accounts, an expired row and a dialog open, in English and Arabic.

### PR 4: TUI

- Show the account list, revoke one account, and show the account on the calendar approval.

## Proving it before it ships

- Run a real test from a fresh GAIA home on Tyler's test accounts. Connect two Gmail accounts
  and one Outlook account through the Agent UI. Search, reply from the second account,
  check free time with a conflict only on Outlook, disconnect one account, and reconnect an
  expired one. Use the safe calendar dates (January 10 to 15, 2030).
- Take before and after screenshots of Settings and the approval cards for the PR bodies.

<details>
<summary>Technical notes from the investigation</summary>

- Store: `store.py` writes `<provider>:default` only (around 426-482). Recovery snapshots
  hardcode the default slot (614-700). `list_connections` probes only the default slot (812-835).
  The token cache is already keyed by `(provider, account)` (`tokens.py:92-96`), but writes back
  under the stored email (`tokens.py:335`). `api.py:737-740` evicts both keys to cope.
- Identity: Google requests `openid` (`providers/google.py:55-60`), and `flow.py:122` decodes the
  id_token but keeps only the email. Microsoft drops `openid` because AMD's tenant rejects it
  (`providers/microsoft.py:178-182`); add `id` to the Graph `/me` `$select` (around 187-212).
  `flow.py:209,230` fall back to `"default"`.
- Auth params: Google sends `prompt=consent` and nothing else that picks an account
  (`google.py:180-184`). Microsoft sends no `prompt` (`microsoft.py` around 346). Only one
  pending flow at a time (`flow.py:115-117`).
- Grants: `{connector_id: {agent_id: scopes}}` (`grants.py`). Disconnect wipes the connector
  (`oauth_pkce.py:270`, `grants.py:245`). Every read is `.get(connector_id)`, so extra keys like
  `google#<account>` are invisible to old readers. The email agent checks at
  `mailbox_state.py:280`. `prior_state.py:33-38` reads the default only.
- Forwarded import rewrites `provider:<provider>` (`api.py:678-681`). The client-id check then
  clears mismatched connections (`store.py:533-543`, `tokens.py:118-127`).
- Locks: `_refresh_lock.py:25-35`, one per provider. Take the provider lock before an account
  lock, never the reverse.
- Uninstall deletes only the default slot (`uninstall_command.py:324`).
- Tools: `_resolve_mailbox` returns `usable[0]` (`email_tools.py:452-471`).
  `list_connected_mailboxes` (1095). `_email_account_label` (605). The send guard is in
  `hub/skills/send-email/tools.py:188, 296-304`. The calendar pick is in
  `hub/skills/calendar/tools.py:584-661`, with the `provider_bound` refusal at 642-647.
  `_calendars_checked` (676) already reports unchecked accounts, and `_merge_free_slots` (462)
  takes a list. Ids are raw provider ids today (`_email/gmail.py:276-277`).
- Compact tier: `model_features.py:79-81` sets `email=False, calendar=False`.
- UI: `ConnectorsSection.tsx` (one `account_id` per tile at 511), `ConnectorTileMenu.tsx:194`,
  `render/ConnectAccountCard.tsx`, `email/EmailConnectCta.tsx`, `ConnectorRetryBanner.tsx`,
  `PermissionPrompt.tsx` (hard-coded English From/To), and `CalendarApproval.tsx:251-257`.
  Routes are in `src/gaia/ui/routers/connectors.py` (list 606, DELETE 918, authorize 1004,
  grants 1113/1139, forwarded 1240). TUI: `gaiaconnectors.go:46,162` and `chat/connectors.go`.
- Provider facts: Google refresh tokens are capped at 100 per account per client, so accounts
  don't eat each other's budget. Revoking one account doesn't affect another. Testing-mode
  tokens expire in 7 days. Microsoft's stable id is `oid` plus `tid`; GAIA can't revoke at
  Microsoft and the user removes the app on their account page. Sources:
  developers.google.com/identity/protocols/oauth2,
  developers.google.com/identity/openid-connect/openid-connect,
  learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference.
- Prior art: Spark uses one combined inbox, a default sending account and a From you can
  change per message. ChatGPT says some apps now support "Connect another" account. No
  official source explains why Claude's Gmail connector holds one account.
</details>
