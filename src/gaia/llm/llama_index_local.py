# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

from typing import Any, Callable

from llama_index.core.llms import (
    LLMMetadata,
    CustomLLM,
)
from llama_index.core.llms.callbacks import llm_completion_callback

from llama_index.llms.llama_cpp.llama_utils import (
    messages_to_prompt,
)
from llama_index.core.base.llms.types import (
    ChatMessage,
    ChatResponse,
    CompletionResponse,
    CompletionResponseGen,
    MessageRole,
)


class LocalLLM(CustomLLM):
    prompt_llm_server: Callable = None
    stream_to_ui: Callable = None
    context_window: int = 3900
    num_output: int = 256
    model_name: str = "custom"

    async def achat(
        self, # pylint: disable=W0613
        messages: Any,
        **kwargs: Any,
    ) -> str:

        formatted_message = messages_to_prompt(messages)

        # Prompt LLM and steam content to UI
        # TODO FIXME: make prompt_llm_server async
        text_response = await self.prompt_llm_server(
            prompt=formatted_message, stream_to_ui=True
        )

        response = ChatResponse(
            message=ChatMessage(
                role=MessageRole.ASSISTANT,
                content=text_response,
                additional_kwargs={},
            ),
            raw={"text": text_response},
        )
        return response

    @property
    def metadata(self) -> LLMMetadata:
        """Get LLM metadata."""
        return LLMMetadata(
            context_window=self.context_window,
            num_output=self.num_output,
            model_name=self.model_name,
        )

    @llm_completion_callback()
    def complete(self, prompt: str, **kwargs: Any) -> CompletionResponse: # pylint: disable=W0613
        response = self.prompt_llm_server(prompt=prompt)
        self.stream_to_ui(response, new_card=True)
        return CompletionResponse(text=response)

    @llm_completion_callback()
    def stream_complete(self, prompt: str, **kwargs: Any) -> CompletionResponseGen: # pylint: disable=W0613
        response = ""
        new_card = True
        for chunk in self.prompt_llm_server(prompt=prompt):

            # Stream chunk to UI
            self.stream_to_ui(chunk, new_card=new_card)
            new_card = False

            response += chunk
            yield CompletionResponse(text=response, delta=chunk)
