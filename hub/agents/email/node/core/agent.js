/**
 * EmailTriageAgent — zero-server, per-step LLM pipeline.
 *
 * Pipeline per email
 * ──────────────────
 *   1. Heuristic classify   (zero LLM calls — string matching)
 *      Short-circuit:  definite spam or phishing → return archive action, done.
 *      Skip classify:  heuristic is confident about category → skip step 2.
 *
 *   2. LLM classify         (forced tool call — enum validated by API)
 *      Skipped when heuristic is confident (unless forceLlmClassify = true).
 *
 *   3. LLM summarize        (plain text completion — budget-aware for threads)
 *      The content budget is derived from contextWindowTokens so the model
 *      never receives more than it can handle.
 *
 *   4. LLM extract actions  (two-phase forced tool calls)
 *      Receives category + summary as context so it makes informed decisions
 *      about draft replies, archive, calendar events, etc.
 *
 * Max 3 LLM calls per email. Each prompt is focused with no tool-schema bloat.
 */

const { SCHEMA_VERSION } = require("../utils/types.js");
const {
  classifyHeuristic,
  extractSigningUrl,
  extractUnsubscribeUrl,
} = require("../utils/heuristics.js");
const { classifyEmail } = require("./steps/classify.js");
const { summariseEmail, summariseThread } = require("./steps/summarize.js");
const { extractActions } = require("./steps/actions.js");
const { extractDueDate } = require("./steps/due.js");
const { rankActions } = require("../utils/rank.js");
const { LlmClient, mergeUsage, runWithEmailTag } = require("./llm.js");

/**
 * @typedef {import('./llm.js').LlmClientConfig & {
 *   contextWindowTokens?: number,
 *   forceLlmClassify?: boolean,
 *   now?: string,
 *   userContext?: string,
 *   maxActionItems?: number,
 *   aliases?: string[],
 *   advancedUsage?: boolean,
 *   batchConcurrency?: number,
 * }} AgentConfig
 */

const CONTENT_BUDGET_RATIO = 0.55;
const CHARS_PER_TOKEN = 3;

/**
 * @template T, R
 * @param {T[]} items
 * @param {number} limit
 * @param {(item: T, index: number) => Promise<R>} fn
 * @returns {Promise<R[]>}
 */
async function runWithConcurrency(items, limit, fn) {
  if (limit <= 0 || limit >= items.length) {
    return Promise.all(items.map((item, i) => fn(item, i)));
  }
  const results = new Array(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const i = next++;
      results[i] = await fn(items[i], i);
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, worker)
  );
  return results;
}

/**
 * @param {import('../utils/types.js').EmailInput} item
 * @returns {string}
 */
function batchItemLabel(item) {
  if (item.kind === "thread") {
    const newest = item.messages[item.messages.length - 1];
    return `thread ${item.thread_id.slice(-8)} | "${truncateLabel(newest.subject)}" from ${newest.from.email}`;
  }
  return `single | "${truncateLabel(item.message.subject)}" from ${item.message.from.email}`;
}

/**
 * @param {string} s
 * @param {number} [max]
 * @returns {string}
 */
function truncateLabel(s, max = 60) {
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`;
}

class EmailTriageAgent {
  /**
   * @param {AgentConfig} config
   */
  constructor(config) {
    /** @type {LlmClient} */
    this._llm = new LlmClient(config);
    const windowTokens = config.contextWindowTokens ?? 16_384;
    /** @type {number} */
    this._contentBudgetChars = Math.floor(
      windowTokens * CONTENT_BUDGET_RATIO * CHARS_PER_TOKEN
    );
    /** @type {boolean} */
    this._forceLlmClassify = config.forceLlmClassify ?? false;
    /** @type {string} */
    this._now = config.now ?? new Date().toISOString();
    /** @type {string|undefined} */
    this._userContext = config.userContext;
    /** @type {number} */
    this._maxActionItems = config.maxActionItems ?? 5;
    /** @type {boolean} */
    this._debug = config.debug ?? false;
    /** @type {boolean} */
    this._advancedUsage = config.advancedUsage ?? false;
    /** @type {number} */
    this._batchConcurrency = config.batchConcurrency ?? 1;
    /** @type {Set<string>} */
    this._principalAddresses = new Set(
      (config.aliases ?? []).map((a) => a.toLowerCase())
    );
  }

  /**
   * Triage a single email or thread.
   *
   * @param {import('../utils/types.js').EmailTriageRequest} request
   * @returns {Promise<import('../utils/types.js').EmailTriageResponse | import('../utils/types.js').SkippedResult>}
   */
  async triage(request) {
    const result = await this._triageInput(request.payload, request.context);
    if (
      result &&
      typeof result === "object" &&
      "kind" in result &&
      result.kind === "skipped"
    ) {
      return result;
    }
    return {
      schema_version: SCHEMA_VERSION,
      request_kind: request.payload.kind === "thread" ? "thread" : "single",
      result,
    };
  }

  /**
   * Triage a batch of up to 100 emails concurrently.
   *
   * @param {import('../utils/types.js').BatchTriageRequest} request
   * @returns {Promise<import('../utils/types.js').BatchTriageResponse>}
   */
  async triageBatch(request) {
    const total = request.items.length;
    const settled = await runWithConcurrency(
      request.items,
      this._batchConcurrency,
      async (item, index) => {
        const tag = `${index + 1}/${total}`;
        const label = batchItemLabel(item);
        console.log(`\n[batch ${tag}] ▶ ${label}`);
        const t0 = Date.now();
        return runWithEmailTag(tag, async () => {
          try {
            const result = await this._triageInput(item, request.context);
            const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
            if (
              result &&
              typeof result === "object" &&
              "kind" in result &&
              result.kind === "skipped"
            ) {
              console.log(
                `[batch ${tag}] ↷ skipped (${result.reason}) in ${elapsed}s\n`
              );
              return { index, skipped: result };
            }
            const r = result;
            const tok = r.usage?.total_tokens
              ? ` ${r.usage.total_tokens}tok`
              : "";
            console.log(
              `[batch ${tag}] ✓ ${r.category} in ${elapsed}s${tok}\n`
            );
            return { index, result: r };
          } catch (err) {
            const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
            const msg = err instanceof Error ? err.message : String(err);
            console.log(`[batch ${tag}] ✗ error in ${elapsed}s — ${msg}\n`);
            return { index, error: { message: msg } };
          }
        });
      }
    );
    return { results: settled };
  }

  /**
   * @param {import('../utils/types.js').EmailInput} input
   * @param {import('../utils/types.js').TriageContext} [context]
   * @returns {Promise<import('../utils/types.js').EmailTriageResult | import('../utils/types.js').SkippedResult>}
   */
  async _triageInput(input, context) {
    if (input.kind === "thread") {
      return this._triageThread(
        input.principal,
        input.thread_id,
        input.messages,
        context
      );
    }
    return this._triageSingle(input.principal, input.message, context);
  }

  /**
   * @param {import('../utils/types.js').EmailAddress} principal
   * @param {import('../utils/types.js').EmailMessage} message
   * @param {import('../utils/types.js').TriageContext} [context]
   * @returns {Promise<import('../utils/types.js').EmailTriageResult>}
   */
  async _triageSingle(principal, message, context) {
    /** @type {(import('../utils/types.js').TriageUsage | undefined)[]} */
    const usages = [];

    const heuristic = classifyHeuristic(
      message.subject,
      message.from,
      message.body
    );

    if (heuristic.confident && (heuristic.is_spam || heuristic.is_phishing)) {
      return buildShortCircuitResult(
        message,
        heuristic.is_spam,
        heuristic.is_phishing,
        undefined,
        heuristic.reason
      );
    }

    let category = heuristic.category;
    let is_spam = heuristic.is_spam;
    let is_phishing = heuristic.is_phishing;
    /** @type {string | undefined} */
    let category_reason = heuristic.confident ? heuristic.reason : undefined;

    if (!heuristic.confident || this._forceLlmClassify) {
      const classified = await classifyEmail(
        this._llm,
        message.subject,
        message.from,
        message.body,
        context,
        this._userContext
      );
      category = classified.category;
      category_reason = classified.category_reason;
      is_spam = is_spam || classified.is_spam;
      is_phishing = is_phishing || classified.is_phishing;
      usages.push(classified.usage);
    }

    const recipientRole = deriveRecipientRole(message, principal);

    const fastActions = !this._forceLlmClassify
      ? buildFastPathActions(category, heuristic.confident, message.body)
      : null;

    const [{ summary, usage: sumUsage }, dueResult] = await Promise.all([
      summariseEmail(
        this._llm,
        message,
        principal,
        recipientRole,
        this._contentBudgetChars
      ),
      category === "URGENT" || category === "NEEDS_RESPONSE"
        ? extractDueDate(this._llm, message, message.date, this._now)
        : Promise.resolve({ due: undefined, usage: undefined }),
    ]);
    usages.push(sumUsage, dueResult.usage);

    let rawActions;
    const signingUrl = extractSigningUrl(message.body);
    if (signingUrl !== null) {
      category = "URGENT";
      category_reason = `document signing URL detected (${signingUrl.service})`;
      rawActions = [
        {
          type: "link",
          description: `Sign via ${signingUrl.service}`,
          cta: "Sign",
          url: signingUrl.url,
        },
      ];
    } else if (fastActions !== null) {
      rawActions = fastActions;
    } else {
      const { items, usage: actUsage } = await extractActions(
        this._llm,
        message,
        principal,
        category,
        summary,
        is_spam,
        is_phishing,
        recipientRole,
        this._userContext,
        this._now
      );
      rawActions = items;
      usages.push(actUsage);
    }

    const action_items = rankActions(rawActions, category).slice(
      0,
      this._maxActionItems
    );

    const usage = mergeUsage(...usages);
    if (!this._advancedUsage) delete usage.steps;

    return {
      category,
      category_reason,
      is_spam,
      is_phishing,
      summary,
      action_items,
      primary_action: action_items[0],
      ...(dueResult.due ? { due: dueResult.due } : {}),
      message_id: message.message_id,
      usage,
    };
  }

  /**
   * @param {import('../utils/types.js').EmailAddress} principal
   * @param {string} threadId
   * @param {import('../utils/types.js').EmailMessage[]} messages
   * @param {import('../utils/types.js').TriageContext} [context]
   * @returns {Promise<import('../utils/types.js').EmailTriageResult | import('../utils/types.js').SkippedResult>}
   */
  async _triageThread(principal, threadId, messages, context) {
    /** @type {(import('../utils/types.js').TriageUsage | undefined)[]} */
    const usages = [];

    const newest = messages[messages.length - 1];

    const ownAddresses = new Set([
      principal.email.toLowerCase(),
      ...this._principalAddresses,
    ]);
    if (this._debug) {
      console.log(
        `[agent-email] thread=${threadId}` +
          ` newest_from=${newest.from.email}` +
          ` own=${[...ownAddresses].join(",")}`
      );
    }
    if (ownAddresses.has(newest.from.email.toLowerCase())) {
      console.log(
        `[agent-email] skip thread=${threadId} reason="already replied"` +
          ` last_sender=${newest.from.email}`
      );
      return { kind: "skipped", reason: "already replied" };
    }

    const heuristic = classifyHeuristic(
      newest.subject,
      newest.from,
      newest.body,
      true
    );

    if (heuristic.confident && (heuristic.is_spam || heuristic.is_phishing)) {
      return buildShortCircuitResult(
        newest,
        heuristic.is_spam,
        heuristic.is_phishing,
        threadId,
        heuristic.reason
      );
    }

    let category = heuristic.category;
    let is_spam = heuristic.is_spam;
    let is_phishing = heuristic.is_phishing;
    /** @type {string | undefined} */
    let category_reason = heuristic.confident ? heuristic.reason : undefined;

    if (!heuristic.confident || this._forceLlmClassify) {
      const classified = await classifyEmail(
        this._llm,
        newest.subject,
        newest.from,
        newest.body,
        context,
        this._userContext
      );
      category = classified.category;
      category_reason = classified.category_reason;
      is_spam = is_spam || classified.is_spam;
      is_phishing = is_phishing || classified.is_phishing;
      usages.push(classified.usage);
    }

    const recipientRole = deriveRecipientRole(newest, principal);

    const fastActions = !this._forceLlmClassify
      ? buildFastPathActions(category, heuristic.confident, newest.body)
      : null;

    const [{ summary, usage: sumUsage }, dueResult] = await Promise.all([
      summariseThread(
        this._llm,
        [...messages],
        principal,
        recipientRole,
        this._contentBudgetChars
      ),
      category === "URGENT" || category === "NEEDS_RESPONSE"
        ? extractDueDate(this._llm, newest, newest.date, this._now)
        : Promise.resolve({ due: undefined, usage: undefined }),
    ]);
    usages.push(sumUsage, dueResult.usage);

    let rawActions;
    const signingUrl = extractSigningUrl(newest.body);
    if (signingUrl !== null) {
      category = "URGENT";
      category_reason = `document signing URL detected (${signingUrl.service})`;
      rawActions = [
        {
          type: "link",
          description: `Sign via ${signingUrl.service}`,
          cta: "Sign",
          url: signingUrl.url,
        },
      ];
    } else if (fastActions !== null) {
      rawActions = fastActions;
    } else {
      const { items, usage: actUsage } = await extractActions(
        this._llm,
        newest,
        principal,
        category,
        summary,
        is_spam,
        is_phishing,
        recipientRole,
        this._userContext,
        this._now
      );
      rawActions = items;
      usages.push(actUsage);
    }

    const action_items = rankActions(rawActions, category).slice(
      0,
      this._maxActionItems
    );

    const usage = mergeUsage(...usages);
    if (!this._advancedUsage) delete usage.steps;

    return {
      category,
      category_reason,
      is_spam,
      is_phishing,
      summary,
      action_items,
      primary_action: action_items[0],
      ...(dueResult.due ? { due: dueResult.due } : {}),
      message_id: threadId,
      usage,
    };
  }
}

/**
 * @param {import('../utils/types.js').EmailMessage} message
 * @param {boolean} is_spam
 * @param {boolean} is_phishing
 * @param {string} [overrideId]
 * @param {string} [category_reason]
 * @returns {import('../utils/types.js').EmailTriageResult}
 */
function buildShortCircuitResult(
  message,
  is_spam,
  is_phishing,
  overrideId,
  category_reason
) {
  const action_items = rankActions(
    [
      {
        type: "delete",
        reason: is_phishing ? "phishing attempt detected" : "spam",
      },
    ],
    is_phishing ? "URGENT" : "PROMOTIONAL"
  );
  return {
    category: is_phishing ? "URGENT" : "PROMOTIONAL",
    category_reason,
    is_spam,
    is_phishing,
    summary: is_phishing
      ? "This email appears to be a phishing attempt."
      : "This email appears to be spam.",
    action_items,
    primary_action: action_items[0],
    message_id: overrideId ?? message.message_id,
  };
}

/**
 * @param {import('../utils/types.js').EmailCategory} category
 * @param {boolean} heuristicConfident
 * @param {string} body
 * @returns {import('../utils/types.js').ActionItem[] | null}
 */
function buildFastPathActions(category, heuristicConfident, body) {
  if (!heuristicConfident) return null;

  if (category === "PROMOTIONAL") {
    const unsubUrl = extractUnsubscribeUrl(body);
    /** @type {import('../utils/types.js').ActionItem[]} */
    const actions = [];
    if (unsubUrl) actions.push({ type: "unsubscribe", url: unsubUrl });
    actions.push({ type: "delete" });
    return actions;
  }

  if (category === "FYI") {
    return [{ type: "delete" }];
  }

  return null;
}

/**
 * @param {import('../utils/types.js').EmailMessage} message
 * @param {import('../utils/types.js').EmailAddress} principal
 * @returns {import('../utils/types.js').RecipientRole}
 */
function deriveRecipientRole(message, principal) {
  const target = principal.email.toLowerCase();
  if (message.to.some((a) => a.email.toLowerCase() === target))
    return "primary";
  if (message.cc?.some((a) => a.email.toLowerCase() === target)) return "cc";
  if (message.bcc?.some((a) => a.email.toLowerCase() === target)) return "bcc";
  return "unknown";
}

module.exports = { EmailTriageAgent };
