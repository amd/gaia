/**
 * Step 1 — LLM email classifier via forced tool call.
 */

const {
  CLASSIFY_SYSTEM_PROMPT,
  withUserContext,
  wrapUntrusted,
} = require("../../utils/prompts.js");
const { CLASSIFY_TOOL } = require("../../utils/tools.js");

/**
 * @param {import('../llm.js').LlmClient} client
 * @param {string} subject
 * @param {import('../../utils/types.js').EmailAddress} from
 * @param {string} body
 * @param {import('../../utils/types.js').TriageContext} [context]
 * @param {string} [userContext]
 * @returns {Promise<{ category: import('../../utils/types.js').EmailCategory, category_reason: string, is_spam: boolean, is_phishing: boolean, usage: import('../../utils/types.js').TriageUsage }>}
 */
async function classifyEmail(
  client,
  subject,
  from,
  body,
  context,
  userContext
) {
  const userMessage = buildUserMessage(subject, from, body, context);

  const { data, usage } = await client.forcedToolCall(
    withUserContext(CLASSIFY_SYSTEM_PROMPT, userContext),
    userMessage,
    CLASSIFY_TOOL,
    "classify"
  );

  return {
    category: data.category,
    category_reason: data.reason,
    is_spam: Boolean(data.is_spam),
    is_phishing: Boolean(data.is_phishing),
    usage,
  };
}

/**
 * @param {string} subject
 * @param {import('../../utils/types.js').EmailAddress} from
 * @param {string} body
 * @param {import('../../utils/types.js').TriageContext} [context]
 * @returns {string}
 */
function buildUserMessage(subject, from, body, context) {
  const lines = [
    `From: ${from.name ? `${from.name} <${from.email}>` : from.email}`,
    `Subject: ${subject}`,
    "",
    wrapUntrusted(body),
  ];

  if (context) {
    lines.push("", "--- Context ---");
    if (context.people?.length)
      lines.push(`Known people: ${context.people.join(", ")}`);
    if (context.projects?.length)
      lines.push(`Projects: ${context.projects.join(", ")}`);
    if (context.self_email) lines.push(`Recipient: ${context.self_email}`);
  }

  return lines.join("\n");
}

module.exports = { classifyEmail };
