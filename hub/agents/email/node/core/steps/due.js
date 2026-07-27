/**
 * Due date extraction step.
 *
 * Only runs for URGENT / NEEDS_RESPONSE emails.
 */

const {
  EXTRACT_DUE_DATE_SYSTEM_PROMPT,
  wrapUntrusted,
} = require("../../utils/prompts.js");
const { EXTRACT_DUE_DATE_TOOL } = require("../../utils/tools.js");

/**
 * @param {import('../llm.js').LlmClient} client
 * @param {import('../../utils/types.js').EmailMessage} message
 * @param {string|undefined} messageDate
 * @param {string} triageDate
 * @returns {Promise<{ due: string|undefined, usage: import('../../utils/types.js').TriageUsage }>}
 */
async function extractDueDate(client, message, messageDate, triageDate) {
  const anchor = messageDate ?? triageDate;

  const userMessage = [
    `Message sent: ${anchor}`,
    ...(anchor !== triageDate ? [`Triage date (today): ${triageDate}`] : []),
    `Subject: ${message.subject}`,
    "",
    wrapUntrusted(message.body),
  ].join("\n");

  const { data, usage } = await client.forcedToolCall(
    EXTRACT_DUE_DATE_SYSTEM_PROMPT,
    userMessage,
    EXTRACT_DUE_DATE_TOOL,
    "due-date"
  );

  const rawDue = data.has_deadline && data.due ? data.due : undefined;

  let due;
  if (rawDue) {
    const parsed = Date.parse(rawDue);
    const triageParsed = Date.parse(triageDate);
    if (!isNaN(parsed) && parsed > triageParsed) {
      due = rawDue;
    }
  }

  return { due, usage };
}

module.exports = { extractDueDate };
