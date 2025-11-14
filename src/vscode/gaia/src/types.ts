/*
Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
*/

/**
 * OpenAI function-call entry emitted by assistant messages.
 */
export interface OpenAIToolCall {
	id: string;
	type: "function";
	function: { name: string; arguments: string };
}

/**
 * OpenAI function tool definition used to advertise tools.
 */
export interface OpenAIFunctionToolDef {
	type: "function";
	function: { name: string; description?: string; parameters?: object };
}

/**
 * OpenAI-style chat message used for GAIA server requests.
 */
export interface OpenAIChatMessage {
	role: OpenAIChatRole;
	content?: string;
	name?: string;
	tool_calls?: OpenAIToolCall[];
	tool_call_id?: string;
}


/**
 * Buffer used to accumulate streamed tool call parts until arguments are valid JSON.
 */
export interface ToolCallBuffer {
	id?: string;
	name?: string;
	args: string;
}

/**
 * Model entry from the /models endpoint
 */
export interface GaiaModel {
	id: string;
	object: string;
	created?: number;
	owned_by?: string;
}

/**
 * Response from the /models endpoint
 */
export interface GaiaModelsResponse {
	object: string;
	data: GaiaModel[];
}

/** OpenAI-style chat roles. */
export type OpenAIChatRole = "system" | "user" | "assistant" | "tool";
