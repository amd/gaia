/**
 * Deterministic action item ranker.
 *
 * Assigns a `priority` value to each action (0 = highest) based on the
 * email's category. The ranked list is sorted so `action_items[0]` is always
 * the best single action to surface in a compact UI.
 */

/**
 * @type {Record<import('./types.js').EmailCategory, import('./types.js').ActionItemType[]>}
 */
const PRIORITY_ORDER = {
  PRIORITY: ["send_reply", "calendar_event", "link", "delete", "unsubscribe"],
  URGENT: ["send_reply", "calendar_event", "link", "delete", "unsubscribe"],
  NEEDS_RESPONSE: [
    "send_reply",
    "calendar_event",
    "link",
    "delete",
    "unsubscribe",
  ],
  FYI: ["calendar_event", "link", "delete", "send_reply", "unsubscribe"],
  PROMOTIONAL: [
    "unsubscribe",
    "delete",
    "link",
    "send_reply",
    "calendar_event",
  ],
  PERSONAL: ["send_reply", "link", "calendar_event", "delete", "unsubscribe"],
};

const FALLBACK_PRIORITY = 99;

const NO_AUTO_DELETE = new Set(["PRIORITY", "URGENT", "NEEDS_RESPONSE"]);

/**
 * Attach a `priority` value to each action item and sort the list so the
 * best action for this category is at index 0.
 *
 * @param {import('./types.js').ActionItem[]} items
 * @param {import('./types.js').EmailCategory} category
 * @returns {import('./types.js').RankedActionItem[]}
 */
function rankActions(items, category) {
  const order = PRIORITY_ORDER[category] ?? [];
  const hasDelete = items.some((i) => i.type === "delete");
  if (!hasDelete && !NO_AUTO_DELETE.has(category)) {
    items = [...items, { type: "delete", reason: "Clean up inbox" }];
  }
  return items
    .map((item) => ({ ...item, priority: priorityFor(item.type, order) }))
    .sort((a, b) => a.priority - b.priority);
}

/**
 * @param {import('./types.js').ActionItemType} type
 * @param {import('./types.js').ActionItemType[]} order
 * @returns {number}
 */
function priorityFor(type, order) {
  const idx = order.indexOf(type);
  return idx === -1 ? FALLBACK_PRIORITY : idx;
}

module.exports = {
  rankActions,
};
