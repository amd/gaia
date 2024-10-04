# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

import argparse
import torch
# import turnkeyml.llm.cache as cache
# from turnkeyml.llm.tools.chat import Serve
# from turnkeyml.llm.tools.huggingface_load import HuggingfaceLoad
# from turnkeyml.llm.tools.ort_genai.oga import OgaLoad
# # from turnkeyml.llm.tools.ryzenai_npu.ryzenai_npu import RyzenAINPULoad
# from turnkeyml.state import State

# TODO: temporary solution using internal lemonade package/repo.
# https://github.com/aigdat/gaia/issues/109
import lemonade.cache as cache # pylint:disable=E0401
from lemonade.tools.chat import Serve # pylint:disable=E0401
from lemonade.tools.huggingface_load import HuggingfaceLoad # pylint:disable=E0401
from lemonade.tools.ort_genai.oga import OgaLoad # pylint:disable=E0401
from turnkeyml.state import State # pylint:disable=E0401

from gaia.llm.ollama_serve import OllamaServe

from gaia.interface.util import UIMessage

def launch_llm_server(backend, checkpoint, device, dtype, max_new_tokens):
    assert(device == "cpu" or device == "npu" or device == "igpu"), f"ERROR: {device} not supported, please select 'cpu' or 'npu'."
    assert(backend == "ollama" or backend == "groq" or backend == "hf" or backend == "oga"), f"ERROR: {backend} not supported, please select 'ollama','groq', 'hf' or 'oga'."

    if backend == "hf" or backend == "oga": # use lemonade
        try:
            runtime = HuggingfaceLoad if backend == "hf" else OgaLoad
            dtype = torch.bfloat16 if dtype == "bfloat16" else dtype

            state = State(cache_dir=cache.DEFAULT_CACHE_DIR, build_name=f"{checkpoint}_{device}_{dtype}")
            state = runtime().run(
                state,
                input=checkpoint,
                device=device,
                dtype=dtype
            )
            state = Serve().run(state, max_new_tokens=max_new_tokens)
        except FileNotFoundError as e:
            UIMessage.error(f"Error: Unable to find the model files for {checkpoint}.\n\nMake sure they are placed in the correct location, e.g. C:/Users/<user>/miniconda3/envs/<venv>/Lib/site-packages/lemonade/tools/ort_genai/models/<model_folder>`\n\n{str(e)}")
            return
        except Exception as e: #pylint:disable=W0718
            UIMessage.error(f"An unexpected error occurred:\n\n{str(e)}")
            return

    if backend == "ollama":
        OllamaServe().run(model=checkpoint)

def get_cpu_args():
    parser = argparse.ArgumentParser(description="Launch LLM server")
    parser.add_argument(
        "--checkpoint",
        type=str,
        default="meta-llama/Meta-Llama-3-8B",
        help="Checkpoint path",
    )
    parser.add_argument("--backend", type=str, default="hf", help="Device type [cpu, npu, igpu]")
    parser.add_argument("--device", type=str, default="cpu", help="Device type [cpu, npu, igpu]")
    parser.add_argument("--dtype", type=str, default="bfloat16", help="Data type [float32, bfloat16, int4]")
    parser.add_argument("--max_new_tokens", type=int, default=100, help="Max new tokens to generate")
    args = parser.parse_args()

    return args

def get_npu_args():
    parser = argparse.ArgumentParser(description="Launch LLM server")
    parser.add_argument(
        "--checkpoint",
        type=str,
        default="meta-llama/Meta-Llama-3-8B",
        help="Checkpoint path",
    )
    parser.add_argument("--backend", type=str, default="oga", help="Device type [cpu, npu, igpu]")
    parser.add_argument("--device", type=str, default="npu", help="Device type [cpu, npu, igpu]")
    parser.add_argument("--dtype", type=str, default="int4", help="Data type [float32, bfloat16, int4]")
    parser.add_argument("--max_new_tokens", type=int, default=100, help="Max new tokens to generate")
    args = parser.parse_args()

    return args

if __name__ == "__main__":
    args = get_cpu_args()
    # args = get_npu_args()
    launch_llm_server(args.backend, args.checkpoint, args.device, args.dtype, args.max_new_tokens)
