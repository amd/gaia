// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * MCP HTTP Client for GAIA Electron Apps
 *
 * This shared client communicates with the GAIA MCP Bridge via HTTP/REST,
 * following the patterns documented in docs/mcp.md
 *
 * Used by all GAIA Electron apps that need to interact with MCP services.
 */

class MCPClient {
  constructor(options = {}) {
    // Allow override via environment variable for development
    const envUrl = process.env.GAIA_MCP_URL;
    this.baseUrl = envUrl || options.baseUrl || 'http://localhost:8765';
    this.timeout = options.timeout || 60000;
    this.debug = options.debug || false;
    this.verbose = options.verbose || false;

    if (this.debug) {
      console.log(`🔌 MCP Client initialized:`);
      console.log(`   Base URL: ${this.baseUrl}`);
      console.log(`   Timeout: ${this.timeout}ms`);
      if (envUrl) {
        console.log(`   (Using GAIA_MCP_URL from environment)`);
      }
    }
  }

  /**
   * Check if MCP bridge is healthy
   */
  async checkHealth() {
    try {
      const response = await fetch(`${this.baseUrl}/health`, {
        method: 'GET',
        signal: AbortSignal.timeout(5000) // Quick timeout for health check
      });

      if (!response.ok) {
        throw new Error(`Health check failed: ${response.status}`);
      }

      const data = await response.json();

      if (this.debug) {
        console.log('✅ MCP Bridge health check:', data);
      }

      return data;
    } catch (error) {
      if (this.debug) {
        console.error('❌ MCP Bridge health check failed:', error);
      }
      throw error;
    }
  }

  /**
   * Get list of available tools from MCP bridge
   */
  async getTools() {
    try {
      const response = await fetch(`${this.baseUrl}/tools`, {
        method: 'GET',
        signal: AbortSignal.timeout(this.timeout)
      });

      if (!response.ok) {
        throw new Error(`Failed to get tools: ${response.status}`);
      }

      return await response.json();
    } catch (error) {
      if (this.debug) {
        console.error('Failed to get tools:', error);
      }
      throw error;
    }
  }

  /**
   * Execute a chat query (maintains conversation context)
   */
  async queryChat(query, options = {}) {
    try {
      const payload = {
        query,
        ...options
      };

      const response = await fetch(`${this.baseUrl}/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(this.timeout)
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Chat query failed (${response.status}): ${errorText}`);
      }

      return await response.json();
    } catch (error) {
      if (this.debug) {
        console.error('Chat query failed:', error);
      }
      throw error;
    }
  }

  /**
   * Execute a direct LLM query (no conversation context)
   */
  async queryLLM(query, options = {}) {
    try {
      const payload = {
        query,
        ...options
      };

      const response = await fetch(`${this.baseUrl}/llm`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(this.timeout)
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`LLM query failed (${response.status}): ${errorText}`);
      }

      return await response.json();
    } catch (error) {
      if (this.debug) {
        console.error('LLM query failed:', error);
      }
      throw error;
    }
  }

  /**
   * Query JIRA using the direct /jira endpoint
   */
  async queryJira(query) {
    try {
      if (this.debug) {
        console.log(`🔍 Sending JIRA query: "${query}"`);
      }

      const response = await fetch(`${this.baseUrl}/jira`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ query }),
        signal: AbortSignal.timeout(this.timeout)
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`JIRA query failed (${response.status}): ${errorText}`);
      }

      const result = await response.json();

      if (this.debug) {
        console.log('✅ JIRA query result:', result);
      }

      return result;
    } catch (error) {
      if (this.debug) {
        console.error('❌ JIRA query failed:', error);
      }
      throw error;
    }
  }

  /**
   * Execute a JSON-RPC call for more advanced operations
   */
  async executeJsonRpc(method, params = {}) {
    try {
      const payload = {
        jsonrpc: '2.0',
        id: Date.now().toString(),
        method,
        params
      };

      if (this.verbose) {
        console.log('📤 JSON-RPC Request:', payload);
      }

      const response = await fetch(this.baseUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(this.timeout)
      });

      if (!response.ok) {
        throw new Error(`JSON-RPC call failed: ${response.status}`);
      }

      const result = await response.json();

      if (this.verbose) {
        console.log('📥 JSON-RPC Response:', result);
      }

      // Check for JSON-RPC error
      if (result.error) {
        throw new Error(`JSON-RPC Error: ${result.error.message || JSON.stringify(result.error)}`);
      }

      return result.result;
    } catch (error) {
      if (this.debug) {
        console.error('JSON-RPC call failed:', error);
      }
      throw error;
    }
  }

  /**
   * Call a specific tool using JSON-RPC
   */
  async callTool(toolName, args = {}) {
    return this.executeJsonRpc('tools/call', {
      name: toolName,
      arguments: args
    });
  }

  /**
   * Initialize a session (optional)
   */
  async initialize(clientName = 'GAIA Electron App', version = '1.0.0') {
    return this.executeJsonRpc('initialize', {
      clientInfo: {
        name: clientName,
        version: version
      }
    });
  }

  /**
   * Check if MCP bridge is available
   */
  async isAvailable() {
    try {
      const health = await this.checkHealth();
      return health && (health.status === 'healthy' || health.status === 'ok');
    } catch {
      return false;
    }
  }

  /**
   * Wait for MCP bridge to become available
   */
  async waitForBridge(maxAttempts = 10, delayMs = 2000) {
    for (let i = 0; i < maxAttempts; i++) {
      if (await this.isAvailable()) {
        if (this.debug) {
          console.log(`✅ MCP Bridge available after ${i + 1} attempts`);
        }
        return true;
      }

      if (this.debug) {
        console.log(`⏳ Waiting for MCP Bridge... (attempt ${i + 1}/${maxAttempts})`);
      }

      await new Promise(resolve => setTimeout(resolve, delayMs));
    }

    return false;
  }
}

module.exports = MCPClient;