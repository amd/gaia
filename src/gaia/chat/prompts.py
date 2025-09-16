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
            "user": "<|user|>{content}<|end|>",
            "assistant": "<|assistant|>{content}<|end|>",
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
            "chat_entry": "{role}: {content}\n",
            "assistant_prefix": "Assistant: ",
        },
        # Add other model formats here...
    }

    system_messages = {
        "llama3": "You are a helpful AI assistant. You provide clear, accurate, and technically-sound responses while maintaining a friendly demeanor.",
        "phi3": "You are a helpful AI assistant. You provide clear, accurate, and technically-sound responses while maintaining a friendly demeanor.",
        "chatglm": "You are ChatGLM3, a large language model trained by Zhipu.AI. Follow the user's instructions carefully. Respond using markdown.",
        "gemma": "You are Gemma, a helpful AI assistant. You provide clear, accurate, and technically-sound responses while maintaining a friendly demeanor.",
        "deepseek": "You are DeepSeek R1, a large language model trained by DeepSeek. You provide clear, accurate, and technically-sound responses while maintaining a friendly demeanor.",
        "qwen": "You are Qwen, a helpful AI assistant. You provide clear, accurate, and technically-sound responses while maintaining a friendly demeanor.",
        "default": "You are a helpful AI assistant. You provide clear, accurate, and technically-sound responses while maintaining a friendly demeanor.",
        # Add other system messages here...
    }

    @staticmethod
    def format_chat_history(
        model: str,
        chat_history: list,
        assistant_name: str = "assistant",
        system_prompt: str = None,
    ) -> str:
        """Format the chat history according to the model's requirements."""
        matched_model = Prompts.match_model_name(model)
        format_template = Prompts.prompt_formats.get(matched_model)
        Prompts.log.debug(
            f"model:{model}, matched_model: {matched_model}, format_template:\n{format_template}"
        )

        if not format_template:
            raise ValueError(f"No format template found for model {matched_model}")

        # Start with the system message - use custom system_prompt if provided, otherwise use default with assistant_name
        if system_prompt:
            system_msg = system_prompt
        else:
            base_msg = Prompts.system_messages.get(
                matched_model, "You are a helpful assistant."
            )
            # Incorporate assistant_name into the default system message if it's not "assistant"
            if assistant_name != "assistant":
                system_msg = base_msg.replace(
                    "helpful AI assistant",
                    f"helpful AI assistant named {assistant_name}",
                )
                system_msg = system_msg.replace(
                    "You are a helpful assistant",
                    f"You are {assistant_name}, a helpful assistant",
                )
                # Handle specific model names
                if matched_model == "chatglm":
                    system_msg = system_msg.replace(
                        "You are ChatGLM3", f"You are {assistant_name} (ChatGLM3)"
                    )
                elif matched_model == "gemma":
                    system_msg = system_msg.replace(
                        "You are Gemma", f"You are {assistant_name} (Gemma)"
                    )
                elif matched_model == "deepseek":
                    system_msg = system_msg.replace(
                        "You are DeepSeek R1", f"You are {assistant_name} (DeepSeek R1)"
                    )
                elif matched_model == "qwen":
                    system_msg = system_msg.replace(
                        "You are Qwen", f"You are {assistant_name} (Qwen)"
                    )
            else:
                system_msg = base_msg

        formatted_prompt = format_template["system"].format(system_message=system_msg)

        # Create dynamic prefixes
        user_prefix = "user: "
        assistant_prefix = f"{assistant_name}: "

        if matched_model == "gemma":
            for entry in chat_history:
                if entry.startswith(user_prefix):
                    content = entry[len(user_prefix) :]
                    formatted_prompt += format_template["user"].format(content=content)
                elif entry.startswith(assistant_prefix):
                    content = entry[len(assistant_prefix) :]
                    formatted_prompt += format_template["assistant"].format(
                        content=content
                    )
                    formatted_prompt += (
                        "<end_of_turn>\n"  # Add end token after assistant responses
                    )

            # Add the assistant prefix if the last message was from user
            if chat_history and chat_history[-1].startswith(user_prefix):
                formatted_prompt += format_template["assistant"].format(content="")

            return formatted_prompt

        elif matched_model == "llama3":
            for i, entry in enumerate(chat_history):
                if entry.startswith(user_prefix):
                    content = entry[len(user_prefix) :]
                    formatted_prompt += format_template["user"].format(content=content)
                elif entry.startswith(assistant_prefix):
                    content = entry[len(assistant_prefix) :]
                    formatted_prompt += (
                        format_template["assistant"].format(content=content)
                        + "<|eot_id|>"
                    )

            if chat_history and chat_history[-1].startswith(user_prefix):
                formatted_prompt += format_template["assistant"].format(content="")

            return formatted_prompt

        elif matched_model == "mistral":
            for i, entry in enumerate(chat_history):
                if entry.startswith(user_prefix):
                    content = entry[len(user_prefix) :]
                    if i > 0:  # Add new instruction block for all but first message
                        formatted_prompt += "<s>[INST] "
                    formatted_prompt += format_template["user"].format(content=content)
                elif entry.startswith(assistant_prefix):
                    content = entry[len(assistant_prefix) :]
                    formatted_prompt += format_template["assistant"].format(
                        content=content
                    )

            # Add final [INST] block if last message was from user
            if chat_history and chat_history[-1].startswith(user_prefix):
                formatted_prompt += " [/INST]"

            return formatted_prompt

        elif matched_model == "qwen":
            for entry in chat_history:
                if entry.startswith(user_prefix):
                    content = entry[len(user_prefix) :]
                    formatted_prompt += format_template["user"].format(content=content)
                elif entry.startswith(assistant_prefix):
                    content = entry[len(assistant_prefix) :]
                    formatted_prompt += format_template["assistant"].format(
                        content=content
                    )

            # Add the final assistant token for the next response
            if chat_history and chat_history[-1].startswith(user_prefix):
                formatted_prompt += "<|im_start|>assistant\n"

            return formatted_prompt

        elif matched_model == "llama2":
            # Start with system message
            formatted_prompt = format_template["system"].format(
                system_message=system_msg
            )

            for i, entry in enumerate(chat_history):
                if entry.startswith(user_prefix):
                    content = entry[len(user_prefix) :]
                    if i > 0:  # Not the first message
                        formatted_prompt += "</s><s>[INST] "
                    formatted_prompt += content
                elif entry.startswith(assistant_prefix):
                    content = entry[len(assistant_prefix) :]
                    formatted_prompt += " [/INST] " + content

            # Add final [/INST] if last message was from user
            if chat_history and chat_history[-1].startswith(user_prefix):
                formatted_prompt += " [/INST]"

            return formatted_prompt

        elif matched_model == "chatglm":
            # Start with system message
            formatted_prompt = format_template["system"].format(
                system_message=system_msg
            )

            for entry in chat_history:
                if entry.startswith(user_prefix):
                    content = entry[len(user_prefix) :]
                    formatted_prompt += format_template["user"].format(content=content)
                elif entry.startswith(assistant_prefix):
                    content = entry[len(assistant_prefix) :]
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
            if chat_history and chat_history[-1].startswith(user_prefix):
                formatted_prompt += "<|assistant|>\n"

            return formatted_prompt

        # Standard handling for other models
        for entry in chat_history:
            if entry.startswith(user_prefix):
                role, content = "user", entry[len(user_prefix) :]
            elif entry.startswith(assistant_prefix):
                role, content = "assistant", entry[len(assistant_prefix) :]
            else:
                continue

            # Use the role-specific format template for all models
            formatted_prompt += format_template[role].format(content=content)

        # Add the assistant prefix for the next response if it exists
        if (
            "assistant_prefix" in format_template
            and chat_history
            and chat_history[-1].startswith(user_prefix)
        ):
            formatted_prompt += format_template["assistant_prefix"]
        # If no assistant_prefix but we need to add assistant marker
        elif chat_history and chat_history[-1].startswith(user_prefix):
            if "assistant" in format_template:
                formatted_prompt += format_template["assistant"].format(content="")

        return formatted_prompt

    @staticmethod
    def match_model_name(model: str) -> str:
        """Match a model path/name to its corresponding prompt type."""
        Prompts.log.debug(f"Matching model name: {model}")
        model = model.lower()

        if any(x in model for x in ["phi-3", "phi3"]):
            return "phi3"
        elif "gemma" in model:
            return "gemma"
        elif any(x in model for x in ["llama3", "llama-3", "llama3.2", "llama-3.2"]):
            return "llama3"
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
    def get_system_prompt(
        cls,
        model: str,
        chat_history: list[str],
        assistant_name: str = "assistant",
        system_prompt: str = None,
    ) -> str:
        """Get the formatted system prompt for the given model and chat history."""
        return cls.format_chat_history(
            model, chat_history, assistant_name, system_prompt
        )


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


def test_llama32_format():
    """Specific test for Llama 3.2 format."""
    model = "Llama-3.2-3B-Instruct-Hybrid"
    chat_history = [
        "user: Hello, how are you?",
        "assistant: I'm doing well, thank you! How can I help you today?",
        "user: What's the weather like?",
    ]

    print("\nTesting Llama 3.2 Format:")
    print("=" * 60)
    matched_model = Prompts.match_model_name(model)
    print(f"Model: {model}")
    print(f"Matched as: {matched_model}")
    print("-" * 60)
    formatted = Prompts.get_system_prompt(model, chat_history)
    print("Formatted prompt:")
    print(formatted)


if __name__ == "__main__":
    # Run all tests
    main()
    test_llama2_format()
    test_llama3_format()
    test_qwen_format()
    test_chatglm_format()
    test_llama32_format()
