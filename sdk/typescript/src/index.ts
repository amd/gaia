// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// GAIA SDK: the GAIA agent loop for your own program.
//
// The loop runs in the native `gaiacpp` engine, started as a child process.
// Your tools, system prompt and conversation stay in your process; this
// package is the thin layer between the two. The wire is pinned by
// tests/fixtures/stdio/gaia_host_wire.json.

import { type ChildProcessWithoutNullStreams, spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

/** The wire version this SDK speaks. */
export const PROTOCOL = 1;

const ENGINE_ENV = "GAIA_ENGINE";
const BINARY = process.platform === "win32" ? "gaiacpp.exe" : "gaiacpp";

/**
 * The engine refused a message, stopped, or broke the protocol. `code` is the
 * engine's own error code when it sent one (`bad_init`, `bad_turn`,
 * `bad_message`, `engine_error`).
 */
export class EngineError extends Error {
  readonly code?: string;
  constructor(message: string, code?: string) {
    super(message);
    this.name = "EngineError";
    this.code = code;
  }
}

/** No `gaiacpp` engine binary could be found to start. */
export class EngineNotFound extends EngineError {
  constructor(message: string) {
    super(message);
    this.name = "EngineNotFound";
  }
}

/**
 * Throw from a tool that ran and said no. The model gets
 * `{status: "refused", message}` as an ordinary result to explain. Any other
 * error is reported to it as a failed call.
 */
export class ToolRefusal extends Error {
  readonly fields: Record<string, unknown>;
  constructor(message: string, fields: Record<string, unknown> = {}) {
    if (typeof message !== "string" || message.trim() === "") {
      throw new TypeError("ToolRefusal needs the reason as a non-empty string.");
    }
    super(message);
    this.name = "ToolRefusal";
    this.fields = fields;
  }
  asResult(): Record<string, unknown> {
    return { ...this.fields, status: "refused", message: this.message };
  }
}

/** A JSON Schema object describing a tool's arguments. */
export interface ParametersSchema {
  type: "object";
  properties: Record<string, Record<string, unknown>>;
  required?: string[];
}

export interface Tool<Args = Record<string, unknown>> {
  name: string;
  /** What the model reads to decide when to call the tool. */
  description: string;
  parameters: ParametersSchema;
  run: (args: Args) => unknown | Promise<unknown>;
  /**
   * How many times the tool may run in one turn, whatever its arguments. A
   * call past the limit is not run: the model is told so and asked to answer
   * from the result it has.
   */
  maxCallsPerTurn?: number;
  /** After a successful call, no other tool runs this turn. For a tool like a handoff. */
  terminal?: boolean;
}

/** Declare a tool. Checks what the model must be told is there. */
export function tool<Args = Record<string, unknown>>(spec: Tool<Args>): Tool<Args> {
  if (!spec.name) throw new TypeError("A tool needs a name.");
  if (!spec.description || spec.description.trim() === "") {
    throw new TypeError(
      `Tool "${spec.name}" has no description. It is what the model reads to decide when to call it.`,
    );
  }
  if (spec.parameters?.type !== "object" || typeof spec.parameters.properties !== "object") {
    throw new TypeError(
      `Tool "${spec.name}" needs parameters as a JSON Schema object: {type: "object", properties: {...}}.`,
    );
  }
  if (typeof spec.run !== "function") {
    throw new TypeError(`Tool "${spec.name}" needs a run function.`);
  }
  if (
    spec.maxCallsPerTurn !== undefined &&
    (!Number.isInteger(spec.maxCallsPerTurn) || spec.maxCallsPerTurn < 1)
  ) {
    throw new TypeError(
      `Tool "${spec.name}": maxCallsPerTurn must be a positive whole number, got ${String(spec.maxCallsPerTurn)}.`,
    );
  }
  if (spec.terminal !== undefined && typeof spec.terminal !== "boolean") {
    throw new TypeError(`Tool "${spec.name}": terminal must be true or false.`);
  }
  return spec;
}

/**
 * What one turn may spend. Checked when the model asks for a tool: once a
 * limit is reached the tool is not run and nothing more is sent to the model.
 */
export interface Budget {
  modelCalls?: number;
  outputTokens?: number;
  seconds?: number;
  /** Dollars at the agent's `prices`, which it then needs. */
  usd?: number;
}

/**
 * A model's list prices in dollars per million tokens. The engine carries no
 * rate card, so a dollar budget and `Turn.costUsd` need them.
 */
export interface Prices {
  input: number;
  cachedInput: number;
  output: number;
}

/** Returns what is wrong with the answer, or nothing. */
export type AnswerCheck = (answer: string, toolCalls: ToolCall[]) => string | null | undefined;

/** A turn's stop reason when an answer check still objects after the one repair. */
export const UNRESOLVED_CHECKS = "unresolved_checks";

export interface Message {
  role: "user" | "assistant";
  content: string;
}

export interface Usage {
  modelCalls: number;
  inputTokens: number;
  cachedTokens: number;
  outputTokens: number;
}

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
  result: unknown;
  failed: boolean;
}

export interface ModelCall {
  step: number;
  seconds: number;
  usage: Omit<Usage, "modelCalls">;
}

/** How one turn ended. */
export interface Turn {
  /** True when `status` is "success". Show `text` to an end user only then. */
  ok: boolean;
  status: "success" | "incomplete" | "failed" | "cancelled";
  /** Why it ended: "answered", "max_steps", "model_calls", "output_tokens", "deadline", "model_call", ... */
  stopReason: string;
  text: string;
  toolCalls: ToolCall[];
  usage: Usage;
  seconds: number;
  /** For a failed model request. */
  failure: { kind: string; message: string } | null;
  /** One entry per model request. */
  modelCalls: ModelCall[];
  /** Dollars at the agent's `prices`; null when it has none. */
  costUsd: number | null;
  /** What the answer checks still object to after the one repair. */
  unresolvedChecks: string[];
}

export interface AgentOptions {
  /** The model id sent to the provider. */
  model: string;
  /** An OpenAI-compatible endpoint, e.g. https://host/v1. */
  baseUrl: string;
  /** Name of the environment variable holding the provider key. The key itself is never sent to the engine. */
  apiKeyEnv?: string;
  /** Sent to the model as written. */
  systemPrompt?: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tools?: Tool<any>[];
  maxSteps?: number;
  maxOutputTokens?: number;
  contextSize?: number;
  temperature?: number;
  /** Send tools as `tools=` (default) or describe them in the prompt. */
  nativeTools?: boolean;
  /** Retries of a failed model request. Default 0: a failure ends the turn. */
  retries?: number;
  /** Limits applied to every turn; `run(..., {budget})` overrides. */
  budget?: Budget;
  /** The model's list prices, for `Turn.costUsd` and `budget.usd`. */
  prices?: Prices;
  /**
   * Run on each successful answer. The model is sent the problems once to
   * repair; any still found afterwards are in `Turn.unresolvedChecks` and the
   * turn is "incomplete".
   */
  answerChecks?: AnswerCheck[];
  onModelCall?: (call: ModelCall) => void;
  /** The gaiacpp binary, or a command to start it, when it is not found otherwise. */
  engine?: string | string[];
}

function checkBudget(budget: Budget | undefined, priced: boolean): Record<string, number> | undefined {
  if (!budget) return undefined;
  if (budget.usd !== undefined && !priced) {
    throw new RangeError(
      "budget.usd needs the agent's prices: pass prices: {input, cachedInput, output} in dollars per million tokens, or limit the turn with outputTokens or modelCalls instead.",
    );
  }
  const wire: Record<string, number> = {};
  for (const [key, name, whole] of [
    ["modelCalls", "model_calls", true],
    ["outputTokens", "output_tokens", true],
    ["seconds", "seconds", false],
    ["usd", "usd", false],
  ] as const) {
    const value = budget[key];
    if (value === undefined) continue;
    if (typeof value !== "number" || !(value > 0) || (whole && !Number.isInteger(value))) {
      throw new RangeError(
        `budget.${key} must be a positive ${whole ? "whole number" : "number"}, got ${String(value)}.`,
      );
    }
    wire[name] = value;
  }
  return Object.keys(wire).length > 0 ? wire : undefined;
}

function onPath(): string | undefined {
  const finder = process.platform === "win32" ? "where" : "which";
  const found = spawnSync(finder, ["gaiacpp"], { encoding: "utf8" });
  const first = found.status === 0 ? found.stdout.split(/\r?\n/)[0]?.trim() : "";
  return first || undefined;
}

/**
 * The command that starts the engine. Looked for in this order: `explicit`,
 * the GAIA_ENGINE environment variable, the binary packaged beside this
 * module, then `gaiacpp` on PATH.
 */
export function findEngine(explicit?: string | string[]): string[] {
  if (Array.isArray(explicit)) {
    if (explicit.length === 0) throw new EngineNotFound("engine was given as an empty command.");
    return explicit;
  }
  const chosen = explicit ?? process.env[ENGINE_ENV]?.trim();
  if (chosen) {
    if (!existsSync(chosen)) {
      throw new EngineNotFound(
        `${explicit ? "engine" : ENGINE_ENV}="${chosen}" is not a file. Point it at the gaiacpp binary.`,
      );
    }
    return [chosen];
  }
  const packaged = join(dirname(fileURLToPath(import.meta.url)), "..", "bin", BINARY);
  if (existsSync(packaged)) return [packaged];
  const found = onPath();
  if (found) return [found];
  throw new EngineNotFound(
    `The gaiacpp engine was not found. Install the platform package for this machine, put gaiacpp on PATH, or set ${ENGINE_ENV} to its path. Builds are attached to each GAIA release: https://github.com/amd/gaia/releases`,
  );
}

type WireEvent = Record<string, unknown> & { type: string };

/** One running engine and the JSON Lines wire to it. */
class Engine {
  private readonly child: ChildProcessWithoutNullStreams;
  private readonly lines: string[] = [];
  private waiting: ((line: string | null) => void) | null = null;
  private ended = false;
  private exitCode: number | null = null;
  private readonly exited: Promise<void>;

  constructor(command: string[]) {
    // stderr is inherited: the engine's diagnostics belong in the host's log.
    this.child = spawn(command[0], [...command.slice(1), "--engine"], {
      stdio: ["pipe", "pipe", "inherit"],
    }) as unknown as ChildProcessWithoutNullStreams;
    this.exited = new Promise((resolve) => {
      this.child.on("error", () => {
        this.finish();
        resolve();
      });
      this.child.on("exit", (code) => {
        this.exitCode = code;
        resolve();
      });
    });
    // A write to an engine that has gone is reported by the next receive().
    this.child.stdin.on("error", () => undefined);
    const reader = createInterface({ input: this.child.stdout });
    reader.on("line", (line) => {
      if (line.trim() === "") return;
      if (this.waiting) {
        const resolve = this.waiting;
        this.waiting = null;
        resolve(line);
      } else {
        this.lines.push(line);
      }
    });
    reader.on("close", () => this.finish());
  }

  private finish(): void {
    this.ended = true;
    if (this.waiting) {
      const resolve = this.waiting;
      this.waiting = null;
      resolve(null);
    }
  }

  send(message: Record<string, unknown>): void {
    if (this.ended) {
      throw new EngineError(`The engine has stopped (exit code ${this.exitCode}).`);
    }
    this.child.stdin.write(`${JSON.stringify(message)}\n`);
  }

  private nextLine(): Promise<string | null> {
    const queued = this.lines.shift();
    if (queued !== undefined) return Promise.resolve(queued);
    if (this.ended) return Promise.resolve(null);
    return new Promise((resolve) => {
      this.waiting = resolve;
    });
  }

  /** The engine's next event. Rejects when it sent an `error` or stopped. */
  async receive(): Promise<WireEvent> {
    const line = await this.nextLine();
    if (line === null) {
      // stdout can close a moment before the exit code is known.
      await this.exited;
      throw new EngineError(
        `The engine closed the connection (exit code ${this.exitCode}) before finishing the exchange.`,
      );
    }
    let event: unknown;
    try {
      event = JSON.parse(line);
    } catch {
      throw new EngineError(`The engine wrote a line that is not JSON: ${line.slice(0, 200)}`);
    }
    if (typeof event !== "object" || event === null || typeof (event as WireEvent).type !== "string") {
      throw new EngineError(`The engine wrote an event with no type: ${line.slice(0, 200)}`);
    }
    const typed = event as WireEvent;
    if (typed.type === "error") {
      throw new EngineError(
        String(typed.message ?? "The engine reported an error."),
        typeof typed.code === "string" ? typed.code : undefined,
      );
    }
    return typed;
  }

  async close(): Promise<void> {
    if (this.child.exitCode !== null || this.ended) return;
    try {
      this.send({ type: "shutdown" });
      this.child.stdin.end();
    } catch {
      this.child.kill();
    }
    const timer = setTimeout(() => this.child.kill(), 5000);
    await this.exited;
    clearTimeout(timer);
  }
}

/**
 * The GAIA agent loop, with your prompt and your tools. One agent runs one
 * turn at a time. Close it to stop the engine process.
 */
export class Agent {
  /** The version the engine reported. */
  readonly engineVersion: string;
  private readonly engine: Engine;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private readonly tools: Map<string, Tool<any>>;
  private readonly options: AgentOptions;
  private turns = 0;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private constructor(engine: Engine, tools: Map<string, Tool<any>>, options: AgentOptions, version: string) {
    this.engine = engine;
    this.tools = tools;
    this.options = options;
    this.engineVersion = version;
  }

  /** Start the engine and hand it the prompt and tools. */
  static async start(options: AgentOptions): Promise<Agent> {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const tools = new Map<string, Tool<any>>();
    for (const item of options.tools ?? []) {
      const checked = tool(item);
      if (tools.has(checked.name)) throw new TypeError(`Two tools are named "${checked.name}".`);
      tools.set(checked.name, checked);
    }
    const prices = options.prices;
    if (prices !== undefined) {
      for (const key of ["input", "cachedInput", "output"] as const) {
        if (typeof prices[key] !== "number" || !(prices[key] >= 0)) {
          throw new RangeError(`prices.${key} must be a number, 0 or more, got ${String(prices[key])}.`);
        }
      }
    }
    checkBudget(options.budget, prices !== undefined);
    for (const check of options.answerChecks ?? []) {
      if (typeof check !== "function") throw new TypeError("answerChecks must be functions.");
    }

    const init: Record<string, unknown> = {
      type: "init",
      protocol: PROTOCOL,
      model: options.model,
      base_url: options.baseUrl,
      system_prompt: options.systemPrompt ?? "",
      tools: [...tools.values()].map((t) => ({
        name: t.name,
        description: t.description,
        parameters: t.parameters,
        ...(t.maxCallsPerTurn !== undefined ? { max_calls_per_turn: t.maxCallsPerTurn } : {}),
        ...(t.terminal ? { terminal: true } : {}),
      })),
      native_tools: options.nativeTools ?? true,
      retries: options.retries ?? 0,
    };
    for (const [key, value] of [
      ["api_key_env", options.apiKeyEnv],
      ["max_steps", options.maxSteps],
      ["max_output_tokens", options.maxOutputTokens],
      ["context_size", options.contextSize],
      ["temperature", options.temperature],
    ] as const) {
      if (value !== undefined) init[key] = value;
    }
    if (prices !== undefined) {
      init.prices = { input: prices.input, cached_input: prices.cachedInput, output: prices.output };
    }

    const engine = new Engine(findEngine(options.engine));
    try {
      engine.send(init);
      const ready = await engine.receive();
      if (ready.type !== "ready" || ready.protocol !== PROTOCOL) {
        throw new EngineError(
          `The engine answered init with ${JSON.stringify(ready)}; this SDK speaks protocol ${PROTOCOL}. Use a gaiacpp build that matches it.`,
        );
      }
      return new Agent(engine, tools, options, String(ready.version ?? ""));
    } catch (error) {
      await engine.close();
      throw error;
    }
  }

  /**
   * Run one turn and return how it ended.
   *
   * `messages` is the user's message, or the conversation so far ending with
   * the user's new message. The engine keeps no history, so send what the
   * model should see.
   *
   * A failed model request, a spent budget and a step limit are not errors:
   * they are a Turn whose `ok` is false. EngineError is thrown only when the
   * engine itself cannot run the turn.
   */
  async run(messages: string | Message[], options: { budget?: Budget } = {}): Promise<Turn> {
    const conversation: Message[] =
      typeof messages === "string" ? [{ role: "user", content: messages }] : messages;
    const budget = checkBudget(options.budget ?? this.options.budget, this.options.prices !== undefined);

    const turn = await this.runOnce(conversation, budget);
    const checks = this.options.answerChecks ?? [];
    if (!turn.ok || checks.length === 0) return turn;
    const problems = findProblems(checks, turn.text, turn.toolCalls);
    if (problems.length === 0) return turn;
    // One repair, then the answer is returned with what is still wrong.
    const second = await this.runOnce(
      [
        ...conversation,
        { role: "assistant", content: turn.text },
        {
          role: "user",
          content: `[check] ${problems.join(" ")} Give your complete answer again: it replaces the one above, which the user will not see. Do not mention this check.`,
        },
      ],
      budget,
    );
    const merged = mergeTurns(turn, second);
    if (!second.ok) return { ...merged, unresolvedChecks: problems };
    const left = findProblems(checks, merged.text, merged.toolCalls);
    if (left.length === 0) return merged;
    return { ...merged, ok: false, status: "incomplete", stopReason: UNRESOLVED_CHECKS, unresolvedChecks: left };
  }

  private async runOnce(conversation: Message[], budget: Record<string, number> | undefined): Promise<Turn> {
    const id = `t${++this.turns}`;
    const request: Record<string, unknown> = {
      type: "turn",
      id,
      messages: conversation.map((m) => ({ role: m.role, content: m.content })),
    };
    if (budget) request.budget = budget;
    this.engine.send(request);

    const modelCalls: ModelCall[] = [];
    for (;;) {
      const event = await this.engine.receive();
      if (event.type === "tool_request") {
        this.engine.send(await this.answer(event));
      } else if (event.type === "model_call") {
        const call: ModelCall = {
          step: Number(event.step ?? 0),
          seconds: Number(event.seconds ?? 0),
          usage: toUsage(event.usage),
        };
        modelCalls.push(call);
        this.options.onModelCall?.(call);
      } else if (event.type === "final") {
        const usage = (event.usage ?? {}) as Record<string, number>;
        return {
          ok: event.status === "success",
          status: event.status as Turn["status"],
          stopReason: String(event.stop_reason),
          text: String(event.text ?? ""),
          toolCalls: (event.tool_calls ?? []) as ToolCall[],
          usage: { modelCalls: Number(usage.model_calls ?? 0), ...toUsage(usage) },
          seconds: Number(event.seconds ?? 0),
          failure: (event.failure ?? null) as Turn["failure"],
          modelCalls,
          costUsd: typeof event.cost_usd === "number" ? event.cost_usd : null,
          unresolvedChecks: [],
        };
      } else {
        throw new EngineError(
          `The engine sent "${event.type}" during a turn, which this SDK does not know. The engine may be newer than the SDK.`,
        );
      }
    }
  }

  /** Run the tool the engine asked for and build its `tool_result`. */
  private async answer(request: WireEvent): Promise<Record<string, unknown>> {
    const answer: Record<string, unknown> = { type: "tool_result", id: request.id };
    const item = this.tools.get(String(request.name));
    if (!item) {
      answer.error = `No tool named "${String(request.name)}" is registered.`;
      return answer;
    }
    try {
      const result = await item.run((request.arguments ?? {}) as Record<string, unknown>);
      answer.result = result === undefined ? null : result;
    } catch (error) {
      if (error instanceof ToolRefusal) {
        answer.result = error.asResult();
      } else {
        // The model is told the call failed and why; the turn goes on.
        const e = error as Error;
        answer.error = `${e?.name ?? "Error"}: ${e?.message ?? String(error)}`;
      }
    }
    return answer;
  }

  /** Stop the engine process. */
  async close(): Promise<void> {
    await this.engine.close();
  }
}

function findProblems(checks: AnswerCheck[], text: string, toolCalls: ToolCall[]): string[] {
  const found: string[] = [];
  for (const check of checks) {
    const problem = check(text, [...toolCalls]);
    if (problem) found.push(String(problem));
  }
  return found;
}

/** The repair turn's outcome, with both turns' work and spend. */
function mergeTurns(first: Turn, second: Turn): Turn {
  return {
    ...second,
    toolCalls: [...first.toolCalls, ...second.toolCalls],
    usage: {
      modelCalls: first.usage.modelCalls + second.usage.modelCalls,
      inputTokens: first.usage.inputTokens + second.usage.inputTokens,
      cachedTokens: first.usage.cachedTokens + second.usage.cachedTokens,
      outputTokens: first.usage.outputTokens + second.usage.outputTokens,
    },
    seconds: first.seconds + second.seconds,
    modelCalls: [...first.modelCalls, ...second.modelCalls],
    costUsd: first.costUsd === null || second.costUsd === null ? null : first.costUsd + second.costUsd,
  };
}

function toUsage(raw: unknown): Omit<Usage, "modelCalls"> {
  const usage = (raw ?? {}) as Record<string, number>;
  return {
    inputTokens: Number(usage.input_tokens ?? 0),
    cachedTokens: Number(usage.cached_tokens ?? 0),
    outputTokens: Number(usage.output_tokens ?? 0),
  };
}
