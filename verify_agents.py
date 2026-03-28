import os
import sys
from pathlib import Path

# Set PYTHONPATH to src
sys.path.insert(0, os.path.abspath("src"))

from gaia.api.agent_registry import AgentRegistry

def main():
    print("Initializing AgentRegistry...")
    registry = AgentRegistry()
    
    print("\nListing available models:")
    models = registry.list_models()
    model_ids = [m["id"] for m in models]
    print(f"Models: {model_ids}")
    
    if "researcher" in model_ids:
        print("\n✅ SUCCESS: 'researcher' agent found in registry!")
        
        print("\nInstantiating 'researcher' agent...")
        agent = registry.get_agent("researcher")
        print(f"Agent name: {agent.agent_name}")
        print(f"System prompt preview: {agent.system_prompt[:100]}...")
        
        tools = agent._format_tools_for_prompt()
        print(f"\nRegistered tools:\n{tools}")
    else:
        print("\n❌ FAILURE: 'researcher' agent NOT found in registry.")

if __name__ == "__main__":
    main()
