#!/usr/bin/env python3
"""
GAIA MCP Validation Script
Tests the MCP bridge functionality with various queries.
"""

import asyncio
import websockets
import json
import sys

class MCPValidator:
    def __init__(self, uri="ws://localhost:8765"):
        self.uri = uri
        self.session_id = None
    
    async def validate(self):
        """Run comprehensive MCP validation tests."""
        print("Starting GAIA MCP Validation")
        print(f"Connecting to: {self.uri}")
        print("-" * 50)
        
        try:
            async with websockets.connect(self.uri) as websocket:
                # Test 1: Initialize session
                success = await self._test_initialize(websocket)
                if not success:
                    return False
                
                # Test 2: List tools  
                success = await self._test_list_tools(websocket)
                if not success:
                    return False
                
                # Test 3: Test LLM query
                success = await self._test_llm_query(websocket)
                if not success:
                    return False
                
                # Test 4: Test chat
                success = await self._test_chat(websocket)
                if not success:
                    return False
                
                # Test 5: Test resources
                success = await self._test_resources(websocket)
                if not success:
                    return False
                
                print("-" * 50)
                print("All MCP validation tests passed!")
                print("GAIA MCP Bridge is working correctly")
                return True
                
        except ConnectionRefusedError:
            print("❌ Cannot connect to MCP server")
            print("   Start the server with: gaia mcp start")
            return False
        except Exception as e:
            print(f"❌ Validation failed: {e}")
            return False
    
    async def _send_request(self, websocket, method, params=None, request_id=None):
        """Send MCP request and get response."""
        if request_id is None:
            request_id = method.replace("/", "_")
        
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method
        }
        
        if params:
            request["params"] = params
        
        await websocket.send(json.dumps(request))
        response = await websocket.recv()
        return json.loads(response)
    
    async def _test_initialize(self, websocket):
        """Test session initialization."""
        print("1. Testing session initialization...")
        
        response = await self._send_request(websocket, "initialize", {
            "clientInfo": {
                "name": "gaia-validator",
                "version": "1.0.0"
            },
            "capabilities": {
                "tools": {},
                "resources": {"subscribe": True}
            }
        })
        
        if "result" in response:
            self.session_id = response["result"].get("sessionId")
            server_info = response["result"].get("serverInfo", {})
            print(f"   SUCCESS: Session initialized: {self.session_id}")
            print(f"   Server: {server_info.get('name', 'unknown')} v{server_info.get('version', 'unknown')}")
            return True
        else:
            print(f"   FAILED to initialize: {response}")
            return False
    
    async def _test_list_tools(self, websocket):
        """Test tool listing."""
        print("2. Testing tool listing...")
        
        response = await self._send_request(websocket, "tools/list")
        
        if "result" in response:
            tools = response["result"].get("tools", [])
            print(f"   SUCCESS: Found {len(tools)} tools:")
            for tool in tools:
                print(f"      - {tool['name']}: {tool['description']}")
            return True
        else:
            print(f"   FAILED to list tools: {response}")
            return False
    
    async def _test_llm_query(self, websocket):
        """Test LLM query execution."""
        print("3. Testing LLM query...")
        
        response = await self._send_request(websocket, "tools/call", {
            "name": "gaia.query",
            "arguments": {
                "query": "What is the capital of France?",
                "max_tokens": 50
            }
        })
        
        if "result" in response:
            content = response["result"].get("content", [])
            if content:
                result = json.loads(content[0]["text"])
                answer = result.get("response", "").strip()
                print(f"   SUCCESS: Query successful!")
                print(f"   Response: {answer[:100]}{'...' if len(answer) > 100 else ''}")
                return True
            else:
                print("   FAILED: No content in response")
                return False
        else:
            print(f"   FAILED: Query failed: {response}")
            return False
    
    async def _test_chat(self, websocket):
        """Test chat functionality."""
        print("4. Testing chat...")
        
        response = await self._send_request(websocket, "tools/call", {
            "name": "gaia.chat",
            "arguments": {
                "message": "Hello! Can you explain what MCP is in one sentence?",
                "session_id": f"test-{self.session_id}"
            }
        })
        
        if "result" in response:
            content = response["result"].get("content", [])
            if content:
                result = json.loads(content[0]["text"])
                answer = result.get("response", "").strip()
                print(f"   SUCCESS: Chat successful!")
                print(f"   Response: {answer[:100]}{'...' if len(answer) > 100 else ''}")
                return True
            else:
                print("   FAILED: No content in response")
                return False
        else:
            print(f"   FAILED: Chat failed: {response}")
            return False
    
    async def _test_resources(self, websocket):
        """Test resource listing."""
        print("5. Testing resources...")
        
        response = await self._send_request(websocket, "resources/list")
        
        if "result" in response:
            resources = response["result"].get("resources", [])
            print(f"   SUCCESS: Found {len(resources)} resources:")
            for resource in resources:
                print(f"      - {resource['name']}: {resource['description']}")
            return True
        else:
            print(f"   FAILED: Failed to list resources: {response}")
            return False

async def main():
    """Main validation function."""
    validator = MCPValidator()
    success = await validator.validate()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())