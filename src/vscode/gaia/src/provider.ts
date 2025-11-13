/*
Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
*/

import * as vscode from "vscode";
import {
	CancellationToken,
	LanguageModelChatInformation,
	LanguageModelChatMessage,
	LanguageModelChatProvider,
	LanguageModelResponsePart,
	Progress,
	ProvideLanguageModelChatResponseOptions,
} from "vscode";

import { convertTools, convertMessages, tryParseJSONObject, validateRequest } from "./utils";
import type { GaiaModel, GaiaModelsResponse } from "./types";

const DEFAULT_BASE_URL = "http://localhost:8080";
const DEFAULT_MAX_OUTPUT_TOKENS = 16000;
const DEFAULT_CONTEXT_LENGTH = 128000;
const HARDCODED_API_KEY = "gaia";

/**
 * VS Code Chat provider backed by GAIA local LLM server.
 */
export class GaiaChatModelProvider implements LanguageModelChatProvider {
	private _chatEndpoints: { model: string; modelMaxPromptTokens: number }[] = [];
	/** Buffer for assembling streamed tool calls by index. */
	private _toolCallBuffers: Map<number, { id?: string; name?: string; args: string }> = new Map<
		number,
		{ id?: string; name?: string; args: string }
	>();

	/** Indices for which a tool call has been fully emitted. */
	private _completedToolCallIndices = new Set<number>();

	/** Track if we emitted any assistant text before seeing tool calls (SSE-like begin-tool-calls hint). */
	private _hasEmittedAssistantText = false;

	/** Track if we emitted the begin-tool-calls whitespace flush. */
	private _emittedBeginToolCallsHint = false;

	// Lightweight tokenizer state for tool calls embedded in text
	private _textToolParserBuffer = "";
	private _textToolActive:
		| undefined
		| {
			name?: string;
			index?: number;
			argBuffer: string;
			emitted?: boolean;
		};
	private _emittedTextToolCallKeys = new Set<string>();
	private _emittedTextToolCallIds = new Set<string>();

	/** Buffer for handling control tokens that might be split across streaming chunks. */
	private _controlTokenBuffer = "";

	/**
	 * Create a provider using the given secret storage for the server URL.
	 * @param secrets VS Code secret storage.
	 */
	constructor(private readonly secrets: vscode.SecretStorage, private readonly userAgent: string) {}

	/**
	 * Format GAIA status messages for better display in GitHub Copilot.
	 * Adds emojis, separators, and visual structure to clean status text from API.
	 */
	private formatGAIAStatus(text: string): string {
		// Don't format if text is part of the final answer (contains substantive content)
		if (text.length > 100 && !text.startsWith("Step ") && !text.startsWith("Processing") && !text.startsWith("Using tool")) {
			return text;
		}

		// Processing start
		if (text.startsWith("Processing request")) {
			return "🚀 " + text;
		}

		// Step headers - add separator with just newline
		if (text.match(/^Step \d+\/\d+/)) {
			return "\n📍 " + text + "\n";
		}

		// Tool usage
		if (text.startsWith("Using tool:")) {
			return "🔧 " + text;
		}

		// File preview
		if (text.startsWith("Previewing file:")) {
			return "📄 " + text;
		}
		if (text.startsWith("File preview complete")) {
			return "✓ " + text;
		}

		// Status messages (PLANNING, EXECUTING, etc.)
		if (text.includes("PLANNING") || text.includes("Plan")) {
			return "🔄 " + text;
		}
		if (text.includes("EXECUTING") || text.includes("Executing")) {
			return "⚙️ " + text;
		}

		// Completion
		if (text.startsWith("Completed in")) {
			return "\n✅ " + text + "\n";
		}

		// Hide errors and error recovery unless in debug mode
		// These are internal agent messages not meant for end users
		if (text.startsWith("Error:") || text.includes("ERROR RECOVERY")) {
			return ""; // Don't show to user
		}

		// Warnings - only show if important
		if (text.startsWith("Warning:")) {
			return "⚠️ " + text;
		}

		// COMPLETION messages - clean up the formatting
		if (text.includes("COMPLETION:")) {
			// Skip internal completion markers
			return "";
		}

		// Default: return text as-is
		return text;
	}

	/** Roughly estimate tokens for VS Code chat messages (text only) */
	private estimateMessagesTokens(msgs: readonly vscode.LanguageModelChatMessage[]): number {
		let total = 0;
		for (const m of msgs) {
			for (const part of m.content) {
				if (part instanceof vscode.LanguageModelTextPart) {
					total += Math.ceil(part.value.length / 4);
				}
			}
		}
		return total;
	}

	/** Rough token estimate for tool definitions by JSON size */
	private estimateToolTokens(tools: { type: string; function: { name: string; description?: string; parameters?: object } }[] | undefined): number {
		if (!tools || tools.length === 0) { return 0; }
		try {
			const json = JSON.stringify(tools);
			return Math.ceil(json.length / 4);
		} catch {
			return 0;
		}
	}

	/**
	 * Get the list of available language models contributed by this provider
	 * @param options Options which specify the calling context of this function
	 * @param token A cancellation token which signals if the user cancelled the request or not
	 * @returns A promise that resolves to the list of available language models
	 */
	async prepareLanguageModelChatInformation(
		options: { silent: boolean },
		_token: CancellationToken
	): Promise<LanguageModelChatInformation[]> {
		// Fetch available models from the GAIA server
		const models = await this.fetchModels();

		if (models.length === 0) {
			if (!options.silent) {
				vscode.window.showWarningMessage(
					"No models available from GAIA server. Make sure GAIA server is running with `gaia api start`."
				);
			}
			return [];
		}

		const maxOutput = DEFAULT_MAX_OUTPUT_TOKENS;
		const maxInput = Math.max(1, DEFAULT_CONTEXT_LENGTH - maxOutput);

		const infos: LanguageModelChatInformation[] = models.map(model => ({
			id: model.id,
			name: model.id,
			tooltip: "GAIA Local LLM",
			family: "gaia",
			version: "1.0.0",
			maxInputTokens: maxInput,
			maxOutputTokens: maxOutput,
			capabilities: {
				toolCalling: true,
				imageInput: false,
			},
		} satisfies LanguageModelChatInformation));

		this._chatEndpoints = infos.map((info) => ({
			model: info.id,
			modelMaxPromptTokens: info.maxInputTokens + info.maxOutputTokens,
		}));

		return infos;
	}

	async provideLanguageModelChatInformation(
		options: { silent: boolean },
		_token: CancellationToken
	): Promise<LanguageModelChatInformation[]> {
		return this.prepareLanguageModelChatInformation({ silent: options.silent ?? false }, _token);
	}

	/**
	 * Get the configured server URL or return the default.
	 */
	private async getServerUrl(): Promise<string> {
		const stored = await this.secrets.get("gaia.serverUrl");
		return stored || DEFAULT_BASE_URL;
	}

	/**
	 * Fetch the list of available models from the GAIA server.
	 */
	private async fetchModels(): Promise<GaiaModel[]> {
		const baseUrl = await this.getServerUrl();

		try {
			const response = await fetch(`${baseUrl}/v1/models`, {
				method: "GET",
				headers: {
					Authorization: `Bearer ${HARDCODED_API_KEY}`,
					"User-Agent": this.userAgent,
				},
			});

			if (!response.ok) {
				console.error("[GAIA Model Provider] Failed to fetch models", {
					status: response.status,
					statusText: response.statusText,
				});
				throw new Error(`Failed to fetch models: ${response.status} ${response.statusText}`);
			}

			const data = (await response.json()) as GaiaModelsResponse;
			return data.data || [];
		} catch (error) {
			console.error("[GAIA Model Provider] Error fetching models", error);
			// Return empty array on error - this will result in no models being available
			// which is better than crashing the extension
			return [];
		}
	}

	/**
	 * Returns the response for a chat request, passing the results to the progress callback.
	 * The {@linkcode LanguageModelChatProvider} must emit the response parts to the progress callback as they are received from the language model.
	 * @param model The language model to use
	 * @param messages The messages to include in the request
	 * @param options Options for the request
	 * @param progress The progress to emit the streamed response chunks to
	 * @param token A cancellation token for the request
	 * @returns A promise that resolves when the response is complete. Results are actually passed to the progress callback.
	 */
	async provideLanguageModelChatResponse(
		model: LanguageModelChatInformation,
		messages: readonly LanguageModelChatMessage[],
		options: ProvideLanguageModelChatResponseOptions,
		progress: Progress<LanguageModelResponsePart>,
		token: CancellationToken
	): Promise<void> {

		console.log("[GAIA Model Provider] ===== CHAT REQUEST START =====");
		console.log("[GAIA Model Provider] Model ID:", model.id);
		console.log("[GAIA Model Provider] Message Count:", messages.length);
		console.log("[GAIA Model Provider] Messages:");
		messages.forEach((msg, idx) => {
			console.log(`  [${idx}] Role: ${msg.role}`);
			console.log(`  [${idx}] Content parts: ${msg.content.length}`);
			msg.content.forEach((part, partIdx) => {
				if (part instanceof vscode.LanguageModelTextPart) {
					console.log(`    [${partIdx}] TextPart:`, part.value.substring(0, 200));
				} else {
					console.log(`    [${partIdx}] ${part.constructor.name}`);
				}
			});
		});

		this._toolCallBuffers.clear();
		this._completedToolCallIndices.clear();
		this._hasEmittedAssistantText = false;
		this._emittedBeginToolCallsHint = false;
        this._textToolParserBuffer = "";
        this._textToolActive = undefined;
        this._emittedTextToolCallKeys.clear();
        this._emittedTextToolCallIds.clear();
		this._controlTokenBuffer = "";

		let requestBody: Record<string, unknown> | undefined;
		const trackingProgress: Progress<LanguageModelResponsePart> = {
			report: (part) => {
				try {
					progress.report(part);
				} catch (e) {
					console.error("[GAIA Model Provider] Progress.report failed", {
						modelId: model.id,
						error: e instanceof Error ? { name: e.name, message: e.message } : String(e),
					});
				}
			},
		};
		try {
			const baseUrl = await this.getServerUrl();

            const openaiMessages = convertMessages(messages);

			// Inject workspace information if available and not already present
			const workspaceFolders = vscode.workspace.workspaceFolders;
			if (workspaceFolders && workspaceFolders.length > 0 && openaiMessages.length > 0) {
				// Check if workspace_info is already present (e.g., from GitHub Copilot)
				const hasWorkspaceInfo = openaiMessages.some(msg =>
					msg.content && msg.content.includes("<workspace_info>")
				);

				if (!hasWorkspaceInfo) {
					// Find the last user message and append workspace info
					for (let i = openaiMessages.length - 1; i >= 0; i--) {
						if (openaiMessages[i].role === "user" && openaiMessages[i].content) {
							const workspacePaths = workspaceFolders.map(f => f.uri.fsPath).join("\n- ");
							const workspaceInfo = `\n<workspace_info>\nI am working in a workspace with the following folders:\n- ${workspacePaths}\n</workspace_info>`;
							openaiMessages[i].content += workspaceInfo;
							break;
						}
					}
				}
			}

			console.log("[GAIA Model Provider] Converted to OpenAI messages:", JSON.stringify(openaiMessages, null, 2));

			validateRequest(messages);

            const toolConfig = convertTools(options);

        if (options.tools && options.tools.length > 128) {
            throw new Error("Cannot have more than 128 tools per request.");
        }

            const inputTokenCount = this.estimateMessagesTokens(messages);
            const toolTokenCount = this.estimateToolTokens(toolConfig.tools);
            const tokenLimit = Math.max(1, model.maxInputTokens);
            if (inputTokenCount + toolTokenCount > tokenLimit) {
                console.error("[GAIA Model Provider] Message exceeds token limit", { total: inputTokenCount + toolTokenCount, tokenLimit });
                throw new Error("Message exceeds token limit.");
            }

            requestBody = {
                model: model.id,
                messages: openaiMessages,
                stream: true,
                max_tokens: Math.min(options.modelOptions?.max_tokens || 4096, model.maxOutputTokens),
                temperature: options.modelOptions?.temperature ?? 0.7,
            };

			// Allow-list model options
			if (options.modelOptions) {
				const mo = options.modelOptions as Record<string, unknown>;
				if (typeof mo.stop === "string" || Array.isArray(mo.stop)) {
					(requestBody as Record<string, unknown>).stop = mo.stop;
				}
				if (typeof mo.frequency_penalty === "number") {
					(requestBody as Record<string, unknown>).frequency_penalty = mo.frequency_penalty;
				}
				if (typeof mo.presence_penalty === "number") {
					(requestBody as Record<string, unknown>).presence_penalty = mo.presence_penalty;
				}
			}

			if (toolConfig.tools) {
				(requestBody as Record<string, unknown>).tools = toolConfig.tools;
			}
			if (toolConfig.tool_choice) {
				(requestBody as Record<string, unknown>).tool_choice = toolConfig.tool_choice;
			}

			console.log("[GAIA Model Provider] Request body:", JSON.stringify(requestBody, null, 2));

			const response = await fetch(`${baseUrl}/v1/chat/completions`, {
                method: "POST",
                headers: {
                    Authorization: `Bearer ${HARDCODED_API_KEY}`,
                    "Content-Type": "application/json",
					"User-Agent": this.userAgent,
                },
                body: JSON.stringify(requestBody),
            });

			console.log("[GAIA Model Provider] Response status:", response.status, response.statusText);

			if (!response.ok) {
				const errorText = await response.text();
				console.error("[GAIA Model Provider] API error response", errorText);
				throw new Error(
					`GAIA API error: ${response.status} ${response.statusText}${errorText ? `\n${errorText}` : ""}`
				);
			}

			if (!response.body) {
				throw new Error("No response body from GAIA API");
			}
			await this.processStreamingResponse(response.body, trackingProgress, token);
			console.log("[GAIA Model Provider] ===== CHAT REQUEST END =====");
		} catch (err) {
			console.error("[GAIA Model Provider] Chat request failed", {
				modelId: model.id,
				messageCount: messages.length,
				error: err instanceof Error ? { name: err.name, message: err.message } : String(err),
			});
			throw err;
		}
	}

	/**
	 * Returns the number of tokens for a given text using the model specific tokenizer logic
	 * @param model The language model to use
	 * @param text The text to count tokens for
	 * @param token A cancellation token for the request
	 * @returns A promise that resolves to the number of tokens
	 */
	async provideTokenCount(
		model: LanguageModelChatInformation,
		text: string | LanguageModelChatMessage,
		_token: CancellationToken
	): Promise<number> {
		if (typeof text === "string") {
			return Math.ceil(text.length / 4);
		} else {
			let totalTokens = 0;
			for (const part of text.content) {
				if (part instanceof vscode.LanguageModelTextPart) {
					totalTokens += Math.ceil(part.value.length / 4);
				}
			}
			return totalTokens;
		}
	}

	/**
	 * Read and parse the GAIA server streaming (SSE-like) response and report parts.
	 * @param responseBody The readable stream body.
	 * @param progress Progress reporter for streamed parts.
	 * @param token Cancellation token.
	 */
    private async processStreamingResponse(
        responseBody: ReadableStream<Uint8Array>,
        progress: vscode.Progress<vscode.LanguageModelResponsePart>,
        token: vscode.CancellationToken,
    ): Promise<void> {
        const reader = responseBody.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

		try {
			while (!token.isCancellationRequested) {
				const { done, value } = await reader.read();
                if (done) { break; }

				buffer += decoder.decode(value, { stream: true });
				const lines = buffer.split("\n");
				buffer = lines.pop() || "";

				for (const line of lines) {
					if (!line.startsWith("data: ")) {
						continue;
					}
					const data = line.slice(6);
					console.log("[GAIA Model Provider] SSE line:", data);
                    if (data === "[DONE]") {
                        // Do not throw on [DONE]; any incomplete/empty buffers are ignored.
                        await this.flushToolCallBuffers(progress, /*throwOnInvalid*/ false);
                        // Flush any in-progress text-embedded tool call (silent if incomplete)
                        await this.flushActiveTextToolCall(progress);
                        continue;
                    }

					try {
						const parsed = JSON.parse(data);
                        await this.processDelta(parsed, progress);
                    } catch {
                        // Silently ignore malformed SSE lines temporarily
                    }
                }
            }
        } finally {
            reader.releaseLock();
            // Clean up any leftover tool call state
            this._toolCallBuffers.clear();
            this._completedToolCallIndices.clear();
            this._hasEmittedAssistantText = false;
            this._emittedBeginToolCallsHint = false;
            this._textToolParserBuffer = "";
            this._textToolActive = undefined;
            this._emittedTextToolCallKeys.clear();
			this._emittedTextToolCallIds.clear();
			this._controlTokenBuffer = "";
        }
    }

	/**
	 * Handle a single streamed delta chunk, emitting text and tool call parts.
	 * @param delta Parsed SSE chunk from the Router.
	 * @param progress Progress reporter for parts.
	 */
    private async processDelta(
        delta: Record<string, unknown>,
        progress: vscode.Progress<vscode.LanguageModelResponsePart>,
    ): Promise<boolean> {
        let emitted = false;
        const choice = (delta.choices as Record<string, unknown>[] | undefined)?.[0];
        if (!choice) { return false; }

		const deltaObj = choice.delta as Record<string, unknown> | undefined;

		// report thinking progress if backend provides it and host supports it
		try {
			const maybeThinking = (choice as Record<string, unknown> | undefined)?.thinking ?? (deltaObj as Record<string, unknown> | undefined)?.thinking;
			if (maybeThinking !== undefined) {
				const vsAny = (vscode as unknown as Record<string, unknown>);
				const ThinkingCtor = vsAny["LanguageModelThinkingPart"] as
					| (new (text: string, id?: string, metadata?: unknown) => unknown)
					| undefined;
                    if (ThinkingCtor) {
                        let text = "";
                        let id: string | undefined;
                        let metadata: unknown;
                        if (maybeThinking && typeof maybeThinking === "object") {
                            const mt = maybeThinking as Record<string, unknown>;
                            text = typeof mt["text"] === "string" ? (mt["text"] as string) : "";
                            id = typeof mt["id"] === "string" ? (mt["id"] as string) : undefined;
                            metadata = mt["metadata"];
                        } else if (typeof maybeThinking === "string") {
                            text = maybeThinking;
                        }
                        if (text) {
                            progress.report(new (ThinkingCtor as new (text: string, id?: string, metadata?: unknown) => unknown)(text, id, metadata) as unknown as vscode.LanguageModelResponsePart);
                            emitted = true;
                        }
                    }
                }
            } catch {
                // ignore errors here temporarily
            }
            if (deltaObj?.content) {
                const content = String(deltaObj.content);
				console.log("[GAIA Model Provider] Content delta:", content);

                // Apply GAIA-specific formatting before processing
                const formattedContent = this.formatGAIAStatus(content);

                const res = this.processTextContent(formattedContent, progress);
                if (res.emittedText) {
                    this._hasEmittedAssistantText = true;
                }
                if (res.emittedAny) {
                    emitted = true;
                }
            }

			if (deltaObj?.tool_calls) {
                const toolCalls = deltaObj.tool_calls as Array<Record<string, unknown>>;

				// SSEProcessor-like: if first tool call appears after text, emit a whitespace
				// to ensure any UI buffers/linkifiers are flushed without adding visible noise.
				if (!this._emittedBeginToolCallsHint && this._hasEmittedAssistantText && toolCalls.length > 0) {
					progress.report(new vscode.LanguageModelTextPart(" "));
					this._emittedBeginToolCallsHint = true;
				}

				for (const tc of toolCalls) {
					const idx = (tc.index as number) ?? 0;
					// Ignore any further deltas for an index we've already completed
					if (this._completedToolCallIndices.has(idx)) {
						continue;
					}
					const buf = this._toolCallBuffers.get(idx) ?? { args: "" };
					if (tc.id && typeof tc.id === "string") {
						buf.id = tc.id as string;
					}
					const func = tc.function as Record<string, unknown> | undefined;
					if (func?.name && typeof func.name === "string") {
						buf.name = func.name as string;
					}
					if (typeof func?.arguments === "string") {
						buf.args += func.arguments as string;
					}
					this._toolCallBuffers.set(idx, buf);

					// Emit immediately once arguments become valid JSON to avoid perceived hanging
                    await this.tryEmitBufferedToolCall(idx, progress);
                }
            }

        const finish = (choice.finish_reason as string | undefined) ?? undefined;
        if (finish === "tool_calls" || finish === "stop") {
            // On both 'tool_calls' and 'stop', emit any buffered calls and throw on invalid JSON
            await this.flushToolCallBuffers(progress, /*throwOnInvalid*/ true);
        }
        return emitted;
    }

	/**
	 * Strip control tokens with buffering to handle tokens split across chunks.
	 * This handles older-style control tokens like <tool_call> and <function=...
	 */
	private stripControlTokensWithBuffering(text: string): string {
		let data = this._controlTokenBuffer + text;
		let result = "";
		let pos = 0;

		while (pos < data.length) {
			let foundMatch = false;

			// Check for complete control token patterns
			const controlPatterns = [
				/<tool_call>/g,
				/<\/function>/g,
				/<\|tool_calls_section_(?:begin|end)\|>/g,
				/<\|tool_call_(?:argument_)?(?:begin|end)\|>/g,
				/<function=[a-zA-Z0-9_\-.]+>/g  // Match complete function tags
			];

			// Try to match any control pattern at current position
			for (const pattern of controlPatterns) {
				pattern.lastIndex = 0; // Reset regex state
				const remaining = data.slice(pos);
				const match = pattern.exec(remaining);

				if (match && match.index === 0) {
					// Found a complete token at current position, skip it
					pos += match[0].length;
					foundMatch = true;
					break;
				}
			}

			if (!foundMatch) {
				// Check for potential incomplete control tokens near end of chunk
				let isPartialToken = false;
				if (pos >= data.length - 50) { // Check last 50 chars for partial tokens
					const remaining = data.slice(pos);

					// Check for partial patterns that could complete in next chunk
					const partialPatterns = [
						/^<tool_cal?$/,
						/^<tool_call?$/,
						/^<\|tool_calls?$/,
						/^<\|tool_calls_section?$/,
						/^<\|tool_calls_section_(?:begin|end)?$/,
						/^<\|tool_call?$/,
						/^<\|tool_call_(?:argument_)?$/,
						/^<\|tool_call_(?:argument_)?(?:begin|end)?$/,
						/^<function?$/,
						/^<function=?$/,
						/^<function=[a-zA-Z0-9_\-.]*$/,  // Partial function name
						/^<\/function?$/
					];

					for (const pattern of partialPatterns) {
						if (pattern.test(remaining)) {
							isPartialToken = true;
							break;
						}
					}
				}

				if (isPartialToken) {
					// Keep remaining text in buffer for next chunk
					this._controlTokenBuffer = data.slice(pos);
					break;
				} else {
					// Safe to emit this character
					result += data[pos];
					pos++;
				}
			}
		}

		// If we consumed everything, clear the buffer
		if (pos >= data.length) {
			this._controlTokenBuffer = "";
		}

		return result;
	}

    /**
     * Process streamed text content for inline tool-call control tokens and emit text/tool calls.
     * Returns which parts were emitted for logging/flow control.
     */
    private processTextContent(
        input: string,
        progress: vscode.Progress<vscode.LanguageModelResponsePart>,
    ): { emittedText: boolean; emittedAny: boolean } {
        const BEGIN = "<|tool_call_begin|>";
        const ARG_BEGIN = "<|tool_call_argument_begin|>";
        const END = "<|tool_call_end|>";

        let data = this._textToolParserBuffer + input;
        let emittedText = false;
        let emittedAny = false;
        let visibleOut = "";

        while (data.length > 0) {
            if (!this._textToolActive) {
                const b = data.indexOf(BEGIN);
                if (b === -1) {
                    // No tool-call start: emit visible portion, but keep any partial BEGIN prefix as buffer
                    const longestPartialPrefix = ((): number => {
                        for (let k = Math.min(BEGIN.length - 1, data.length - 1); k > 0; k--) {
                            if (data.endsWith(BEGIN.slice(0, k))) { return k; }
                        }
                        return 0;
                    })();
                    if (longestPartialPrefix > 0) {
                        const visible = data.slice(0, data.length - longestPartialPrefix);
                        if (visible) { visibleOut += this.stripControlTokensWithBuffering(visible); }
                        this._textToolParserBuffer = data.slice(data.length - longestPartialPrefix);
                        data = "";
                        break;
                    } else {
                        // All visible, clean other control tokens with buffering
                        visibleOut += this.stripControlTokensWithBuffering(data);
                        data = "";
                        break;
                    }
                }
                // Emit text before the token
                const pre = data.slice(0, b);
                if (pre) {
                    visibleOut += this.stripControlTokensWithBuffering(pre);
                }
                // Advance past BEGIN
                data = data.slice(b + BEGIN.length);

                // Find the delimiter that ends the name/index segment
                const a = data.indexOf(ARG_BEGIN);
                const e = data.indexOf(END);
                let delimIdx = -1;
                let delimKind: "arg" | "end" | undefined = undefined;
                if (a !== -1 && (e === -1 || a < e)) { delimIdx = a; delimKind = "arg"; }
                else if (e !== -1) { delimIdx = e; delimKind = "end"; }
                else {
                    // Incomplete header; keep for next chunk (re-add BEGIN so we don't lose it)
                    this._textToolParserBuffer = BEGIN + data;
                    data = "";
                    break;
                }

                const header = data.slice(0, delimIdx).trim();
                const m = header.match(/^([A-Za-z0-9_\-.]+)(?::(\d+))?/);
                const name = m?.[1] ?? undefined;
                const index = m?.[2] ? Number(m?.[2]) : undefined;
                this._textToolActive = { name, index, argBuffer: "", emitted: false };
                // Advance past delimiter token
                if (delimKind === "arg") {
                    data = data.slice(delimIdx + ARG_BEGIN.length);
                } else /* end */ {
                    // No args, finalize immediately
                    data = data.slice(delimIdx + END.length);
                    const did = this.emitTextToolCallIfValid(progress, this._textToolActive, "{}");
                    if (did) {
                        this._textToolActive.emitted = true;
                        emittedAny = true;
                    }
                    this._textToolActive = undefined;
                }
                continue;
            }

            // We are inside arguments, collect until END and emit as soon as JSON becomes valid
            const e2 = data.indexOf(END);
            if (e2 === -1) {
                // No end marker yet, accumulate and check for early valid JSON
                this._textToolActive.argBuffer += data;
                // Early emit when JSON becomes valid and we haven't emitted yet
                if (!this._textToolActive.emitted) {
                    const did = this.emitTextToolCallIfValid(progress, this._textToolActive, this._textToolActive.argBuffer);
                    if (did) {
                        this._textToolActive.emitted = true;
                        emittedAny = true;
                    }
                }
                data = "";
                break;
            } else {
                this._textToolActive.argBuffer += data.slice(0, e2);
                // Consume END
                data = data.slice(e2 + END.length);
                // Final attempt to emit if not already
                if (!this._textToolActive.emitted) {
                    const did = this.emitTextToolCallIfValid(progress, this._textToolActive, this._textToolActive.argBuffer);
                    if (did) {
                        emittedAny = true;
                    }
                }
                this._textToolActive = undefined;
                continue;
            }
        }

        // Emit any visible text
        const textToEmit = visibleOut;
        if (textToEmit && textToEmit.length > 0) {
            progress.report(new vscode.LanguageModelTextPart(textToEmit));
            emittedText = true;
            emittedAny = true;
        }

        // Store leftover for next chunk
        this._textToolParserBuffer = data;

        return { emittedText, emittedAny };
    }

    private emitTextToolCallIfValid(
        progress: vscode.Progress<vscode.LanguageModelResponsePart>,
        call: { name?: string; index?: number; argBuffer: string; emitted?: boolean },
        argText: string,
    ): boolean {
        const name = call.name ?? "unknown_tool";
        const parsed = tryParseJSONObject(argText);
        if (!parsed.ok) {
            return false;
        }
        const canonical = JSON.stringify(parsed.value);
        const key = `${name}:${canonical}`;
        // identity-based dedupe when index is present
        if (typeof call.index === "number") {
            const idKey = `${name}:${call.index}`;
            if (this._emittedTextToolCallIds.has(idKey)) {
                return false;
            }
            // Mark identity as emitted
            this._emittedTextToolCallIds.add(idKey);
        } else if (this._emittedTextToolCallKeys.has(key)) {
            return false;
        }
        this._emittedTextToolCallKeys.add(key);
        const id = `tct_${Math.random().toString(36).slice(2, 10)}`;
        progress.report(new vscode.LanguageModelToolCallPart(id, name, parsed.value));
        return true;
    }

    private async flushActiveTextToolCall(
        progress: vscode.Progress<vscode.LanguageModelResponsePart>,
    ): Promise<void> {
        if (!this._textToolActive) {
            return;
        }
        const argText = this._textToolActive.argBuffer;
        const parsed = tryParseJSONObject(argText);
        if (!parsed.ok) {
            return;
        }
        // Emit (dedupe ensures we don't double-emit)
        this.emitTextToolCallIfValid(progress, this._textToolActive, argText);
        this._textToolActive = undefined;
    }

	/**
	 * Try to emit a buffered tool call when a valid name and JSON arguments are available.
	 * @param index The tool call index from the stream.
	 * @param progress Progress reporter for parts.
	 */
    private async tryEmitBufferedToolCall(
        index: number,
        progress: vscode.Progress<vscode.LanguageModelResponsePart>
    ): Promise<void> {
        const buf = this._toolCallBuffers.get(index);
        if (!buf) {
            return;
        }
        if (!buf.name) {
            return;
        }
        const canParse = tryParseJSONObject(buf.args);
        if (!canParse.ok) {
            return;
        }
        const id = buf.id ?? `call_${Math.random().toString(36).slice(2, 10)}`;
        const parameters = canParse.value;
        try {
            const canonical = JSON.stringify(parameters);
            this._emittedTextToolCallKeys.add(`${buf.name}:${canonical}`);
        } catch { /* ignore */ }
        progress.report(new vscode.LanguageModelToolCallPart(id, buf.name, parameters));
        this._toolCallBuffers.delete(index);
        this._completedToolCallIndices.add(index);
    }

	/**
	 * Flush all buffered tool calls, optionally throwing if arguments are not valid JSON.
	 * @param progress Progress reporter for parts.
	 * @param throwOnInvalid If true, throw when a tool call has invalid JSON args.
	 */
    private async flushToolCallBuffers(
        progress: vscode.Progress<vscode.LanguageModelResponsePart>,
        throwOnInvalid: boolean,
    ): Promise<void> {
        if (this._toolCallBuffers.size === 0) {
            return;
        }
        for (const [idx, buf] of Array.from(this._toolCallBuffers.entries())) {
            const parsed = tryParseJSONObject(buf.args);
            if (!parsed.ok) {
                if (throwOnInvalid) {
                    console.error("[GAIA Model Provider] Invalid JSON for tool call", { idx, snippet: (buf.args || "").slice(0, 200) });
                    throw new Error("Invalid JSON for tool call");
                }
                // When not throwing (e.g. on [DONE]), drop silently to reduce noise
                continue;
            }
            const id = buf.id ?? `call_${Math.random().toString(36).slice(2, 10)}`;
            const name = buf.name ?? "unknown_tool";
            try {
                const canonical = JSON.stringify(parsed.value);
                this._emittedTextToolCallKeys.add(`${name}:${canonical}`);
            } catch { /* ignore */ }
            progress.report(new vscode.LanguageModelToolCallPart(id, name, parsed.value));
            this._toolCallBuffers.delete(idx);
            this._completedToolCallIndices.add(idx);
        }
    }

	/** Strip provider control tokens like <|tool_calls_section_begin|> and <|tool_call_begin|> from streamed text. */
	private stripControlTokens(text: string): string {
		try {
			// Remove section markers and explicit tool call begin/argument/end markers that some backends stream as text
			return text
				.replace(/<\|[a-zA-Z0-9_-]+_section_(?:begin|end)\|>/g, "")
				.replace(/<\|tool_call_(?:argument_)?(?:begin|end)\|>/g, "");
		} catch {
			return text;
		}
	}
}
