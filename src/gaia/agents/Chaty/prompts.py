# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from gaia.logger import get_logger


class Prompts:
    log = get_logger(__name__)

    # Define model-specific formatting templates
    prompt_formats = {
        "llama3": {
            "system": "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n{system_message}<|eot_id|>",
            "user": "<|start_header_id|>user<|end_header_id|>\n{content}<|eot_id|>",
            "assistant": "<|start_header_id|>assistant<|end_header_id|>\n{content}",
        },
        "mistral": {
            "system": "<s>[INST] {system_message}\n\n",
            "user": "{content}",
            "assistant": " [/INST] {content}</s>",
        },
        "qwen": {
            "system": "<|im_start|>system\n{system_message}<|im_end|>",
            "user": "<|im_start|>user\n{content}<|im_end|>",
            "assistant": "<|im_start|>assistant\n{content}<|im_end|>",
        },
        "phi3": {
            "system": "<|user|>{system_message}\n",
            "chat_entry": "<|{role}|>{content}<|end|>",
            "assistant_prefix": "<|assistant|>",
        },
        "llama2": {
            "system": "<s>[INST] <<SYS>>\n{system_message}\n<</SYS>>\n\n",
            "chat_entry": "{content}",  # Content will include [/INST] and </s><s>[INST] formatting
            "assistant_prefix": " [/INST] ",
        },
        "chatglm": {
            "system": "<|system|>\n{system_message}\n",
            "user": "<|user|>\n{content}\n",
            "assistant": "<|assistant|>\n{content}\n",
            "observation": "<|observation|>\n{content}\n",  # For external return results
        },
        "gemma": {
            "system": "<start_of_turn>system\n{system_message}<end_of_turn>\n",
            "user": "<start_of_turn>user\n{content}<end_of_turn>\n",
            "assistant": "<start_of_turn>assistant\n{content}<end_of_turn>\n",
        },
        "deepseek": {
            "system": "{system_message}\n",
            "user": "<|User|>{content}\n",
            "assistant": "<|Assistant|>{content}\n",
        },
        "default": {
            "system": "{system_message}\n",
            "user": "User: {content}\n",
            "assistant": "Assistant: {content}\n",
        },
        # Add other model formats here...
    }

    system_messages = {
        "llama3": "You are a helpful AI assistant. You provide clear, accurate, and technically-sound responses while maintaining a friendly demeanor.",
        "phi3": "You are a helpful AI assistant. You provide clear, accurate, and technically-sound responses while maintaining a friendly demeanor.",
        "chatglm": "You are ChatGLM3, a large language model trained by Zhipu.AI. Follow the user's instructions carefully. Respond using markdown.",
        "gemma": "You are Gemma, a helpful AI assistant. You provide clear, accurate, and technically-sound responses while maintaining a friendly demeanor.",
        "deepseek": "You are DeepSeek R1, a large language model trained by DeepSeek. You provide clear, accurate, and technically-sound responses while maintaining a friendly demeanor.",
        "default": "You are a helpful AI assistant. You provide clear, accurate, and technically-sound responses while maintaining a friendly demeanor.",
        # Add other system messages here...
    }

    @staticmethod
    def format_chat_history(model: str, chat_history: list) -> str:
        """Format the chat history according to the model's requirements."""
        matched_model = Prompts._match_model_name(model)
        format_template = Prompts.prompt_formats.get(matched_model)
        Prompts.log.info(
            f"model:{model}, matched_model: {matched_model}, format_template:\n{format_template}"
        )

        if not format_template:
            raise ValueError(f"No format template found for model {matched_model}")

        # Start with the system message
        system_msg = Prompts.system_messages.get(
            matched_model, "You are a helpful assistant."
        )
        formatted_prompt = format_template["system"].format(system_message=system_msg)

        if matched_model == "gemma":
            for entry in chat_history:
                if entry.startswith("user: "):
                    content = entry[6:]
                    formatted_prompt += format_template["user"].format(content=content)
                elif entry.startswith("assistant: "):
                    content = entry[11:]
                    formatted_prompt += format_template["assistant"].format(
                        content=content
                    )
                    formatted_prompt += (
                        "<end_of_turn>\n"  # Add end token after assistant responses
                    )

            # Add the assistant prefix if the last message was from user
            if chat_history and chat_history[-1].startswith("user: "):
                formatted_prompt += format_template["assistant"].format(content="")

            return formatted_prompt

        elif matched_model == "llama3":
            for i, entry in enumerate(chat_history):
                if entry.startswith("user: "):
                    content = entry[6:]
                    formatted_prompt += format_template["user"].format(content=content)
                elif entry.startswith("assistant: "):
                    content = entry[11:]
                    formatted_prompt += (
                        format_template["assistant"].format(content=content)
                        + "<|eot_id|>"
                    )

            if chat_history and chat_history[-1].startswith("user: "):
                formatted_prompt += format_template["assistant"].format(content="")

            return formatted_prompt

        elif matched_model == "mistral":
            for i, entry in enumerate(chat_history):
                if entry.startswith("user: "):
                    content = entry[6:]
                    if i > 0:  # Add new instruction block for all but first message
                        formatted_prompt += "<s>[INST] "
                    formatted_prompt += format_template["user"].format(content=content)
                elif entry.startswith("assistant: "):
                    content = entry[11:]
                    formatted_prompt += format_template["assistant"].format(
                        content=content
                    )

            # Add final [INST] block if last message was from user
            if chat_history and chat_history[-1].startswith("user: "):
                formatted_prompt += " [/INST]"

            return formatted_prompt

        elif matched_model == "qwen":
            for entry in chat_history:
                if entry.startswith("user: "):
                    content = entry[6:]
                    formatted_prompt += format_template["user"].format(content=content)
                elif entry.startswith("assistant: "):
                    content = entry[11:]
                    formatted_prompt += format_template["assistant"].format(
                        content=content
                    )

            # Add the final assistant token for the next response
            if chat_history and chat_history[-1].startswith("user: "):
                formatted_prompt += "<|im_start|>assistant\n"

            return formatted_prompt

        elif matched_model == "llama2":
            # Start with system message
            formatted_prompt = format_template["system"].format(
                system_message=system_msg
            )

            for i, entry in enumerate(chat_history):
                if entry.startswith("user: "):
                    content = entry[6:]
                    if i > 0:  # Not the first message
                        formatted_prompt += "</s><s>[INST] "
                    formatted_prompt += content
                elif entry.startswith("assistant: "):
                    content = entry[11:]
                    formatted_prompt += " [/INST] " + content

            # Add final [/INST] if last message was from user
            if chat_history and chat_history[-1].startswith("user: "):
                formatted_prompt += " [/INST]"

            return formatted_prompt

        elif matched_model == "chatglm":
            # Start with system message
            formatted_prompt = format_template["system"].format(
                system_message=system_msg
            )

            for entry in chat_history:
                if entry.startswith("user: "):
                    content = entry[6:]
                    formatted_prompt += format_template["user"].format(content=content)
                elif entry.startswith("assistant: "):
                    content = entry[11:]
                    formatted_prompt += format_template["assistant"].format(
                        content=content
                    )
                elif entry.startswith(
                    "observation: "
                ):  # Add support for observation messages
                    content = entry[12:]
                    formatted_prompt += format_template["observation"].format(
                        content=content
                    )

            # Add the assistant prefix if the last message was from user
            if chat_history and chat_history[-1].startswith("user: "):
                formatted_prompt += "<|assistant|>\n"

            return formatted_prompt

        # Standard handling for other models
        for entry in chat_history:
            if entry.startswith("user: "):
                role, content = "user", entry[6:]
            elif entry.startswith("assistant: "):
                role, content = "assistant", entry[11:]
            else:
                continue

            formatted_prompt += format_template["chat_entry"].format(
                role=role, content=content
            )

        # Add the assistant prefix for the next response
        formatted_prompt += format_template["assistant_prefix"]

        return formatted_prompt

    @staticmethod
    def _match_model_name(model: str) -> str:
        """Match a model path/name to its corresponding prompt type."""
        Prompts.log.debug(f"Matching model name: {model}")
        model = model.lower()

        if any(x in model for x in ["phi-3", "phi3"]):
            return "phi3"
        elif "gemma" in model:
            return "gemma"
        elif any(x in model for x in ["llama3", "llama-3"]):
            return "llama3"
        elif any(x in model for x in ["llama2", "llama-2"]):
            return "llama2"
        elif "mistral" in model:
            return "mistral"
        elif "qwen" in model:
            return "qwen"
        elif "chatglm" in model:
            return "chatglm"
        elif "deepseek" in model:
            return "deepseek"
        else:
            Prompts.log.warning(
                f"No specific format found for model {model}, using default format"
            )
            return "default"

    @classmethod
    def get_system_prompt(cls, model: str, chat_history: list[str]) -> str:
        """Get the formatted system prompt for the given model and chat history."""
        model_type = cls._match_model_name(model)
        format_template = cls.prompt_formats[model_type]
        system_message = cls.system_messages[model_type]

        # Format system message
        prompt = format_template["system"].format(system_message=system_message)

        # Format chat history
        for message in chat_history:
            if message.startswith("user: "):
                content = message[6:]  # Remove "user: " prefix
                prompt += format_template["user"].format(content=content)
            elif message.startswith("assistant: "):
                content = message[11:]  # Remove "assistant: " prefix
                prompt += format_template["assistant"].format(content=content)

        return prompt


def main():
    """Test different prompt formats with sample conversations."""
    # Sample conversation
    chat_history = [
        "user: Hello, how are you?",
        "assistant: I'm doing well, thank you! How can I help you today?",
        "user: What's the weather like?",
    ]

    # Test cases for different models
    test_models = [
        "amd/Phi-3-mini-4k-instruct-awq-g128-int4-asym-fp32-onnx-ryzen-strix",
        "amd/Llama-2-7b-hf-awq-g128-int4-asym-fp32-onnx-ryzen-strix",
        "meta-llama/Meta-Llama-3-8B",
        "amd/Mistral-7B-Instruct-v0.3-awq-g128-int4-asym-fp32-onnx-ryzen-strix",
    ]

    for model in test_models:
        print(f"\n{'='*80}")
        print(f"Testing model: {model}")
        formatted_prompt = Prompts.get_system_prompt(model, chat_history)
        print(f"Matched as: {formatted_prompt}")
        print(f"{'='*80}\n")

        try:
            formatted_prompt = Prompts.get_system_prompt(model, chat_history)
            print("Formatted prompt:")
            print("-" * 40)
            print(formatted_prompt)
            print("-" * 40)
        except ValueError as e:
            print(f"Error: {e}")


def test_llama2_format():
    """Specific test for Llama 2 format."""
    model = "meta-llama/Llama-2-7b-chat-hf"
    chat_history = [
        "user: What is Python?",
        "assistant: Python is a high-level programming language known for its simplicity and readability.",
        "user: Can you show me a simple example?",
    ]

    print("\nTesting Llama 2 Format:")
    print("=" * 60)
    formatted = Prompts.get_system_prompt(model, chat_history)
    print(formatted)


def test_llama3_format():
    """Specific test for Llama 3 format."""
    model = "meta-llama/Meta-Llama-3-8B"
    chat_history = [
        "user: Explain what an API is.",
        "assistant: An API (Application Programming Interface) is a set of rules and protocols that allows different software applications to communicate with each other.",
        "user: Give me an example.",
    ]

    print("\nTesting Llama 3 Format:")
    print("=" * 60)
    formatted = Prompts.get_system_prompt(model, chat_history)
    print(formatted)


def test_qwen_format():
    """Specific test for Qwen format."""
    model = "amd/Qwen1.5-7B-Chat-awq-g128-int4-asym-fp32-onnx-ryzen-strix"
    chat_history = [
        "user: What is Python?",
        "assistant: Python is a high-level programming language known for its simplicity and readability.",
        "user: Show me an example.",
    ]

    print("\nTesting Qwen Format:")
    print("=" * 60)
    formatted = Prompts.get_system_prompt(model, chat_history)
    print(formatted)


def test_chatglm_format():
    """Specific test for ChatGLM format."""
    model = "THUDM/chatglm3-6b"
    chat_history = [
        "user: What's the weather like?",
        "assistant: Let me check the weather for you.",
        "observation: Current temperature is 72°F, sunny with light clouds",
        "assistant: Based on the current data, it's a pleasant day with 72°F temperature and partly cloudy skies.",
        "user: Thank you!",
    ]

    print("\nTesting ChatGLM Format:")
    print("=" * 60)
    formatted = Prompts.get_system_prompt(model, chat_history)
    print(formatted)


if __name__ == "__main__":
    # Run all tests
    main()
    test_llama2_format()
    test_llama3_format()
    test_qwen_format()
    test_chatglm_format()
