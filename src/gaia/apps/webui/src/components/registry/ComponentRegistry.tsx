// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * ComponentRegistry - Browse all Component Framework files organized by category.
 * Supports viewing and editing MD files with YAML frontmatter.
 */

import { useState, useEffect, useMemo } from 'react';
import { Search, FolderOpen, FileText, ChevronDown, ChevronRight, Code2, Edit3 } from 'lucide-react';
import * as api from '../../services/api';
import type { ComponentItem } from '../../types';
import { ComponentFileModal } from './ComponentFileModal';
import './ComponentRegistry.css';

const CATEGORY_ICONS: Record<string, React.ReactNode> = {
    memory: <FolderOpen size={16} />,
    knowledge: <FileText size={16} />,
    tasks: <FolderOpen size={16} />,
    commands: <Code2 size={16} />,
    documents: <FileText size={16} />,
    checklists: <FolderOpen size={16} />,
    personas: <FolderOpen size={16} />,
    workflows: <Code2 size={16} />,
    templates: <FileText size={16} />,
};

const CATEGORY_LABELS: Record<string, string> = {
    memory: 'Memory',
    knowledge: 'Knowledge',
    tasks: 'Tasks',
    commands: 'Commands',
    documents: 'Documents',
    checklists: 'Checklists',
    personas: 'Personas',
    workflows: 'Workflows',
    templates: 'Templates',
};

export function ComponentRegistry() {
    const [components, setComponents] = useState<ComponentItem[]>([]);
    const [categories, setCategories] = useState<Record<string, ComponentItem[]>>({});
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    const [expandedCategory, setExpandedCategory] = useState<string | null>(null);

    // Modal state
    const [editingComponent, setEditingComponent] = useState<{ category: string; name: string } | null>(null);
    const [fileContent, setFileContent] = useState<string>('');
    const [filePath, setFilePath] = useState<string>('');
    const [frontmatter, setFrontmatter] = useState<Record<string, unknown>>({});
    const [isLoadingFile, setIsLoadingFile] = useState(false);
    const [isEditing, setIsEditing] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);

    useEffect(() => {
        api.listComponents()
            .then((data: { components: ComponentItem[]; total: number }) => {
                setComponents(data.components || []);
                setTotal(data.total || 0);

                // Group by category
                const grouped: Record<string, ComponentItem[]> = {};
                (data.components || []).forEach((comp: ComponentItem) => {
                    if (!grouped[comp.category]) {
                        grouped[comp.category] = [];
                    }
                    grouped[comp.category].push(comp);
                });
                setCategories(grouped);

                // Expand first category by default
                const firstCategory = Object.keys(grouped).sort()[0];
                if (firstCategory) {
                    setExpandedCategory(firstCategory);
                }
            })
            .catch((_err: unknown) => {
                console.error('Failed to load component registry:', _err);
            })
            .finally(() => setLoading(false));
    }, []);

    const filteredCategories = useMemo(() => {
        if (!search.trim()) {
            return categories;
        }

        const q = search.toLowerCase();
        const filtered: Record<string, ComponentItem[]> = {};

        Object.entries(categories).forEach(([cat, items]) => {
            const matched = items.filter(
                (item) =>
                    item.name.toLowerCase().includes(q) ||
                    (item.title && item.title.toLowerCase().includes(q)) ||
                    (item.description && item.description.toLowerCase().includes(q)) ||
                    item.category.toLowerCase().includes(q)
            );
            if (matched.length > 0) {
                filtered[cat] = matched;
            }
        });

        return filtered;
    }, [categories, search]);

    const categoryList = useMemo(() => {
        return Object.keys(categories).sort();
    }, [categories]);

    const toggleCategory = (category: string) => {
        setExpandedCategory((prev) => (prev === category ? null : category));
    };

    // Modal functions
    const loadComponentFile = async (category: string, name: string) => {
        setIsLoadingFile(true);
        setSaveError(null);
        try {
            const data = await api.getComponentRaw(category, name);
            setFileContent(data.content);
            setFilePath(data.path);
            setFrontmatter(data.frontmatter || {});
            setEditingComponent({ category, name });
            setIsEditing(false);
        } catch (err) {
            setSaveError(`Failed to load component file: ${err instanceof Error ? err.message : String(err)}`);
        } finally {
            setIsLoadingFile(false);
        }
    };

    const saveComponentFile = async () => {
        if (!editingComponent) return;
        setSaveError(null);
        try {
            await api.saveComponentRaw(editingComponent.category, editingComponent.name, fileContent);
            setIsEditing(false);
            // Close modal after successful save
            setEditingComponent(null);
        } catch (err) {
            setSaveError(`Failed to save component file: ${err instanceof Error ? err.message : String(err)}`);
        }
    };

    const cancelEdit = () => {
        setIsEditing(false);
        setSaveError(null);
        // Reload original content
        if (editingComponent) {
            loadComponentFile(editingComponent.category, editingComponent.name);
        }
    };

    const closeModal = () => {
        setEditingComponent(null);
        setFileContent('');
        setFilePath('');
        setFrontmatter({});
        setSaveError(null);
        setIsEditing(false);
    };

    if (loading) {
        return (
            <div className="component-registry">
                <div className="cr-header">
                    <h1>Component Framework</h1>
                    <p>Loading components...</p>
                </div>
                <div className="cr-loading">
                    <div className="cr-loading-spinner" />
                    <span>Discovering components from component-framework/...</span>
                </div>
            </div>
        );
    }

    return (
        <div className="component-registry">
            {/* Header */}
            <div className="cr-header">
                <h1>Component Framework</h1>
                <p>
                    {total} component{total !== 1 ? 's' : ''} across {categoryList.length} categories
                </p>
            </div>

            {/* Search Bar */}
            <div className="cr-toolbar">
                <div className="cr-search">
                    <Search size={16} className="cr-search-icon" />
                    <input
                        type="text"
                        placeholder="Search components by name, title, or description..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                    />
                </div>
            </div>

            {/* Category Groups */}
            {Object.keys(filteredCategories).length === 0 && (
                <div className="cr-empty">
                    <FolderOpen size={48} strokeWidth={1} />
                    <h3>No components found</h3>
                    <p>
                        {search
                            ? 'Try a different search term.'
                            : 'No components are registered in this category.'}
                    </p>
                </div>
            )}

            <div className="cr-category-list">
                {categoryList.map((category) => {
                    const categoryComponents = filteredCategories[category] || categories[category] || [];
                    const isExpanded = expandedCategory === category;
                    const categoryCount = categories[category]?.length || 0;
                    const filteredCount = categoryComponents.length;

                    return (
                        <div key={category} className="cr-category-group">
                            <div
                                className="cr-category-header"
                                onClick={() => toggleCategory(category)}
                                role="button"
                                tabIndex={0}
                                aria-expanded={expandedCategory === category}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' || e.key === ' ') {
                                        e.preventDefault();
                                        toggleCategory(category);
                                    }
                                }}
                            >
                                <div className="cr-category-icon">
                                    {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                                </div>
                                <div className="cr-category-title">
                                    {CATEGORY_ICONS[category] || <FolderOpen size={16} />}
                                    <span>{CATEGORY_LABELS[category] || category}</span>
                                </div>
                                <div className="cr-category-count">
                                    {filteredCount !== categoryCount ? (
                                        <span className="cr-count-filtered">{filteredCount}</span>
                                    ) : null}
                                    <span className="cr-count-total">{categoryCount}</span>
                                </div>
                            </div>

                            {isExpanded && (
                                <div className="cr-category-content">
                                    {categoryComponents.length === 0 ? (
                                        <div className="cr-category-empty">
                                            <p>No components match your search in this category.</p>
                                        </div>
                                    ) : (
                                        <div className="cr-component-list">
                                            {categoryComponents.map((comp) => (
                                                <div
                                                    key={`${comp.category}/${comp.name}`}
                                                    className="cr-component-card"
                                                    onClick={() => loadComponentFile(comp.category, comp.name)}
                                                    role="button"
                                                    tabIndex={0}
                                                    onKeyDown={(e) => {
                                                        if (e.key === 'Enter') {
                                                            e.preventDefault();
                                                            loadComponentFile(comp.category, comp.name);
                                                        }
                                                    }}
                                                >
                                                    <div className="cr-component-icon">
                                                        <FileText size={18} />
                                                    </div>
                                                    <div className="cr-component-info">
                                                        <div className="cr-component-name">{comp.name}</div>
                                                        <div className="cr-component-title">{comp.title}</div>
                                                        <div className="cr-component-description">{comp.description}</div>
                                                    </div>
                                                    <div className="cr-component-actions">
                                                        <button
                                                            className="cr-action-btn"
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                loadComponentFile(comp.category, comp.name);
                                                            }}
                                                            title="View source"
                                                        >
                                                            <Code2 size={14} />
                                                        </button>
                                                        <button
                                                            className="cr-action-btn cr-action-btn-edit"
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                loadComponentFile(comp.category, comp.name).then(() => setIsEditing(true));
                                                            }}
                                                            title="Edit"
                                                        >
                                                            <Edit3 size={14} />
                                                        </button>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>

            {/* File Modal */}
            {editingComponent && (
                <ComponentFileModal
                    category={CATEGORY_LABELS[editingComponent.category] || editingComponent.category}
                    name={editingComponent.name}
                    filePath={filePath}
                    content={fileContent}
                    frontmatter={frontmatter}
                    isLoading={isLoadingFile}
                    isEditing={isEditing}
                    saveError={saveError}
                    onClose={closeModal}
                    onSave={saveComponentFile}
                    onCancel={cancelEdit}
                    onContentChange={setFileContent}
                    onToggleEdit={() => setIsEditing((prev) => !prev)}
                />
            )}
        </div>
    );
}
