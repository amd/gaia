import argparse
import lemonade
from lemonade.tools.general import Serve
from lemonade.tools.ort_genai.dml_og import DmlOgLoad


def launch_llm_server(checkpoint, device, dtype, max_new_tokens):
    state = lemonade.initialize_state(
        eval_id="gaiaexe_server",
    )
    state = DmlOgLoad().run(
        state,
        checkpoint=checkpoint,
        device=device,
        dtype=dtype,
    )
    state = Serve().run(state, max_new_tokens=max_new_tokens)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Launch LLM server")
    parser.add_argument(
        "--checkpoint",
        type=str,
        default="microsoft/Phi-3-mini-4k-instruct",
        help="Checkpoint path",
    )
    parser.add_argument("--device", type=str, default="igpu", help="Device type")
    parser.add_argument("--dtype", type=str, default="int4", help="Data type")
    parser.add_argument("--max_new_tokens", type=int, default=60, help="Data type")
    args = parser.parse_args()
    launch_llm_server(args.checkpoint, args.device, args.dtype, args.max_new_tokens)
