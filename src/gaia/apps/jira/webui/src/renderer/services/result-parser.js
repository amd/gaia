// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Result Parser
// Handles parsing different response formats from JIRA operations

class ResultParser {
  constructor() {
    this.resultTypes = {
      ISSUES: 'issues',
      PROJECTS: 'projects', 
      CREATED_ISSUE: 'created_issue',
      SEARCH_RESULTS: 'search_results',
      CHAT_RESPONSE: 'chat_response',
      ERROR: 'error',
      UNKNOWN: 'unknown'
    };
  }

  parseResult(result) {
    console.log('🔍 parseResult received:', result);
    
    if (!result) {
      return { type: this.resultTypes.ERROR, message: 'No result provided' };
    }

    // Priority 1: Check for conversation-based response format (new format from MCP bridge)
    if (result.conversation && Array.isArray(result.conversation)) {
      console.log('💬 Found conversation-based response format at top level');
      return this.parseConversationResponse(result);
    }

    // Priority 2: Handle new JiraAgent JSON API response structure directly
    if (result.success !== undefined && result.metadata && result.metadata.tool_calls) {
      console.log('🎯 Found clean JiraAgent JSON API structure - processing directly');
      return this.parseNewJiraAgentResponse(result);
    }

    // Priority 3: Check for errors first
    if (!result.success && result.error) {
      return { type: this.resultTypes.ERROR, error: result.error };
    }

    // Priority 4: Parse data if present
    if (result.data) {
      return this.parseDirectResponse(result.data);
    }

    return { type: this.resultTypes.UNKNOWN, data: result };
  }


  parseDirectResponse(data) {
    console.log('🔍 parseDirectResponse received data:', data);
    
    // Check if it's already in the new JSON API structure
    if (data.success !== undefined && data.metadata) {
      console.log('📝 Found nested JSON API structure');
      return this.parseNewJiraAgentResponse(data);
    }

    // Check for conversation-based response format
    if (data.conversation && Array.isArray(data.conversation)) {
      console.log('💬 Found conversation-based response format');
      return this.parseConversationResponse(data);
    }

    // Handle simple answer/message in data
    if (data.answer && typeof data.answer === 'string') {
      console.log('📝 Using answer as chat response');
      return {
        type: this.resultTypes.CHAT_RESPONSE,
        message: data.answer
      };
    }

    return { type: this.resultTypes.UNKNOWN, data };
  }


  parseConversationResponse(data) {
    console.log('🔍 parseConversationResponse parsing conversation:', data.conversation);
    
    let assistantAnswer = null;
    let systemData = null;
    let performanceStats = [];
    let lastError = null;
    
    // Parse conversation array to extract relevant data
    for (const message of data.conversation) {
      console.log(`  Processing message - role: ${message.role}, content type:`, typeof message.content);
      
      if (message.role === 'assistant' && message.content) {
        // Get the final assistant answer
        if (message.content.answer) {
          console.log('    Found assistant answer:', message.content.answer);
          assistantAnswer = message.content.answer;
        }
      } else if (message.role === 'system' && message.content) {
        // Check for errors
        if (message.content.status === 'error' || message.content.error) {
          console.log('    Found error:', message.content.error);
          lastError = message.content.error;
        }
        // Extract system data (issues, projects, etc.)
        if (message.content.issues || message.content.projects) {
          console.log('    Found system data with issues/projects:', message.content);
          systemData = message.content;
        }
        // Collect performance stats
        if (message.content.type === 'stats' && message.content.performance_stats) {
          console.log('    Found performance stats:', message.content.performance_stats);
          performanceStats.push({
            step: message.content.step,
            stats: message.content.performance_stats
          });
        }
      }
    }
    
    console.log('  Final extracted data:');
    console.log('    assistantAnswer:', assistantAnswer);
    console.log('    systemData:', systemData);
    console.log('    lastError:', lastError);
    console.log('    performanceStats:', performanceStats);
    
    // If there was an error and no successful data, return error result
    if (lastError && !systemData && !assistantAnswer) {
      console.log('❌ Search failed with error');
      return {
        type: this.resultTypes.ERROR,
        error: `Search failed: ${lastError}`,
        performanceStats: performanceStats,
        rawData: data
      };
    }
    
    // Determine result type based on system data
    if (systemData) {
      if (systemData.issues && Array.isArray(systemData.issues)) {
        console.log('🎯 Found issues in conversation:', systemData.issues);
        const result = {
          type: this.resultTypes.ISSUES,
          issues: systemData.issues,
          total: systemData.total || systemData.issues.length,
          jql: systemData.jql,
          message: assistantAnswer,
          performanceStats: performanceStats,
          rawData: data  // Preserve original data for Raw tab
        };
        console.log('  Returning ISSUES result:', result);
        return result;
      }
      
      if (systemData.projects && Array.isArray(systemData.projects)) {
        console.log('🎯 Found projects in conversation:', systemData.projects);
        return {
          type: this.resultTypes.PROJECTS,
          projects: systemData.projects,
          total: systemData.total || systemData.projects.length,
          message: assistantAnswer,
          performanceStats: performanceStats,
          rawData: data  // Preserve original data for Raw tab
        };
      }
      
      if (systemData.created) {
        console.log('🎯 Found created issue in conversation:', systemData);
        return {
          type: this.resultTypes.CREATED_ISSUE,
          issue: {
            key: systemData.key,
            url: systemData.url,
            ...systemData
          },
          message: assistantAnswer,
          performanceStats: performanceStats,
          rawData: data  // Preserve original data for Raw tab
        };
      }
    }
    
    // If we only have an answer, treat as chat response
    if (assistantAnswer) {
      return {
        type: this.resultTypes.CHAT_RESPONSE,
        message: assistantAnswer,
        performanceStats: performanceStats,
        rawData: data  // Preserve original data for Raw tab
      };
    }
    
    // Fallback to unknown with full data
    return { 
      type: this.resultTypes.UNKNOWN, 
      data: data,
      performanceStats: performanceStats,
      rawData: data  // Preserve original data for Raw tab
    };
  }

  parseNewJiraAgentResponse(response) {
    console.log('🔍 parseNewJiraAgentResponse parsing:', response);
    
    // Check if the request was successful
    if (!response.success) {
      console.log('❌ New JSON API returned error:', response.error);
      return {
        type: this.resultTypes.ERROR,
        error: response.error?.message || 'Unknown error'
      };
    }

    // Check tool_calls in metadata for direct tool results
    if (response.metadata && response.metadata.tool_calls && response.metadata.tool_calls.length > 0) {
      console.log('🔧 Found tool calls in metadata:', response.metadata.tool_calls);
      
      for (const toolCall of response.metadata.tool_calls) {
        const toolResult = toolCall.result;
        
        // Check for issues in tool result
        if (toolResult && toolResult.issues && Array.isArray(toolResult.issues)) {
          console.log('🎯 Found issues in tool call result:', toolResult);
          return {
            type: this.resultTypes.ISSUES,
            issues: toolResult.issues,
            total: toolResult.total || toolResult.issues.length,
            jql: toolResult.jql
          };
        }
        
        // Check for projects in tool result
        if (toolResult && toolResult.projects && Array.isArray(toolResult.projects)) {
          console.log('🎯 Found projects in tool call result:', toolResult);
          return {
            type: this.resultTypes.PROJECTS,
            projects: toolResult.projects,
            total: toolResult.projects.length
          };
        }
        
        // Check for created issue in tool result
        if (toolResult && toolResult.created) {
          console.log('🎯 Found created issue in tool call result:', toolResult);
          return {
            type: this.resultTypes.CREATED_ISSUE,
            issue: {
              key: toolResult.key,
              url: toolResult.url,
              ...toolResult
            }
          };
        }
      }
    }

    // If we have data.answer, treat as chat response
    if (response.data && response.data.answer) {
      console.log('💬 Found answer in data, treating as chat response');
      return {
        type: this.resultTypes.CHAT_RESPONSE,
        message: response.data.answer
      };
    }

    console.log('❓ No recognized data in new JSON API response');
    return { type: this.resultTypes.UNKNOWN, data: response };
  }


  formatChatResponse(parsedResult) {
    // If we have a message from the assistant, use it directly
    if (parsedResult.message) {
      return parsedResult.message;
    }
    
    // Otherwise format based on type
    switch (parsedResult.type) {
      case this.resultTypes.ISSUES:
        return this.formatIssuesForChat(parsedResult);
      case this.resultTypes.PROJECTS:
        return this.formatProjectsForChat(parsedResult);
      case this.resultTypes.CREATED_ISSUE:
        return this.formatCreatedIssueForChat(parsedResult);
      case this.resultTypes.CHAT_RESPONSE:
        return parsedResult.message;
      case this.resultTypes.ERROR:
        return `Error: ${parsedResult.error}`;
      default:
        return 'Task completed successfully! Check the results panel for details.';
    }
  }

  formatIssuesForChat(parsedResult) {
    const { issues, total } = parsedResult;
    let response = `📋 Found ${total} issue${total !== 1 ? 's' : ''}:\n\n`;
    
    issues.slice(0, 3).forEach(issue => {
      response += `• ${issue.key} - ${issue.summary}\n  Status: ${issue.status} | Priority: ${issue.priority}\n\n`;
    });
    
    if (issues.length > 3) {
      response += `... and ${issues.length - 3} more. Check the Results panel for full details.`;
    }
    
    return response;
  }

  formatProjectsForChat(parsedResult) {
    const { projects, total } = parsedResult;
    let response = `📁 Found ${total} project${total !== 1 ? 's' : ''}:\n\n`;
    
    projects.slice(0, 3).forEach(project => {
      response += `• ${project.key} - ${project.name}\n  Status: ${project.status} | Issues: ${project.issueCount || 0}\n\n`;
    });
    
    if (projects.length > 3) {
      response += `... and ${projects.length - 3} more. Check the Results panel for full details.`;
    }
    
    return response;
  }

  formatCreatedIssueForChat(parsedResult) {
    const { issue } = parsedResult;
    let response = `✅ Successfully created issue: ${issue.key || 'Unknown'}`;
    if (issue.url) {
      response += `\n🔗 ${issue.url}`;
    }
    return response;
  }
}

// Export singleton instance
window.resultParser = new ResultParser();
export default window.resultParser;