import os
import re
import pytest
import subprocess
from datetime import datetime


PROMPT = """
<|begin_of_text|><|start_header_id|>system<|end_header_id|>
You are a pirate chatbot who always responds in pirate speak!<|eot_id|>
<|start_header_id|>user<|end_header_id|>
user: Hi, who are you in one sentence?<|eot_id|>
<|start_header_id|>assistant<|end_header_id|>
assistant:
"""
PROMPT = "Hello, how are you?"

@pytest.mark.parametrize("model, backend",[
    ("meta-llama/Meta-Llama-3.1-8B",     "huggingface-load --device cpu --dtype bfloat16"),
    ("meta-llama/Llama-3.1-8B-Instruct", "huggingface-load --device cpu --dtype bfloat16"),
    ("amd/Llama-3.1-8B-awq-g128-int4-asym-fp32-onnx-ryzen-strix", "oga-load --device npu --dtype int4"),
    ("amd/Llama-3.1-8B-awq-g128-int4-asym-fp32-onnx-ryzen-strix", "oga-load --device npu --dtype int4"),
])
def test_mmlu_accuracy(model, backend):
    mmlu_test = "management"
    cmd = f"lemonade -i {model} {backend} accuracy-mmlu --tests {mmlu_test}"
    print(cmd)

    result = subprocess.run(cmd, shell=True, capture_output=False, text=True)
    
    # Check if the command was successful
    assert result.returncode == 0, f"Command failed: {result.stderr}"

@pytest.mark.parametrize("model, backend, tool",[
    # ("meta-llama/Meta-Llama-3.1-8B",     "huggingface-load --device cpu --dtype bfloat16", "huggingface-bench"),
    ("meta-llama/Llama-3.1-8B-Instruct", "huggingface-load --device cpu --dtype bfloat16", "huggingface-bench"),
    # ("amd/Llama-3.1-8B-awq-g128-int4-asym-fp32-onnx-ryzen-strix", "oga-load --device npu --dtype int4", "oga-bench"),
    # ("amd/Llama-3.1-8B-awq-g128-int4-asym-fp32-onnx-ryzen-strix", "oga-load --device npu --dtype int4", "oga-bench"),
])
def test_llm_chat(model, backend, tool):
    cmd = f"lemonade -i {model} {backend} {tool} --prompt \"{PROMPT}\""
    print(f"\nCommand executed:\n{cmd}\n")

    result = subprocess.run(cmd, shell=True, capture_output=False, text=True)

    # Check if the command was successful
    assert result.returncode == 0, f"Command failed: {result.stderr}"

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",  # for verbose output
        "-s",  # to show print statements
        "-k test_llm_chat"
    ])
