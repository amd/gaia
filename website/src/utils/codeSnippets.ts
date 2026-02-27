// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import fs from 'fs';
import path from 'path';

/**
 * Path to the GAIA examples directory
 *
 * Currently: Local path (relative to website root)
 * Future: Can be updated to fetch from GitHub raw URLs when examples are public
 *
 * Example GitHub URL format:
 * https://raw.githubusercontent.com/amd/gaia/main/examples/weather_agent.py
 */
const EXAMPLES_DIR = path.resolve(process.cwd(), '../../../gaia3/examples');

/**
 * Read a code snippet from the GAIA examples directory
 */
export function readExampleFile(filename: string): string {
  try {
    const filePath = path.join(EXAMPLES_DIR, filename);
    return fs.readFileSync(filePath, 'utf-8');
  } catch (error) {
    console.warn(`Warning: Could not read ${filename}:`, error);
    return `# Error: Could not load ${filename}`;
  }
}

/**
 * Extract just the class definition and relevant code from an example file
 */
export function extractClassCode(content: string, className: string): string {
  // Find the class definition
  const classMatch = content.match(new RegExp(`class ${className}[\\s\\S]*?(?=\\n(?:class |def main|if __name__))`));

  if (classMatch) {
    return classMatch[0].trim();
  }

  return content;
}

/**
 * Get syntax-highlighted HTML for code display
 */
export function getSyntaxHighlightedCode(code: string): string {
  // Simple syntax highlighting for Python
  let highlighted = code
    // Keywords
    .replace(/\b(from|import|class|def|return|async|await|if|else|elif|for|while|try|except|with|as)\b/g, '<span class="text-purple-400">$1</span>')
    // Class names and types
    .replace(/\b([A-Z][a-zA-Z0-9_]*)\b/g, '<span class="text-yellow-400">$1</span>')
    // Strings
    .replace(/(["'])(?:(?=(\\?))\2.)*?\1/g, '<span class="text-green-400">$&</span>')
    // Comments
    .replace(/(#.*$)/gm, '<span class="text-gaia-muted">$1</span>')
    // Decorators
    .replace(/(@\w+)/g, '<span class="text-gaia-muted">$1</span>')
    // Built-in functions
    .replace(/\b(print|len|str|int|dict|list|super|self)\b/g, '<span class="text-blue-400">$1</span>');

  return highlighted;
}

/**
 * Code snippets for the landing page
 */
export const codeSnippets = {
  weather: {
    file: 'weather_agent.py',
    extract: (content: string) => {
      // Get just the class definition
      const lines = content.split('\n');
      const startIdx = lines.findIndex(line => line.includes('class WeatherAgent'));
      const endIdx = lines.findIndex((line, idx) => idx > startIdx && line.startsWith('def main'));

      if (startIdx !== -1 && endIdx !== -1) {
        return lines.slice(startIdx, endIdx).join('\n').trim();
      }
      return extractClassCode(content, 'WeatherAgent');
    }
  },

  rag: {
    file: 'rag_doc_agent.py',
    extract: (content: string) => {
      // Get imports and class definition
      const lines = content.split('\n');

      // Find imports
      const importStart = lines.findIndex(line => line.includes('from gaia'));
      const importEnd = lines.findIndex((line, idx) => idx > importStart && line === '');

      // Find class
      const classStart = lines.findIndex(line => line.includes('class DocAgent'));
      const classEnd = lines.findIndex((line, idx) => idx > classStart && line.startsWith('def main'));

      let result = '';
      if (importStart !== -1 && importEnd !== -1) {
        result += lines.slice(importStart, importEnd + 1).join('\n') + '\n\n';
      }
      if (classStart !== -1 && classEnd !== -1) {
        // Get just first few lines of class for demo
        result += lines.slice(classStart, Math.min(classStart + 20, classEnd)).join('\n');
      }

      return result.trim();
    }
  },

  mockup: {
    file: 'product_mockup_agent.py',
    extract: (content: string) => {
      // Get the tool decorator and function
      const lines = content.split('\n');
      const toolStart = lines.findIndex(line => line.includes('@tool'));
      const toolEnd = lines.findIndex((line, idx) => idx > toolStart + 1 && line.trim() === '');

      if (toolStart !== -1 && toolEnd !== -1) {
        return lines.slice(toolStart, Math.min(toolStart + 25, toolEnd)).join('\n').trim();
      }
      return extractClassCode(content, 'ProductMockupAgent');
    }
  },

  workflow: {
    file: 'file_watcher_agent.py',
    extract: (content: string) => {
      // Get the class definition focusing on watch_directory call
      const lines = content.split('\n');
      const classStart = lines.findIndex(line => line.includes('class FileWatcherAgent'));
      const watchCall = lines.findIndex((line, idx) => idx > classStart && line.includes('self.watch_directory'));

      if (classStart !== -1 && watchCall !== -1) {
        return lines.slice(classStart, watchCall + 10).join('\n').trim();
      }
      return extractClassCode(content, 'FileWatcherAgent');
    }
  }
};

/**
 * Load all code snippets from example files
 */
export function loadAllSnippets() {
  const result: Record<string, { code: string; highlighted: string }> = {};

  for (const [key, config] of Object.entries(codeSnippets)) {
    const fullContent = readExampleFile(config.file);
    const extractedCode = config.extract(fullContent);

    result[key] = {
      code: extractedCode,
      highlighted: getSyntaxHighlightedCode(extractedCode)
    };
  }

  return result;
}
