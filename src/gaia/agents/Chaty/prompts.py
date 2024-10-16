class Prompts:
    system_prompts = {
        "llama3-pirate": """
<|begin_of_text|><|start_header_id|>system<|end_header_id|>
You are a pirate chatbot who always responds in pirate speak!<|eot_id|>
<|start_header_id|>user<|end_header_id|>
"""
    }

    @staticmethod
    def get_system_prompt(model: str) -> str:
        prompt = Prompts.system_prompts.get(model)
        if prompt is None:
            available_models = ", ".join(Prompts.system_prompts.keys())
            raise ValueError(f"No system prompt found for model '{model}'. Available models: {available_models}")
        return prompt
