class ChatyPrompts:
    system_prompts = {
        "meta-llama/Meta-Llama-3-8B": """
<|begin_of_text|><|start_header_id|>system<|end_header_id|>
You are a pirate chatbot who always responds in pirate speak!<|eot_id|>
<|start_header_id|>user<|end_header_id|>
"""
    }

    @staticmethod
    def get_system_prompt(model: str) -> str:
        return ChatyPrompts.system_prompts.get(model, "No system prompt found for this model.")
