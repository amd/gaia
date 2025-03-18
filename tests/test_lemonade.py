# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import pytest
import subprocess
from dotenv import load_dotenv
from gaia.logger import get_logger

log = get_logger(__name__)

load_dotenv()
os.environ["HF_TOKEN"] = os.getenv("HUGGINGFACE_ACCESS_TOKEN")
print(os.environ["HF_TOKEN"])

ENABLE_SMOKE_TESTS = True
if ENABLE_SMOKE_TESTS:
    MODELS_BACKEND = [
        (
            "meta-llama/Meta-Llama-3.1-8B",
            "huggingface-load --device cpu --dtype bfloat16",
        ),
        (
            "amd/Meta-Llama-3-8B-Instruct-int4-oga-npu",
            "oga-load --device npu --dtype int4",
        ),
    ]
    MMLU_TESTS = "management"
    # 128, 256 tokens
    PROMPT_SIZES = [128, 256]
    PROMPTS = [
        "./data/prompts/prompt.128.txt",
        "./data/prompts/prompt.256.txt",
    ]
else:
    MODELS_BACKEND = [
        ("meta-llama/Llama-3.2-1B", "huggingface-load --device cpu --dtype bfloat16"),
        (
            "meta-llama/Llama-3.2-1B-Instruct",
            "huggingface-load --device cpu --dtype bfloat16",
        ),
        ("meta-llama/Llama-3.2-3B", "huggingface-load --device cpu --dtype bfloat16"),
        (
            "meta-llama/Llama-3.2-3B-Instruct",
            "huggingface-load --device cpu --dtype bfloat16",
        ),
        (
            "meta-llama/Meta-Llama-3.1-8B",
            "huggingface-load --device cpu --dtype bfloat16",
        ),
        (
            "amd/Llama-3.1-8B-awq-g128-int4-asym-fp32-onnx-ryzen-strix",
            "oga-load --device npu --dtype int4",
        ),
        (
            "meta-llama/Llama-3.1-8B-Instruct",
            "huggingface-load --device cpu --dtype bfloat16",
        ),
        (
            "amd/Meta-Llama-3-8B-Instruct-int4-oga-npu",
            "oga-load --device npu --dtype int4",
        ),
        ("Qwen/Qwen1.5-7B-Chat", "huggingface-load --device cpu --dtype bfloat16"),
        (
            "amd/Qwen1.5-7B-Chat-awq-g128-int4-asym-fp32-onnx-ryzen-strix",
            "oga-load --device npu --dtype int4",
        ),
        (
            "microsoft/Phi-3.5-mini-instruct",
            "huggingface-load --device cpu --dtype bfloat16",
        ),
        (
            "amd/Phi-3.5-mini-instruct-awq-g128-int4-asym-fp32-onnx-ryzen-strix",
            "oga-load --device npu --dtype int4",
        ),
        (
            "microsoft/Phi-3-mini-4k-instruct",
            "huggingface-load --device cpu --dtype bfloat16",
        ),
        (
            "amd/Phi-3-mini-4k-instruct-awq-g128-int4-asym-fp32-onnx-ryzen-strix",
            "oga-load --device npu --dtype int4",
        ),
        ("meta-llama/Llama-2-7b-hf", "huggingface-load --device cpu --dtype bfloat16"),
        (
            "amd/Llama-2-7b-hf-awq-g128-int4-asym-fp32-onnx-ryzen-strix",
            "oga-load --device npu --dtype int4",
        ),
        # ("meta-llama/Llama-2-7b-chat", "huggingface-load --device cpu --dtype bfloat16"),
        (
            "amd/Llama2-7b-chat-awq-g128-int4-asym-fp32-onnx-ryzen-strix",
            "oga-load --device npu --dtype int4",
        ),
        (
            "mistralai/Mistral-7B-Instruct-v0.3",
            "huggingface-load --device cpu --dtype bfloat16",
        ),
        (
            "amd/Mistral-7B-Instruct-v0.3-awq-g128-int4-asym-fp32-onnx-ryzen-strix",
            "oga-load --device npu --dtype int4",
        ),
    ]
    MMLU_TESTS = "management philosophy anatomy mathematics"
    # 128, 256, 512, 1024, 2048 tokens
    PROMPT_SIZES = [128, 256, 512, 1024, 2048]
    PROMPTS = [
        "./data/prompts/prompt.128.txt",
        "./data/prompts/prompt.256.txt",
        "./data/prompts/prompt.512.txt",
        "./data/prompts/prompt.1024.txt",
        "./data/prompts/prompt.2048.txt",
    ]


def handle_subprocess_error(result, model):
    if result.returncode != 0:
        error_message = result.stderr or result.stdout or ""
        if isinstance(error_message, bytes):
            error_message = error_message.decode("utf-8", errors="replace")

        if "GatedRepoError" in error_message:
            skip_reason = f"Access to model {model} is restricted. Please request access from Hugging Face."
            log.warning(f"\nSkipping test: {skip_reason}")
            pytest.skip(skip_reason)
        else:
            fail_reason = f"Command failed with return code {result.returncode}. Error: {error_message}"
            log.error(f"\nFailing test: {fail_reason}")
            pytest.fail(fail_reason)


@pytest.mark.parametrize("model, backend", MODELS_BACKEND)
def test_mmlu_accuracy(model, backend):
    cmd = f"lemonade -i {model} {backend} accuracy-mmlu --tests {MMLU_TESTS}"
    log.info(f"\nCommand executed:\n{cmd}\n")

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    handle_subprocess_error(result, model)
    log.info(result.stdout)


@pytest.mark.parametrize("model, backend", MODELS_BACKEND)
def test_perplexity_accuracy(model, backend):
    cmd = f"lemonade -i {model} {backend} accuracy-perplexity"
    log.info(f"\nCommand executed:\n{cmd}\n")

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    handle_subprocess_error(result, model)
    log.info(result.stdout)


@pytest.mark.parametrize(
    "model, backend, prompt",
    [
        (model, backend, prompt)
        for model, backend in MODELS_BACKEND
        for prompt in PROMPT_SIZES
    ],
)
def test_llm_prompt_bench(model, backend, prompt):
    if "huggingface" in backend:
        tool = "huggingface-bench"
    else:
        tool = "oga-bench"

    cmd = f"lemonade -i {model} {backend} {tool} -p {prompt}"
    log.info(f"\nCommand executed:\n{cmd}\n")

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    handle_subprocess_error(result, model)
    log.info(result.stdout)


@pytest.mark.parametrize(
    "model, backend, prompt_path",
    [
        (model, backend, prompt_path)
        for model, backend in MODELS_BACKEND
        for prompt_path in PROMPTS
    ],
)
def test_llm_prompt(model, backend, prompt_path):
    with open(prompt_path, "r") as f:
        prompt = f.read()
    cmd = f'lemonade -i {model} {backend} llm-prompt --prompt "{prompt}"'
    log.info(f"\nCommand executed:\n{cmd}\n")

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    handle_subprocess_error(result, model)
    log.info(result.stdout)


if __name__ == "__main__":
    # Add freeze_support to handle Windows multiprocessing
    from multiprocessing import freeze_support

    freeze_support()

    pytest.main(
        [
            __file__,
            "-v",  # for verbose output
            "-s",  # to show print statements
            # "-k test_mmlu_accuracy"
            # "-k test_perplexity_accuracy"
            # "-k test_llm_prompt_bench"
            "-k test_llm_prompt[",
        ]
    )
