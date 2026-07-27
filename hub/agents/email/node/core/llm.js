/**
 * Thin OpenAI-compatible LLM client.
 *
 * Three call shapes:
 *   textChat        — plain text response; simplest, works on any model
 *   jsonChat        — free-form JSON response; used only as a fallback
 *   forcedToolCall  — the model MUST call a specific tool; schema-validated args
 *                     come back pre-parsed with no extraction gymnastics
 */

const OpenAI = require("openai");
const { AsyncLocalStorage } = require("async_hooks");

const _tagStorage = new AsyncLocalStorage();

/**
 * Run `fn` with a batch-item label that appears in every LLM log line.
 *
 * @template T
 * @param {string} tag
 * @param {() => Promise<T>} fn
 * @returns {Promise<T>}
 */
function runWithEmailTag(tag, fn) {
  return _tagStorage.run(tag, fn);
}

/**
 * @param {string} step
 * @returns {string}
 */
function logPrefix(step) {
  const tag = _tagStorage.getStore();
  return tag ? `[email ${tag}:${step}]` : `[agent-email:${step}]`;
}

/**
 * @typedef {Object} LlmClientConfig
 * @property {string}   baseUrl     - Base URL of an OpenAI-compatible endpoint, e.g. "http://127.0.0.1:1234/v1"
 * @property {string}   [apiKey]
 * @property {string}   model
 * @property {number}   [timeoutMs]   - Hard per-request timeout in ms; defaults to 120 000
 * @property {number}   [temperature] - Temperature; defaults to 0 for deterministic outputs
 * @property {number}   [maxTokens]   - Optional cap on completion tokens per request.
 * @property {string[]} [stop]        - Optional stop sequences. Applied only when present.
 * @property {boolean}  [debug]       - When true, logs each call's step label, token counts,
 *   and message sizes to stderr.
 */

/**
 * @template T
 * @typedef {Object} LlmResult
 * @property {T}          data
 * @property {import('../utils/types.js').TriageUsage} usage
 * @property {string}     step - The step label this result came from
 */

class LlmClient {
  /**
   * @param {LlmClientConfig} config
   */
  constructor(config) {
    /** @type {OpenAI} */
    this._oai = new OpenAI({
      baseURL: config.baseUrl,
      apiKey: config.apiKey ?? "not-required",
      timeout: config.timeoutMs ?? 120_000,
      maxRetries: 0,
    });
    /** @type {string} */
    this._model = config.model;
    /** @type {number} */
    this._temperature = config.temperature ?? 0;
    /** @type {number|undefined} */
    this._maxTokens = config.maxTokens;
    /** @type {string[]|undefined} */
    this._stop = config.stop?.length ? config.stop : undefined;
    /** @type {boolean} */
    this._debug = config.debug ?? false;
  }

  /**
   * Force the model to call `tool` and return its parsed arguments as `T`.
   *
   * @template T
   * @param {string} systemPrompt
   * @param {string} userMessage
   * @param {object} tool           - An OpenAI ChatCompletionTool schema object
   * @param {string} [step]         - Short label shown in debug output
   * @returns {Promise<LlmResult<T>>}
   */
  async forcedToolCall(systemPrompt, userMessage, tool, step = "tool") {
    const toolName = tool.function.name;

    if (this._debug) {
      process.stderr.write(
        `${logPrefix(step)} → tool=${toolName} sys=${systemPrompt.length}c usr=${userMessage.length}c\n`
      );
    }

    const response = await this._oai.chat.completions.create({
      model: this._model,
      temperature: this._temperature,
      ...(this._maxTokens !== undefined ? { max_tokens: this._maxTokens } : {}),
      ...(this._stop !== undefined ? { stop: this._stop } : {}),
      messages: [
        { role: "system", content: systemPrompt },
        { role: "user", content: userMessage },
      ],
      tools: [tool],
      tool_choice: "required",
    });

    const toolCall = response.choices[0]?.message?.tool_calls?.[0];
    if (!toolCall) {
      throw new Error(
        `Model did not call tool "${toolName}". ` +
          `Choice finish_reason: ${response.choices[0]?.finish_reason}`
      );
    }

    /** @type {T} */
    const data = JSON.parse(toolCall.function.arguments);
    const usage = extractUsage(response.usage);

    if (this._debug) {
      process.stderr.write(
        `${logPrefix(step)} ← ` +
          `tool=${toolName} ` +
          `sys=${systemPrompt.length}c usr=${userMessage.length}c ` +
          `prompt=${usage.prompt_tokens}tok compl=${usage.completion_tokens}tok total=${usage.total_tokens}tok\n`
      );
    }

    return {
      data,
      usage: {
        ...usage,
        steps: {
          [step]: {
            prompt_tokens: usage.prompt_tokens,
            completion_tokens: usage.completion_tokens,
            total_tokens: usage.total_tokens,
          },
        },
      },
      step,
    };
  }

  /**
   * Plain text completion.
   *
   * @param {string} systemPrompt
   * @param {string} userMessage
   * @param {string} [step]
   * @returns {Promise<LlmResult<string>>}
   */
  async textChat(systemPrompt, userMessage, step = "llm") {
    if (this._debug) {
      process.stderr.write(
        `${logPrefix(step)} → text sys=${systemPrompt.length}c usr=${userMessage.length}c\n`
      );
    }

    const response = await this._oai.chat.completions.create({
      model: this._model,
      temperature: this._temperature,
      ...(this._maxTokens !== undefined ? { max_tokens: this._maxTokens } : {}),
      ...(this._stop !== undefined ? { stop: this._stop } : {}),
      messages: [
        { role: "system", content: systemPrompt },
        { role: "user", content: userMessage },
      ],
    });

    const text = (response.choices[0]?.message?.content ?? "").trim();
    const usage = extractUsage(response.usage);

    if (this._debug) {
      process.stderr.write(
        `${logPrefix(step)} ← ` +
          `text sys=${systemPrompt.length}c usr=${userMessage.length}c ` +
          `prompt=${usage.prompt_tokens}tok compl=${usage.completion_tokens}tok total=${usage.total_tokens}tok\n`
      );
    }

    return {
      data: text,
      usage: {
        ...usage,
        steps: {
          [step]: {
            prompt_tokens: usage.prompt_tokens,
            completion_tokens: usage.completion_tokens,
            total_tokens: usage.total_tokens,
          },
        },
      },
      step,
    };
  }

  /**
   * Free-form JSON chat — fallback when tool calling isn't supported.
   *
   * @template T
   * @param {string} systemPrompt
   * @param {string} userMessage
   * @param {string} [step]
   * @returns {Promise<LlmResult<T>>}
   */
  async jsonChat(systemPrompt, userMessage, step = "llm") {
    if (this._debug) {
      process.stderr.write(
        `${logPrefix(step)} → json sys=${systemPrompt.length}c usr=${userMessage.length}c\n`
      );
    }

    const response = await this._oai.chat.completions.create({
      model: this._model,
      temperature: this._temperature,
      ...(this._maxTokens !== undefined ? { max_tokens: this._maxTokens } : {}),
      ...(this._stop !== undefined ? { stop: this._stop } : {}),
      messages: [
        { role: "system", content: systemPrompt },
        { role: "user", content: userMessage },
      ],
    });

    const raw = response.choices[0]?.message?.content ?? "";
    /** @type {T} */
    const data = parseJsonContent(raw);
    const usage = extractUsage(response.usage);

    if (this._debug) {
      process.stderr.write(
        `${logPrefix(step)} ← ` +
          `tools=0 ` +
          `sys=${systemPrompt.length}c usr=${userMessage.length}c ` +
          `prompt=${usage.prompt_tokens}tok compl=${usage.completion_tokens}tok total=${usage.total_tokens}tok\n`
      );
    }

    return {
      data,
      usage: {
        ...usage,
        steps: {
          [step]: {
            prompt_tokens: usage.prompt_tokens,
            completion_tokens: usage.completion_tokens,
            total_tokens: usage.total_tokens,
          },
        },
      },
      step,
    };
  }
}

/**
 * @param {import('openai').OpenAI.CompletionUsage | undefined} raw
 * @returns {import('../utils/types.js').TriageUsage}
 */
function extractUsage(raw) {
  return {
    prompt_tokens: raw?.prompt_tokens ?? 0,
    completion_tokens: raw?.completion_tokens ?? 0,
    total_tokens: raw?.total_tokens ?? 0,
  };
}

/**
 * @template T
 * @param {string} raw
 * @returns {T}
 */
function parseJsonContent(raw) {
  const stripped = raw.trim();
  if (stripped.startsWith("{")) return JSON.parse(stripped);

  const fenced = stripped.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fenced?.[1]) return JSON.parse(fenced[1].trim());

  const start = stripped.indexOf("{");
  const end = stripped.lastIndexOf("}");
  if (start !== -1 && end > start)
    return JSON.parse(stripped.slice(start, end + 1));

  throw new Error(`LLM response did not contain valid JSON.\nRaw: ${raw}`);
}

/**
 * Merge multiple TriageUsage objects (token counts summed, steps merged).
 *
 * @param {...(import('../utils/types.js').TriageUsage | undefined)} parts
 * @returns {import('../utils/types.js').TriageUsage}
 */
function mergeUsage(...parts) {
  const defined = parts.filter((u) => u !== undefined);
  const totals = defined.reduce(
    (acc, u) => ({
      prompt_tokens: acc.prompt_tokens + u.prompt_tokens,
      completion_tokens: acc.completion_tokens + u.completion_tokens,
      total_tokens: acc.total_tokens + u.total_tokens,
    }),
    { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }
  );

  const steps = defined.reduce((acc, u) => {
    if (!u.steps) return acc;
    return { ...acc, ...u.steps };
  }, {});

  return { ...totals, ...(Object.keys(steps).length > 0 ? { steps } : {}) };
}

module.exports = { runWithEmailTag, LlmClient, mergeUsage };
