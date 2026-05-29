// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Shared icon map for Agent Hub components.
 * Maps lucide icon names (from agent metadata) to components.
 */

import {
    Bot, MessageCircle, Zap, Plug, Wrench, Cpu, Hammer,
    Shield, Code, FileText, FolderSearch, Table, Globe, Mail,
    type LucideIcon,
} from 'lucide-react';

export const AGENT_ICON_MAP: Record<string, LucideIcon> = {
    'message-circle': MessageCircle,
    'file-text': FileText,
    'folder-search': FolderSearch,
    'table': Table,
    'globe': Globe,
    'mail': Mail,
    'zap': Zap,
    'plug': Plug,
    'wrench': Wrench,
    'cpu': Cpu,
    'hammer': Hammer,
    'shield': Shield,
    'code': Code,
    'bot': Bot,
};

export function getAgentIcon(name?: string): LucideIcon {
    return (name && AGENT_ICON_MAP[name]) || Bot;
}
