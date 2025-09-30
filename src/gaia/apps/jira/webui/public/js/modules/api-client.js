// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export class ApiClient {
    constructor() {
        // Check if running in Electron
        this.isElectron = window.electronAPI !== undefined;

        // Get configuration - prioritize GAIA_MCP_URL from environment
        const config = window.APP_CONFIG || {};
        // First check for environment variable injected by dev-server (like in mcp-status app)
        // Then fall back to config file
        this.mcpBaseUrl = window.ENV?.GAIA_MCP_URL || config.MCP_BASE_URL || 'http://localhost:8765';
        this.debug = config.DEBUG || false;

        // Always log the configuration for debugging
        console.log('API Client initialized:', {
            isElectron: this.isElectron,
            mcpBaseUrl: this.mcpBaseUrl,
            debug: this.debug,
            fullConfig: window.APP_CONFIG,
            env: window.ENV
        });
    }

    async getConfig() {
        if (this.isElectron && window.electronAPI?.getConfig) {
            const config = await window.electronAPI.getConfig();
            // Update MCP URL from Electron config if provided
            if (config.mcpUrl) {
                this.mcpBaseUrl = config.mcpUrl;
                if (this.debug) {
                    console.log('Updated MCP URL from Electron config:', this.mcpBaseUrl);
                }
            }
            return config;
        }

        // In development, return a default config
        const appConfig = window.APP_CONFIG || {};
        return {
            name: 'jira',
            displayName: 'JAX',
            version: '1.0.0',
            environment: appConfig.ENVIRONMENT || 'development'
        };
    }

    async sendMessage(message) {
        // In Electron mode, use IPC
        if (this.isElectron && window.electronAPI?.invoke) {
            const result = await window.electronAPI.invoke('jira:query', message);
            if (result.success) {
                return {
                    message: this.formatJiraResponse(result.data),
                    data: result.data
                };
            } else {
                throw new Error(result.error);
            }
        }

        // Call MCP Bridge directly
        try {
            console.log('Sending request to:', `${this.mcpBaseUrl}/jira`);
            console.log('Request body:', { query: message });

            const response = await fetch(`${this.mcpBaseUrl}/jira`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ query: message })
            });

            if (!response.ok) {
                throw new Error(`MCP Bridge returned ${response.status}: ${response.statusText}`);
            }

            const result = await response.json();
            if (this.debug) {
                console.log('Raw MCP response:', result);
            }

            // Check if it's an error response
            if (result.error) {
                if (this.debug) {
                    console.error('MCP Error:', result.error);
                }
                return {
                    message: `Error: ${result.error}`,
                    data: null
                };
            }

            // Handle MCP bridge response format (success, result, conversation)
            if (result.success) {
                // Check if we have conversation data with issues
                if (result.conversation && Array.isArray(result.conversation)) {
                    // Look for system messages with issues data
                    for (const msg of result.conversation) {
                        if (msg.role === 'system' && msg.content && msg.content.issues) {
                            console.log('Found issues in conversation:', msg.content.issues);
                            return {
                                message: `Found ${msg.content.total || msg.content.issues.length} issue(s)`,
                                data: {
                                    type: 'issues',
                                    items: msg.content.issues
                                }
                            };
                        }
                    }

                    // Look for the final assistant answer
                    const lastAssistant = result.conversation.filter(m => m.role === 'assistant').pop();
                    if (lastAssistant && lastAssistant.content && lastAssistant.content.answer) {
                        return {
                            message: lastAssistant.content.answer,
                            data: null
                        };
                    }
                }

                // If result field exists and has content
                if (result.result) {
                    // The actual response is in result.result
                    const responseText = result.result;
                    console.log('JIRA Agent response:', responseText);

                    // Try to extract JSON from the response
                    let parsedData = null;
                    if (typeof responseText === 'string') {
                        // First check for JSON in markdown code blocks
                        const jsonMatch = responseText.match(/```json\n([\s\S]*?)\n```/);
                        if (jsonMatch) {
                            try {
                                parsedData = JSON.parse(jsonMatch[1]);
                                console.log('Extracted JSON from markdown:', parsedData);
                            } catch (e) {
                                console.warn('Failed to parse JSON from markdown:', e);
                            }
                        }

                        // If no JSON found, try to parse the whole response
                        if (!parsedData) {
                            try {
                                parsedData = JSON.parse(responseText);
                                console.log('Parsed entire response as JSON:', parsedData);
                            } catch {
                                // Not JSON, just return the text message
                                console.log('Response is plain text:', responseText);
                                return {
                                    message: responseText || 'Query completed',
                                    data: null
                                };
                            }
                        }
                    }

                    // If we have parsed data with the right structure, use it
                    if (parsedData && (parsedData.type === 'issues' || parsedData.issues)) {
                        return {
                            message: `Found ${parsedData.issues?.length || 0} issue(s)`,
                            data: {
                                type: 'issues',
                                items: parsedData.issues || []
                            }
                        };
                    } else if (parsedData && (parsedData.type === 'projects' || parsedData.projects)) {
                        return {
                            message: `Found ${parsedData.projects?.length || 0} project(s)`,
                            data: {
                                type: 'projects',
                                items: parsedData.projects || []
                            }
                        };
                    } else {
                        // Return the response without mock data
                        return {
                            message: responseText || 'Query completed',
                            data: null
                        };
                    }
                }
            }

            // Fallback for unexpected response format
            return {
                message: 'Query completed',
                data: null
            };
        } catch (error) {
            // Don't fall back to mock data - show real error
            console.error('Failed to communicate with MCP Bridge:', error);
            throw new Error(`Cannot connect to GAIA MCP Bridge at ${this.mcpBaseUrl}: ${error.message}`);
        }
    }

    formatJiraResponse(data) {
        if (!data) return 'No results found.';

        if (data.issues && Array.isArray(data.issues)) {
            return `Found ${data.issues.length} issue(s) matching your query.`;
        }

        if (data.projects && Array.isArray(data.projects)) {
            return `Found ${data.projects.length} project(s).`;
        }

        if (data.message) {
            return data.message;
        }

        return 'Query executed successfully.';
    }

    async checkConnection() {
        if (this.isElectron && window.electronAPI?.checkConnection) {
            return await window.electronAPI.checkConnection();
        }

        // Check actual MCP Bridge connectivity
        try {
            console.log(`Checking MCP Bridge at: ${this.mcpBaseUrl}/health`);

            const response = await fetch(`${this.mcpBaseUrl}/health`, {
                method: 'GET',
                signal: AbortSignal.timeout(5000) // 5 second timeout
            });

            console.log('Health check response status:', response.status);

            if (response.ok) {
                const data = await response.json();
                console.log('MCP Bridge health response:', data);
                return data.status === 'healthy';
            } else {
                console.error('Health check failed with status:', response.status);
            }

            return false;
        } catch (error) {
            console.error('MCP Bridge connection check failed:', error);
            console.error('Make sure MCP Bridge is running: gaia mcp start');
            return false;
        }
    }

}