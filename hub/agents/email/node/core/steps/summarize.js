/**
 * Step 2 — Summariser with context-window-aware thread handling.
 */

const {
  SUMMARIZE_SYSTEM_PROMPT,
  SUMMARIZE_THREAD_SYSTEM_PROMPT,
  wrapUntrusted,
} = require("../../utils/prompts.js");

const MAX_SUMMARY_CHARS = 300;
const CHARS_PER_TOKEN = 3;

/**
 * Summarise a single email message.
 *
 * @param {import('../llm.js').LlmClient} client
 * @param {import('../../utils/types.js').EmailMessage} message
 * @param {import('../../utils/types.js').EmailAddress} principal
 * @param {import('../../utils/types.js').RecipientRole} recipientRole
 * @param {number} contentBudgetChars
 * @returns {Promise<{ summary: string, usage: import('../../utils/types.js').TriageUsage }>}
 */
async function summariseEmail(
  client,
  message,
  principal,
  recipientRole,
  contentBudgetChars
) {
  const roleNote = buildRoleNote(principal, recipientRole);

  const userMessage = [
    `From: ${formatAddr(message.from)}`,
    `To: ${message.to.map(formatAddr).join(", ")}`,
    `Subject: ${message.subject}`,
    ...(message.date ? [`Date: ${message.date}`] : []),
    roleNote ? `Note: ${roleNote}` : "",
    "",
    wrapUntrusted(truncate(message.body, contentBudgetChars)),
  ]
    .filter((l) => l !== "")
    .join("\n");

  const { data: text, usage } = await client.textChat(
    SUMMARIZE_SYSTEM_PROMPT,
    userMessage,
    "summarize"
  );

  return { summary: capSummary(text), usage };
}

/**
 * Summarise an email thread, fitting the content into the budget.
 *
 * @param {import('../llm.js').LlmClient} client
 * @param {import('../../utils/types.js').EmailMessage[]} messages
 * @param {import('../../utils/types.js').EmailAddress} principal
 * @param {import('../../utils/types.js').RecipientRole} recipientRole
 * @param {number} contentBudgetChars
 * @returns {Promise<{ summary: string, usage: import('../../utils/types.js').TriageUsage }>}
 */
async function summariseThread(
  client,
  messages,
  principal,
  recipientRole,
  contentBudgetChars
) {
  const roleNote = buildRoleNote(principal, recipientRole);
  const threadContent = buildThreadContent(messages, contentBudgetChars);

  const userMessage = [
    ...(roleNote ? [`Note: ${roleNote}`, ""] : []),
    wrapUntrusted(threadContent),
  ].join("\n");

  const { data: text, usage } = await client.textChat(
    SUMMARIZE_THREAD_SYSTEM_PROMPT,
    userMessage,
    "summarize-thread"
  );

  return { summary: capSummary(text), usage };
}

/**
 * @param {import('../../utils/types.js').EmailMessage[]} messages
 * @param {number} budgetChars
 * @returns {string}
 */
function buildThreadContent(messages, budgetChars) {
  const newest = messages[messages.length - 1];
  const prior = messages.slice(0, -1);

  const newestBlock = formatMessageBlock(newest, true);

  const totalChars = messages.reduce((s, m) => s + m.body.length, 0);
  if (totalChars <= budgetChars) {
    const priorBlocks = prior
      .map((m) => formatMessageBlock(m, false))
      .join("\n\n");
    return priorBlocks ? `${priorBlocks}\n\n${newestBlock}` : newestBlock;
  }

  const newestBudget = budgetChars * 0.6;
  const priorBudget = budgetChars - Math.min(newestBlock.length, newestBudget);

  const includedPrior = [];
  let remaining = priorBudget;

  for (const msg of [...prior].reverse()) {
    const block = formatMessageBlock(msg, false);
    if (block.length <= remaining) {
      includedPrior.unshift(block);
      remaining -= block.length;
    } else if (remaining > 300) {
      const partial = block.slice(0, remaining - 60);
      includedPrior.unshift(`${partial}\n[... earlier message truncated ...]`);
      break;
    } else {
      break;
    }
  }

  const priorSection = includedPrior.join("\n\n");
  return priorSection ? `${priorSection}\n\n${newestBlock}` : newestBlock;
}

/**
 * @param {import('../../utils/types.js').EmailMessage} message
 * @param {boolean} isNewest
 * @returns {string}
 */
function formatMessageBlock(message, isNewest) {
  const header = isNewest
    ? "--- LATEST MESSAGE ---"
    : `--- ${message.date ?? "earlier"} ---`;
  return [
    header,
    `From: ${formatAddr(message.from)}`,
    `Subject: ${message.subject}`,
    "",
    message.body,
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
 * @param {import('../../utils/types.js').EmailAddress} principal
 * @param {import('../../utils/types.js').RecipientRole} role
 * @returns {string}
 */
function buildRoleNote(principal, role) {
  if (role === "primary")
    return `${principal.email} is the primary recipient (TO).`;
  if (role === "cc")
    return `${principal.email} is CC'd — being kept in the loop, not the primary actor.`;
  if (role === "bcc") return `${principal.email} is BCC'd.`;
  return "";
}

/**
 * @param {string} text
 * @param {number} budgetChars
 * @returns {string}
 */
function truncate(text, budgetChars) {
  if (text.length <= budgetChars) return text;
  const slice = text.slice(0, budgetChars);
  const lastSpace = slice.lastIndexOf(" ");
  return lastSpace > 0 ? slice.slice(0, lastSpace) : slice;
}

/**
 * @param {string} text
 * @returns {string}
 */
function capSummary(text) {
  if (text.length <= MAX_SUMMARY_CHARS) return text;
  const truncated = text.slice(0, MAX_SUMMARY_CHARS);
  const lastSpace = truncated.lastIndexOf(" ");
  return lastSpace > 0 ? truncated.slice(0, lastSpace) : truncated;
}

/**
 * @param {number} chars
 * @returns {number}
 */
function estimateTokens(chars) {
  return Math.ceil(chars / CHARS_PER_TOKEN);
}

module.exports = { summariseEmail, summariseThread, estimateTokens };
