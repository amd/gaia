# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

import argparse
import torch
import turnkeyml.llm.cache as cache
from turnkeyml.llm.tools.chat import Serve
from turnkeyml.llm.tools.huggingface_load import HuggingfaceLoad
from turnkeyml.llm.tools.ort_genai.oga import OgaLoad
# from turnkeyml.llm.tools.ryzenai_npu.ryzenai_npu import RyzenAINPULoad
from turnkeyml.state import State


def launch_llm_server(backend, checkpoint, device, dtype, max_new_tokens):
    assert(device == "cpu" or device == "npu" or device == "igpu"), f"ERROR: {device} not supported, please select 'cpu' or 'npu'."
    assert(backend == "groq" or backend == "hf" or backend == "oga"), f"ERROR: {backend} not supported, please select 'groq', 'hf' or 'oga'."

    if backend == "hf" or backend == "oga": # use lemonade
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

    # TODO: Add support for RyzenAINPULoad (Pytorch NPU flow)
    # if RyzenAINPULoad is not None:
    #     state = lemonade.initialize_state( # pylint:disable=E1101
    #         eval_id="gaiaexe_server",
    #     )
    #     state = RyzenAINPULoad().run( # pylint:disable=E1123
    #         state,
    #         checkpoint=checkpoint,
    #         device='phx', # FIXME: handle properly, set to 'stx' to enable fast attention.
    #     )
    #     state = Serve().run(state, max_new_tokens=max_new_tokens)
    # else:
    #     print("ERROR: RyzenAINPULoad package not found, llm server is not running.")

def get_cpu_args():
    parser = argparse.ArgumentParser(description="Launch LLM server")
    parser.add_argument(
        "--checkpoint",
        type=str,
        # default="meta-llama/Meta-Llama-3-8B",
        default="",
        help="Checkpoint path",
    )
    parser.add_argument("--backend", type=str, default="hf", help="Device type [cpu, npu, igpu]")
    parser.add_argument("--device", type=str, default="cpu", help="Device type [cpu, npu, igpu]")
    parser.add_argument("--dtype", type=str, default=torch.bfloat16, help="Data type [float32, bfloat16, int4]")
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
