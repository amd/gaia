/**
 * Public API surface for @amd-gaia/agent-email.
 *
 * Re-exports all public classes, functions, constants, and JSDoc types.
 */

const { EmailTriageAgent } = require("./core/agent.js");
const { LlmClient, mergeUsage, runWithEmailTag } = require("./core/llm.js");
const { rankActions } = require("./utils/rank.js");
const {
  CLASSIFY_TOOL,
  SUMMARIZE_TOOL,
  EXTRACT_ACTIONS_TOOL,
  EXTRACT_DUE_DATE_TOOL,
  SELECT_ACTIONS_TOOL,
} = require("./utils/tools.js");
const { SCHEMA_VERSION, MAX_BATCH_SIZE } = require("./utils/types.js");
const {
  classifyHeuristic,
  extractSigningUrl,
  extractUnsubscribeUrl,
} = require("./utils/heuristics.js");

module.exports = {
  EmailTriageAgent,
  LlmClient,
  mergeUsage,
  runWithEmailTag,
  rankActions,
  CLASSIFY_TOOL,
  SUMMARIZE_TOOL,
  EXTRACT_ACTIONS_TOOL,
  EXTRACT_DUE_DATE_TOOL,
  SELECT_ACTIONS_TOOL,
  SCHEMA_VERSION,
  MAX_BATCH_SIZE,
  classifyHeuristic,
  extractSigningUrl,
  extractUnsubscribeUrl,
};
