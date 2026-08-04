/**
 * Step 3 — Two-phase action extraction.
 *
 * Phase 1 — select_actions (forced tool call)
 *   Pure enum selection. The model picks which action types apply.
 *
 * Phase 2 — per-action body calls (parallel forced tool calls)
 *   One focused tool call per selected type.
 */

const {
  COMPOSE_DRAFT_SYSTEM_PROMPT,
  EXTRACT_CALENDAR_EVENT_SYSTEM_PROMPT,
  EXTRACT_LINKS_SYSTEM_PROMPT,
  EXTRACT_UNSUBSCRIBE_SYSTEM_PROMPT,
  SELECT_ACTIONS_SYSTEM_PROMPT,
  withUserContext,
  wrapUntrusted,
} = require("../../utils/prompts.js");
const {
  COMPOSE_DRAFT_TOOL,
  EXTRACT_CALENDAR_EVENT_TOOL,
  EXTRACT_LINKS_TOOL,
  EXTRACT_UNSUBSCRIBE_TOOL,
  SELECT_ACTIONS_TOOL,
} = require("../../utils/tools.js");

/**
 * Run the full two-phase action extraction pipeline.
 *
 * @param {import('../llm.js').LlmClient} client
 * @param {import('../../utils/types.js').EmailMessage} message
 * @param {import('../../utils/types.js').EmailAddress} principal
 * @param {import('../../utils/types.js').EmailCategory} category
 * @param {string} summary
 * @param {boolean} is_spam
 * @param {boolean} is_phishing
 * @param {import('../../utils/types.js').RecipientRole} recipientRole
 * @param {string} [userContext]
 * @param {string} [triageDate]
 * @returns {Promise<{ items: import('../../utils/types.js').ActionItem[], usage: import('../../utils/types.js').TriageUsage }>}
 */
async function extractActions(
  client,
  message,
  principal,
  category,
  summary,
  is_spam,
  is_phishing,
  recipientRole,
  userContext,
  triageDate
) {
  const usages = [];

  const selectMsg = buildSelectMessage(
    message,
    principal,
    category,
    summary,
    is_spam,
    is_phishing,
    recipientRole
  );

  const { data: selectData, usage: selectUsage } = await client.forcedToolCall(
    withUserContext(SELECT_ACTIONS_SYSTEM_PROMPT, userContext),
    selectMsg,
    SELECT_ACTIONS_TOOL,
    "actions:select"
  );
  usages.push(selectUsage);

  /** @type {import('../../utils/types.js').EmailCategory[]} */
  const SEND_REPLY_ALLOWED_CATEGORIES = [
    "NEEDS_RESPONSE",
    "URGENT",
    "PERSONAL",
  ];
  /** @type {import('../../utils/types.js').EmailCategory[]} */
  const CALENDAR_ALLOWED_CATEGORIES = [
    "NEEDS_RESPONSE",
    "URGENT",
    "PERSONAL",
    "FYI",
  ];

  const selectedTypes = dedupeTypes(selectData.actions ?? []).filter((t) => {
    if (t === "send_reply" && !SEND_REPLY_ALLOWED_CATEGORIES.includes(category))
      return false;
    if (
      t === "calendar_event" &&
      !CALENDAR_ALLOWED_CATEGORIES.includes(category)
    )
      return false;
    return true;
  });

  if (selectedTypes.length === 0) {
    return { items: [], usage: mergeUsages(usages) };
  }

  /** @type {import('../../utils/types.js').ActionItem[]} */
  const items = [];
  if (selectedTypes.includes("delete")) {
    /** @type {import('../../utils/types.js').DeleteAction} */
    const action = {
      type: "delete",
      ...(is_spam ? { reason: "spam" } : {}),
      ...(is_phishing ? { reason: "phishing attempt" } : {}),
    };
    items.push(action);
  }

  const bodyTypes = selectedTypes.filter((t) => t !== "delete");
  const emailContext = buildEmailContext(
    message,
    principal,
    summary,
    recipientRole,
    triageDate
  );

  const phase2 = await Promise.all(
    bodyTypes.map(async (type) => {
      try {
        return await fillActionBody(
          client,
          type,
          message,
          principal,
          emailContext,
          userContext
        );
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        process.stderr.write(
          `[agent-email:actions] warn: skipping ${type} — ${msg}\n`
        );
        return { items: [], usage: emptyUsage() };
      }
    })
  );

  for (const result of phase2) {
    items.push(...result.items);
    usages.push(result.usage);
  }

  return { items, usage: mergeUsages(usages) };
}

/**
 * @param {import('../llm.js').LlmClient} client
 * @param {import('../../utils/types.js').ActionItemType} type
 * @param {import('../../utils/types.js').EmailMessage} message
 * @param {import('../../utils/types.js').EmailAddress} principal
 * @param {string} emailContext
 * @param {string} [userContext]
 * @returns {Promise<{ items: import('../../utils/types.js').ActionItem[], usage: import('../../utils/types.js').TriageUsage }>}
 */
async function fillActionBody(
  client,
  type,
  message,
  principal,
  emailContext,
  userContext
) {
  switch (type) {
    case "send_reply":
      return fillDraft(client, message, principal, emailContext, userContext);
    case "link":
      return fillLinks(client, message, emailContext);
    case "calendar_event":
      return fillCalendarEvent(client, emailContext);
    case "unsubscribe":
      return fillUnsubscribe(client, emailContext);
    default:
      return { items: [], usage: emptyUsage() };
  }
}

/**
 * @param {import('../llm.js').LlmClient} client
 * @param {import('../../utils/types.js').EmailMessage} message
 * @param {import('../../utils/types.js').EmailAddress} principal
 * @param {string} emailContext
 * @param {string} [userContext]
 * @returns {Promise<{ items: import('../../utils/types.js').SendReplyAction[], usage: import('../../utils/types.js').TriageUsage }>}
 */
async function fillDraft(
  client,
  message,
  principal,
  emailContext,
  userContext
) {
  const userMsg = [
    emailContext,
    "",
    `Replying as: ${formatAddr(principal)}`,
    `Original From: ${formatAddr(message.from)}`,
    `Original To: ${message.to.map(formatAddr).join(", ")}`,
    ...(message.cc?.length
      ? [`Original CC: ${message.cc.map(formatAddr).join(", ")}`]
      : []),
  ].join("\n");

  const { data, usage } = await client.forcedToolCall(
    withUserContext(COMPOSE_DRAFT_SYSTEM_PROMPT, userContext),
    userMsg,
    COMPOSE_DRAFT_TOOL,
    "actions:draft"
  );

  if (!data.body) return { items: [], usage };

  /** @type {import('../../utils/types.js').EmailAddress[]} */
  const replyTo = [message.from];

  const excludeEmails = new Set([
    principal.email.toLowerCase(),
    message.from.email.toLowerCase(),
  ]);
  const cc = normaliseAddrs(data.cc ?? []).filter(
    (a) => !excludeEmails.has(a.email.toLowerCase())
  );
  const bcc = normaliseAddrs(data.bcc ?? []).filter(
    (a) => !excludeEmails.has(a.email.toLowerCase())
  );

  return {
    items: [
      {
        type: "send_reply",
        to: replyTo,
        ...(cc.length ? { cc } : {}),
        ...(bcc.length ? { bcc } : {}),
        body: data.body,
      },
    ],
    usage,
  };
}

const URL_REGEX = /https?:\/\/[^\s<>"')\]]+/gi;

/**
 * @param {string} body
 * @returns {string[]}
 */
function extractRawUrls(body) {
  const matches = body.match(URL_REGEX) ?? [];
  const cleaned = matches.map((u) => u.replace(/[.,;:!?]+$/, ""));
  return [...new Set(cleaned)];
}

/**
 * @param {import('../llm.js').LlmClient} client
 * @param {import('../../utils/types.js').EmailMessage} message
 * @param {string} emailContext
 * @returns {Promise<{ items: import('../../utils/types.js').LinkAction[], usage: import('../../utils/types.js').TriageUsage }>}
 */
async function fillLinks(client, message, emailContext) {
  const candidates = extractRawUrls(message.body);
  if (candidates.length === 0) {
    return { items: [], usage: emptyUsage() };
  }

  const candidateBlock = candidates
    .map((url, i) => `${i + 1}. ${url}`)
    .join("\n");

  const userMsg = [
    emailContext,
    "",
    "--- URLs found verbatim in this email ---",
    candidateBlock,
  ].join("\n");

  const { data, usage } = await client.forcedToolCall(
    EXTRACT_LINKS_SYSTEM_PROMPT,
    userMsg,
    EXTRACT_LINKS_TOOL,
    "actions:links"
  );

  const candidateSet = new Set(candidates);

  /** @type {import('../../utils/types.js').LinkAction[]} */
  const items = (data.items ?? [])
    .filter((l) => {
      if (!l.description || !l.cta || !l.url) return false;
      if (!candidateSet.has(l.url)) {
        process.stderr.write(
          `[agent-email:links] warn: dropping hallucinated URL not in body: ${l.url}\n`
        );
        return false;
      }
      return true;
    })
    .map((l) => ({
      type: "link",
      description: l.description,
      cta: l.cta,
      url: l.url,
    }));

  return { items, usage };
}

/**
 * @param {import('../llm.js').LlmClient} client
 * @param {string} emailContext
 * @returns {Promise<{ items: import('../../utils/types.js').CalendarEventAction[], usage: import('../../utils/types.js').TriageUsage }>}
 */
async function fillCalendarEvent(client, emailContext) {
  const { data, usage } = await client.forcedToolCall(
    EXTRACT_CALENDAR_EVENT_SYSTEM_PROMPT,
    emailContext,
    EXTRACT_CALENDAR_EVENT_TOOL,
    "actions:calendar"
  );

  if (!data.title || !data.start) return { items: [], usage };

  let start = data.start;
  let end = data.end || null;
  if (start !== "TBD") {
    const parsed = Date.parse(start);
    if (!isNaN(parsed) && parsed < Date.now()) {
      start = "TBD";
    }
  }

  if (start === "TBD" && end && end !== "TBD") {
    const parsedEnd = Date.parse(end);
    if (!isNaN(parsedEnd)) {
      start = new Date(parsedEnd - 3600_000).toISOString();
    }
  } else if (start !== "TBD" && (!end || end === "TBD")) {
    const parsedStart = Date.parse(start);
    if (!isNaN(parsedStart)) {
      end = new Date(parsedStart + 3600_000).toISOString();
    }
  }

  return {
    items: [
      {
        type: "calendar_event",
        title: data.title,
        start,
        ...(end ? { end } : {}),
        ...(data.location ? { location: data.location } : {}),
        ...(data.description ? { description: data.description } : {}),
        ...(data.rsvp !== undefined ? { rsvp: data.rsvp } : {}),
      },
    ],
    usage,
  };
}

/**
 * @param {import('../llm.js').LlmClient} client
 * @param {string} emailContext
 * @returns {Promise<{ items: import('../../utils/types.js').UnsubscribeAction[], usage: import('../../utils/types.js').TriageUsage }>}
 */
async function fillUnsubscribe(client, emailContext) {
  const { data, usage } = await client.forcedToolCall(
    EXTRACT_UNSUBSCRIBE_SYSTEM_PROMPT,
    emailContext,
    EXTRACT_UNSUBSCRIBE_TOOL,
    "actions:unsubscribe"
  );

  if (!data.url) return { items: [], usage };
  return { items: [{ type: "unsubscribe", url: data.url }], usage };
}

/**
 * @param {import('../../utils/types.js').EmailMessage} message
 * @param {import('../../utils/types.js').EmailAddress} principal
 * @param {import('../../utils/types.js').EmailCategory} category
 * @param {string} summary
 * @param {boolean} is_spam
 * @param {boolean} is_phishing
 * @param {import('../../utils/types.js').RecipientRole} recipientRole
 * @returns {string}
 */
function buildSelectMessage(
  message,
  principal,
  category,
  summary,
  is_spam,
  is_phishing,
  recipientRole
) {
  const flags = [is_spam && "SPAM", is_phishing && "PHISHING"]
    .filter(Boolean)
    .join(", ");
  const roleLabel = roleDescription(recipientRole, principal);

  return [
    `From: ${formatAddr(message.from)}`,
    `To: ${message.to.map(formatAddr).join(", ")}`,
    ...(message.cc?.length
      ? [`CC: ${message.cc.map(formatAddr).join(", ")}`]
      : []),
    `Subject: ${message.subject}`,
    `Category: ${category}${flags ? ` [${flags}]` : ""}`,
    `Summary: ${summary}`,
    `Principal: ${roleLabel}`,
    "",
    wrapUntrusted(message.body),
  ].join("\n");
}

/**
 * @param {import('../../utils/types.js').EmailMessage} message
 * @param {import('../../utils/types.js').EmailAddress} principal
 * @param {string} summary
 * @param {import('../../utils/types.js').RecipientRole} recipientRole
 * @param {string} [triageDate]
 * @returns {string}
 */
function buildEmailContext(
  message,
  principal,
  summary,
  recipientRole,
  triageDate
) {
  let dateLines = [];
  if (triageDate) {
    const d = new Date(triageDate);
    const iso = d.toISOString().slice(0, 10);
    const human = d.toLocaleDateString("en-US", {
      weekday: "long",
      year: "numeric",
      month: "long",
      day: "numeric",
      timeZone: "UTC",
    });
    const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const upcoming = Array.from({ length: 7 }, (_, i) => {
      const future = new Date(d);
      future.setUTCDate(future.getUTCDate() + i);
      return `${dayNames[future.getUTCDay()]} = ${future.toISOString().slice(0, 10)}`;
    });
    dateLines = [
      `Today: ${human} (${iso})`,
      `This week: ${upcoming.join(", ")}`,
    ];
  }
  return [
    ...dateLines,
    ...(message.date ? [`Message date: ${message.date}`] : []),
    `From: ${formatAddr(message.from)}`,
    `To: ${message.to.map(formatAddr).join(", ")}`,
    ...(message.cc?.length
      ? [`CC: ${message.cc.map(formatAddr).join(", ")}`]
      : []),
    `Subject: ${message.subject}`,
    `Summary: ${summary}`,
    `Principal: ${roleDescription(recipientRole, principal)}`,
    "",
    wrapUntrusted(message.body),
  ].join("\n");
}

/**
 * @param {import('../../utils/types.js').EmailAddress} addr
 * @returns {string}
 */
function formatAddr(addr) {
  return addr.name ? `${addr.name} <${addr.email}>` : addr.email;
}

/**
 * @param {Array<{ name?: string, email: string }>} raw
 * @returns {import('../../utils/types.js').EmailAddress[]}
 */
function normaliseAddrs(raw) {
  return raw
    .filter((a) => a?.email)
    .map((a) => ({ email: a.email, ...(a.name ? { name: a.name } : {}) }));
}

/**
 * @param {import('../../utils/types.js').RecipientRole} role
 * @param {import('../../utils/types.js').EmailAddress} principal
 * @returns {string}
 */
function roleDescription(role, principal) {
  const addr = formatAddr(principal);
  if (role === "primary") return `${addr} (primary TO recipient)`;
  if (role === "cc") return `${addr} (CC'd — being kept in the loop)`;
  if (role === "bcc") return `${addr} (BCC'd)`;
  return addr;
}

/**
 * @param {import('../../utils/types.js').ActionItemType[]} types
 * @returns {import('../../utils/types.js').ActionItemType[]}
 */
function dedupeTypes(types) {
  return [...new Set(types)];
}

/**
 * @returns {import('../../utils/types.js').TriageUsage}
 */
function emptyUsage() {
  return { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
}

/**
 * @param {import('../../utils/types.js').TriageUsage[]} usages
 * @returns {import('../../utils/types.js').TriageUsage}
 */
function mergeUsages(usages) {
  return usages.reduce(
    (acc, u) => ({
      prompt_tokens: acc.prompt_tokens + u.prompt_tokens,
      completion_tokens: acc.completion_tokens + u.completion_tokens,
      total_tokens: acc.total_tokens + u.total_tokens,
      steps: { ...(acc.steps ?? {}), ...(u.steps ?? {}) },
    }),
    { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, steps: {} }
  );
}

module.exports = { extractActions };
