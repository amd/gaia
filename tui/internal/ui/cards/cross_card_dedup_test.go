package cards

import (
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// #2631 -- shared items render once, across the pre-scan and attention card
// types. The two cards do not share a taxonomy (meeting_request/
// waiting_on_you/action_item exist only on the attention card), so a
// duplicate is resolved per item, not by suppressing whichever card renders
// second -- that would throw away meeting proposals and action items the
// attention card exists to surface. Whole-card suppression only happens as
// a side effect of every one of a card's items turning out to be a
// duplicate.
//
// #2743 moved this out of chat/attention_init_test.go (which drove it
// through the deleted attention-fetch plumbing) to here, driving
// cards.RenderDeduped directly the way chat.updateViewport threads `seen`
// across cards in one turn -- the plan's own instruction for keeping
// cards/attention.go's unit tests once its client-side fetch is gone.
// ---------------------------------------------------------------------------

const crossDedupPreScanShared = `{
  "kind": "email_pre_scan",
  "urgent": [], "actionable": [], "informational_count": 0,
  "suggested_archives": [], "suggested_drafts": [], "needs_review": [],
  "scanned": 1,
  "needs_you": [
    {"ref":1,"kind":"urgent","message_id":"MSG_A","sender":"a@example.com","subject":"F-Bombs","why":"urgent"}
  ],
  "needs_you_total": 1,
  "bulk": {"count": 0, "filter_tests": []},
  "preferences_applied": null
}`

const crossDedupPreScanSharedAndUnique = `{
  "kind": "email_pre_scan",
  "urgent": [], "actionable": [], "informational_count": 0,
  "suggested_archives": [], "suggested_drafts": [], "needs_review": [],
  "scanned": 2,
  "needs_you": [
    {"ref":1,"kind":"urgent","message_id":"MSG_A","sender":"a@example.com","subject":"F-Bombs","why":"urgent"},
    {"ref":2,"kind":"needs_response","message_id":"MSG_B","sender":"b@example.com","subject":"PreScanOnlySubject","why":"needs a reply"}
  ],
  "needs_you_total": 2,
  "bulk": {"count": 0, "filter_tests": []},
  "preferences_applied": null
}`

const crossDedupAttentionAllDuplicate = `{
  "kind":"email_attention",
  "items":[
    {"kind":"waiting_on_you","message_id":"MSG_A","sender":"a@example.com","subject":"F-Bombs (attention copy)","why":"still waiting"}
  ],
  "coverage":{"scanned":1,"total_unread":1},
  "generated_at":"x","cache_age_seconds":0.0,"stale":false
}`

const crossDedupAttentionSharedAndUnique = `{
  "kind":"email_attention",
  "items":[
    {"kind":"waiting_on_you","message_id":"MSG_A","sender":"a@example.com","subject":"F-Bombs","why":"waiting"},
    {"kind":"action_item","message_id":"MSG_C","sender":"c@example.com","subject":"AttentionOnlySubject","why":"open item"}
  ],
  "coverage":{"scanned":2,"total_unread":2},
  "generated_at":"x","cache_age_seconds":0.0,"stale":false
}`

// #2631 -- when every item an attention card would show is a duplicate of
// something the turn's pre-scan card already rendered, the attention card
// ends up with nothing left to say and is suppressed. This is the only case
// where dropping the whole card is correct, and it falls out of the
// per-item logic rather than a separate whole-card check.
func TestCrossCardDedupSuppressesAttentionCardWhenEveryItemAlreadyShownByPreScan(t *testing.T) {
	seen := map[string]bool{}
	preScanOut, preScanIDs := RenderDeduped("email_pre_scan", raw(t, crossDedupPreScanShared), width80, seen)
	for _, id := range preScanIDs {
		seen[id] = true
	}
	attentionOut, _ := RenderDeduped("email_attention", raw(t, crossDedupAttentionAllDuplicate), width80, seen)

	if !strings.Contains(plain(preScanOut), "F-Bombs") {
		t.Error("the surviving pre-scan card lost its own content")
	}
	if attentionOut != "" {
		t.Errorf("attention card rendered even though every one of its items was a duplicate -- the whole card should be suppressed:\n%s", plain(attentionOut))
	}
}

// #2631's "no message id twice" acceptance criterion, read literally off
// the issue: a pre-scan needs_you item and an attention item sharing a
// message_id must not both draw it. Unlike the whole-card-suppression
// design this replaces, each card's own unique content survives: the
// shared subject renders exactly once, the pre-scan-only subject is
// untouched, and the attention-only subject (a taxonomy needs_you has no
// equivalent for) still renders too.
func TestCrossCardDedupKeepsBothCardsUniqueContent(t *testing.T) {
	seen := map[string]bool{}
	preScanOut, preScanIDs := RenderDeduped("email_pre_scan", raw(t, crossDedupPreScanSharedAndUnique), width80, seen)
	for _, id := range preScanIDs {
		seen[id] = true
	}
	attentionOut, _ := RenderDeduped("email_attention", raw(t, crossDedupAttentionSharedAndUnique), width80, seen)

	combined := plain(preScanOut) + "\n" + plain(attentionOut)
	t.Logf("\n%s", combined)

	if n := strings.Count(combined, "F-Bombs"); n != 1 {
		t.Errorf("shared subject rendered %d times, want exactly 1", n)
	}
	if !strings.Contains(combined, "PreScanOnlySubject") {
		t.Error("the pre-scan card lost its own unique content")
	}
	if !strings.Contains(combined, "AttentionOnlySubject") {
		t.Error("the attention card's own unique content was dropped -- per-item dedup must not suppress the whole card when it still has something to say")
	}
	if strings.Contains(combined, "WAITING ON YOU") {
		t.Error("the WAITING ON YOU section should have been dropped -- its only item was deduped away, so an empty section must not render")
	}
	if !strings.Contains(combined, "ACTION ITEMS") {
		t.Error("the ACTION ITEMS section, whose one item is not a duplicate, should still render")
	}
	if !strings.Contains(combined, "Needs you") {
		t.Error("the attention card itself should still render -- it still has real content (the action item)")
	}
}

const crossDedupPreScanWithNullMessageID = `{
  "kind": "email_pre_scan",
  "urgent": [], "actionable": [], "informational_count": 0,
  "suggested_archives": [], "suggested_drafts": [], "needs_review": [],
  "scanned": 1,
  "needs_you": [
    {"ref":1,"kind":"urgent","message_id":null,"sender":"legal@example.com","subject":"Follow up with Legal","why":"urgent"}
  ],
  "needs_you_total": 1,
  "bulk": {"count": 0, "filter_tests": []},
  "preferences_applied": null
}`

const crossDedupAttentionActionItemsWithNullMessageID = `{
  "kind":"email_attention",
  "items":[
    {"kind":"action_item","message_id":null,"sender":"a@example.com","subject":"Renew the domain","why":"expires Friday"},
    {"kind":"action_item","message_id":null,"sender":"b@example.com","subject":"Approve the invoice","why":"awaiting sign-off"}
  ],
  "coverage":{"scanned":2,"total_unread":2},
  "generated_at":"x","cache_age_seconds":0.0,"stale":false
}`

// #2631 -- action items legitimately carry no message_id (they are not tied
// to one specific message), so an empty id must never be treated as a
// duplicate: not of a real id already in play, not of another empty-id item
// in the very same card, and -- the sharpest case -- not of an empty id an
// EARLIER card in the same turn also carried. The pre-scan fixture here has
// its own null-id item specifically to prove that last case: seen must
// never gain an "" entry that a later card's null-id items could collide
// with.
func TestCrossCardDedupNeverTreatsEmptyMessageIDsAsDuplicates(t *testing.T) {
	seen := map[string]bool{}
	preScanOut, preScanIDs := RenderDeduped("email_pre_scan", raw(t, crossDedupPreScanWithNullMessageID), width80, seen)
	for _, id := range preScanIDs {
		seen[id] = true
	}
	attentionOut, _ := RenderDeduped("email_attention", raw(t, crossDedupAttentionActionItemsWithNullMessageID), width80, seen)

	combined := plain(preScanOut) + "\n" + plain(attentionOut)
	t.Logf("\n%s", combined)

	if !strings.Contains(combined, "Follow up with Legal") {
		t.Error("the pre-scan card's own null-message_id item was dropped")
	}
	if !strings.Contains(combined, "Renew the domain") {
		t.Error("a null-message_id action item was dropped -- an empty id must never be treated as a duplicate")
	}
	if !strings.Contains(combined, "Approve the invoice") {
		t.Error("a second null-message_id action item was dropped -- empty ids must not be deduped against each other either")
	}
}
