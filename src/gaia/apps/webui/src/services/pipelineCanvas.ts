// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * API service for Pipeline Canvas.
 *
 * Bridges the visual canvas with the backend:
 * - Fetch agent registry for palette items
 * - Save canvas state as pipeline template YAML
 * - Load canvas state from existing template
 */

import type { AgentRegistryEntry, PipelineTemplate, CanvasTemplateExport } from '../types';
import * as api from './api';
import { log } from '../utils/logger';

const API_BASE = '/api';

/** Fetch all registered agents from the backend. */
export async function fetchAgents(): Promise<{ agents: AgentRegistryEntry[]; categories: Record<string, string[]>; total: number }> {
    return api.listAgents();
}

/**
 * Serialize canvas state into a pipeline template and save it.
 *
 * Groups agent nodes by assigned stage, builds agent_categories map,
 * and constructs routing rules from canvas edges.
 */
export async function saveCanvasAsTemplate(
    name: string,
    canvas: CanvasTemplateExport,
): Promise<PipelineTemplate> {
    return api.createTemplate({
        name: canvas.name,
        description: canvas.description,
        quality_threshold: canvas.quality_threshold,
        max_iterations: canvas.max_iterations,
        agent_categories: canvas.agent_categories,
        routing_rules: canvas.routing_rules,
    });
}

/**
 * Load a template and convert it to canvas state.
 *
 * Reads the template YAML, extracts agent categories,
 * and produces CanvasNode + CanvasEdge arrays.
 */
export async function loadTemplateAsCanvas(name: string): Promise<{ template: PipelineTemplate; yaml: string }> {
    const [template, yaml] = await Promise.all([
        api.getTemplate(name),
        api.getTemplateRaw(name),
    ]);
    return { template, yaml };
}

/**
 * Update an existing template from canvas state.
 */
export async function updateTemplateFromCanvas(
    name: string,
    canvas: CanvasTemplateExport,
): Promise<PipelineTemplate> {
    return api.updateTemplate(name, {
        description: canvas.description,
        quality_threshold: canvas.quality_threshold,
        max_iterations: canvas.max_iterations,
        agent_categories: canvas.agent_categories,
        routing_rules: canvas.routing_rules,
    });
}

/**
 * Parse raw YAML into structured agent_categories and routing_rules.
 *
 * This is a lightweight YAML parser for the specific template format.
 * For robust parsing, the backend TemplateService should be used.
 */
export function parseTemplateYaml(yaml: string): {
    agent_categories?: Record<string, string[]>;
    routing_rules?: Array<Record<string, unknown>>;
    quality_threshold?: number;
    max_iterations?: number;
    description?: string;
} {
    const result: Record<string, unknown> = {};

    // Simple key-value extraction for top-level fields
    const lines = yaml.split('\n');
    let currentSection: string | null = null;
    let currentSubSection: string | null = null;

    for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) continue;

        // Top-level keys
        if (!line.startsWith(' ') && trimmed.includes(':')) {
            const [key] = trimmed.split(':');
            if (['agent_categories', 'routing_rules', 'quality_threshold', 'max_iterations', 'description', 'name'].includes(key)) {
                currentSection = key;
                currentSubSection = null;
                if (['quality_threshold', 'max_iterations'].includes(key)) {
                    const [, value] = trimmed.split(':');
                    result[key] = Number(value.trim());
                } else if (key === 'description') {
                    result[key] = trimmed.split(':').slice(1).join(':').trim().replace(/"/g, '');
                }
            }
        }

        // agent_categories section
        if (currentSection === 'agent_categories' && line.startsWith('  ')) {
            if (!line.startsWith('    ')) {
                const [key] = trimmed.split(':');
                currentSubSection = key;
                result.agent_categories = result.agent_categories || {};
                (result.agent_categories as Record<string, string[]>)[key] = [];
            } else if (currentSubSection && trimmed.startsWith('- ')) {
                const agentId = trimmed.slice(2).trim();
                const cats = (result.agent_categories as Record<string, string[]>);
                if (cats[currentSubSection]) {
                    cats[currentSubSection].push(agentId);
                }
            }
        }
    }

    return result;
}
