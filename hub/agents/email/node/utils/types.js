/**
 * Schema types and constants for the email triage contract (schema 2.2).
 *
 * All types are expressed as JSDoc typedefs — no runtime overhead, full
 * editor support in VS Code / WebStorm.
 *
 * @module types
 */

const SCHEMA_VERSION = "2.2";
const MAX_BATCH_SIZE = 100;
const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;

/**
 * @typedef {'URGENT'|'NEEDS_RESPONSE'|'FYI'|'PROMOTIONAL'|'PERSONAL'} EmailCategory
 */

/**
 * @typedef {'reply'|'none'|'delete'} SuggestedAction
 */

/**
 * @typedef {'high'|'low'} ConfidenceLevel
 */

/**
 * @typedef {Object} EmailAddress
 * @property {string} [name]   - Display name (optional)
 * @property {string} email    - Email address
 */

/**
 * @typedef {Object} AttachmentMeta
 * @property {string} filename
 * @property {string} mime_type
 * @property {number} size_bytes
 * @property {string} [attachment_id]
 */

/**
 * @typedef {Object} EmailMessage
 * @property {string}           message_id
 * @property {string}           [thread_id]
 * @property {EmailAddress}     from
 * @property {EmailAddress[]}   to
 * @property {EmailAddress[]}   [cc]
 * @property {EmailAddress[]}   [bcc]
 * @property {string}           [date]
 * @property {string}           subject
 * @property {string}           body
 * @property {AttachmentMeta[]} [attachments]
 */

/**
 * @typedef {Object} SingleEmailInput
 * @property {'single'}    kind
 * @property {EmailAddress} principal
 * @property {EmailMessage} message
 */

/**
 * @typedef {Object} ThreadInput
 * @property {'thread'}     kind
 * @property {EmailAddress} principal
 * @property {string}       thread_id
 * @property {EmailMessage[]} messages - At least one message; newest-first order
 */

/**
 * @typedef {SingleEmailInput|ThreadInput} EmailInput
 */

/**
 * @typedef {Object} TriageContext
 * @property {string[]} [people]
 * @property {string[]} [projects]
 * @property {string}   [tone]
 * @property {string}   [self_email]
 */

/**
 * @typedef {Object} EmailTriageRequest
 * @property {string}       [schema_version]
 * @property {EmailInput}   payload
 * @property {TriageContext} [context]
 */

/**
 * An external link that requires user action.
 * Renders as a CTA button.
 *
 * @typedef {Object} LinkAction
 * @property {'link'}  type
 * @property {string}  description - Short description of what the link does, e.g. "View pull request"
 * @property {string}  cta         - Verb label for the button, e.g. "View", "Approve", "Download"
 * @property {string}  url
 */

/**
 * A ready-to-send reply composed from the email context.
 * Recipients are derived from the original message + principal context.
 *
 * @typedef {Object} SendReplyAction
 * @property {'send_reply'}   type
 * @property {EmailAddress[]} to
 * @property {EmailAddress[]} [cc]
 * @property {EmailAddress[]} [bcc]
 * @property {string}         body - Full ready-to-send reply body
 */

/**
 * Suggest deleting the email.
 * Generated for spam, phishing, and confident PROMOTIONAL classification.
 *
 * @typedef {Object} DeleteAction
 * @property {'delete'} type
 * @property {string}   [reason]
 */

/**
 * Create or RSVP to a calendar event derived from the email.
 *
 * @typedef {Object} CalendarEventAction
 * @property {'calendar_event'} type
 * @property {string}  title
 * @property {string}  start       - ISO datetime of start
 * @property {string}  [end]
 * @property {string}  [description]
 * @property {string}  [location]
 * @property {boolean} [rsvp]      - true = accept, false = decline; omit when no RSVP decision is needed
 */

/**
 * One-click unsubscribe link extracted from a promotional email.
 * Distinct from `delete` because it's a web action, not just removing the email.
 *
 * @typedef {Object} UnsubscribeAction
 * @property {'unsubscribe'} type
 * @property {string}        url
 */

/**
 * @typedef {LinkAction|SendReplyAction|DeleteAction|CalendarEventAction|UnsubscribeAction} ActionItem
 */

/**
 * @typedef {'link'|'send_reply'|'delete'|'calendar_event'|'unsubscribe'} ActionItemType
 */

/**
 * An action item with a deterministic priority rank.
 * `priority === 0` is the highest-value action for the current email category
 * and is the one to surface as the single primary CTA in a compact UI.
 *
 * @typedef {ActionItem & { priority: number }} RankedActionItem
 */

/**
 * @typedef {Object} StepUsage
 * @property {number} prompt_tokens
 * @property {number} completion_tokens
 * @property {number} total_tokens
 */

/**
 * @typedef {Object} TriageUsage
 * @property {number} prompt_tokens
 * @property {number} completion_tokens
 * @property {number} total_tokens
 * @property {number} [tokens_per_second]
 * @property {Record<string, StepUsage>} [steps] - Per-step breakdown; key is the step label (e.g. "classify", "summarize")
 */

/**
 * @typedef {Object} EmailTriageResult
 * @property {EmailCategory}      category
 * @property {boolean}            is_spam
 * @property {boolean}            is_phishing
 * @property {string}             summary
 * @property {RankedActionItem[]} action_items   - Actions sorted by priority ascending — index 0 is the best action.
 * @property {RankedActionItem}   [primary_action] - The single highest-priority action — render as the primary CTA button.
 * @property {string}             [due]          - ISO 8601 deadline. Only for URGENT/NEEDS_RESPONSE with an explicit due date.
 * @property {string}             [message_id]   - Echoed from input; `thread_id` for thread requests
 * @property {TriageUsage}        [usage]
 * @property {AttachmentMeta[]}   [attachments]
 */

/**
 * @typedef {Object} EmailTriageResponse
 * @property {string}           schema_version
 * @property {'single'|'thread'} request_kind
 * @property {EmailTriageResult} result
 */

/**
 * @typedef {Object} BatchTriageRequest
 * @property {EmailInput[]}   items
 * @property {TriageContext}  [context]
 */

/**
 * @typedef {Object} BatchItemError
 * @property {string} message
 */

/**
 * A thread or message that was intentionally not triaged.
 * Included in batch results so every input index is accounted for.
 *
 * @typedef {Object} SkippedResult
 * @property {'skipped'} kind
 * @property {string}    reason
 */

/**
 * @typedef {Object} BatchItemResultOk
 * @property {number}          index
 * @property {EmailTriageResult} result
 */

/**
 * @typedef {Object} BatchItemResultSkipped
 * @property {number}        index
 * @property {SkippedResult} skipped
 */

/**
 * @typedef {Object} BatchItemResultError
 * @property {number}         index
 * @property {BatchItemError} error
 */

/**
 * @typedef {BatchItemResultOk|BatchItemResultSkipped|BatchItemResultError} BatchItemResult
 */

/**
 * @typedef {Object} BatchTriageResponse
 * @property {BatchItemResult[]} results
 */

/**
 * @typedef {Object} HeuristicResult
 * @property {EmailCategory} category
 * @property {boolean}       is_spam
 * @property {boolean}       spam_confident
 * @property {boolean}       is_phishing
 * @property {boolean}       confident     - When true the caller skips the LLM classify step
 * @property {string}        reason
 */

/**
 * Whether the principal was a direct recipient or only CC'd.
 *
 * @typedef {'primary'|'cc'|'bcc'|'unknown'} RecipientRole
 */

module.exports = {
  SCHEMA_VERSION,
  MAX_BATCH_SIZE,
  MAX_ATTACHMENT_BYTES,
};
