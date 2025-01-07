# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import argparse
import torch

from gaia.interface.util import UIMessage
from gaia.interface.huggingface import set_huggingface_token

USE_TKML = True
if USE_TKML:
    import turnkeyml.llm.cache as cache  # pylint:disable=E0611
    from turnkeyml.llm.tools.chat import Serve  # pylint:disable=E0611
    from turnkeyml.llm.tools.huggingface_load import (  # pylint:disable=E0611
        HuggingfaceLoad,
    )
    from turnkeyml.llm.tools.ort_genai.oga import OgaLoad  # pylint:disable=E0611
    from turnkeyml.state import State  # pylint:disable=E0401
else:
    # TODO: temporary solution using internal lemonade package/repo.
    # https://github.com/aigdat/gaia/issues/109
    import lemonade.cache as cache  # pylint:disable=E0401
    from lemonade.tools.chat import Serve  # pylint:disable=E0401
    from lemonade.tools.huggingface_load import HuggingfaceLoad  # pylint:disable=E0401
    from lemonade.tools.ort_genai.oga import OgaLoad  # pylint:disable=E0401
    from turnkeyml.state import State  # pylint:disable=E0401,C0412


def launch_llm_server(
    backend, checkpoint, device, dtype, max_new_tokens, cli_mode=False
):
    if not (
        device == "cpu" or device == "npu" or device == "igpu" or device == "hybrid"
    ):
        raise ValueError(
            f"ERROR: {device} not supported, please select 'cpu', 'npu' or 'igpu'."
        )
    if not (backend == "ollama" or backend == "hf" or backend == "oga"):
        raise ValueError(
            f"ERROR: {backend} not supported, please select 'ollama', 'hf' or 'oga'."
        )

    if backend == "hf" or backend == "oga":  # use lemonade
        try:
            runtime = HuggingfaceLoad if backend == "hf" else OgaLoad
            dtype = torch.bfloat16 if dtype == "bfloat16" else dtype

            state = State(
                cache_dir=cache.DEFAULT_CACHE_DIR,
                build_name=f"{checkpoint}_{device}_{dtype}",
            )
            try:
                state = runtime().run(
                    state, input=checkpoint, device=device, dtype=dtype
                )

            except Exception as e:
                err = str(e)
                if "gated repo" in err or "token" in err:
                    # Get HuggingFace token through UI
                    token = set_huggingface_token()
                    if token:
                        # Retry with the new token
                        state = State(
                            cache_dir=cache.DEFAULT_CACHE_DIR,
                            build_name=f"{checkpoint}_{device}_{dtype}",
                        )
                        state = runtime().run(
                            state, input=checkpoint, device=device, dtype=dtype
                        )
                    else:
                        UIMessage.error(
                            "No Hugging Face token provided. Cannot access gated model.",
                            cli_mode=cli_mode,
                        )
                        return
                else:
                    UIMessage.error(err, cli_mode=cli_mode)
                    raise
            state = Serve().run(state, max_new_tokens=max_new_tokens)

        except FileNotFoundError as e:
            UIMessage.error(
                f"Error: Unable to find the model files for {checkpoint}.\n\n"
                "Make sure they are placed in the correct location, e.g. "
                "C:/Users/<user>/miniconda3/envs/<venv>/Lib/site-packages"
                f"/lemonade/tools/ort_genai/models/<model_folder>`\n\n{str(e)}",
                cli_mode=cli_mode,
            )
            return

        except Exception as e:
            UIMessage.error(
                f"An unexpected error occurred:\n\n{str(e)}", cli_mode=cli_mode
            )
            return
    return None


def get_cpu_args():
    parser = argparse.ArgumentParser(description="Launch LLM server")
    parser.add_argument(
        "--checkpoint",
        type=str,
        required=True,
        help="Checkpoint path",
    )
    parser.add_argument(
        "--backend", type=str, default="hf", help="Device type [cpu, npu, igpu]"
    )
    parser.add_argument(
        "--device", type=str, default="cpu", help="Device type [cpu, npu, igpu]"
    )
    parser.add_argument(
        "--dtype",
        type=str,
        default="bfloat16",
        help="Data type [float32, bfloat16, int4]",
    )
    parser.add_argument(
        "--max_new_tokens", type=int, default=100, help="Max new tokens to generate"
    )
    args = parser.parse_args()

    return args


def get_npu_args():
    parser = argparse.ArgumentParser(description="Launch LLM server")
    parser.add_argument(
        "--checkpoint",
        type=str,
        required=True,
        help="Checkpoint path",
    )
    parser.add_argument(
        "--backend", type=str, default="oga", help="Device type [cpu, npu, igpu]"
    )
    parser.add_argument(
        "--device", type=str, default="npu", help="Device type [cpu, npu, igpu]"
    )
    parser.add_argument(
        "--dtype", type=str, default="int4", help="Data type [float32, bfloat16, int4]"
    )
    parser.add_argument(
        "--max_new_tokens", type=int, default=100, help="Max new tokens to generate"
    )
    args = parser.parse_args()

    return args


if __name__ == "__main__":
    args = get_cpu_args()
    # args = get_npu_args()
    launch_llm_server(
        args.backend,
        args.checkpoint,
        args.device,
        args.dtype,
        args.max_new_tokens,
        cli_mode=True,
    )
