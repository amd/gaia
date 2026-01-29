"""
SDXL-Turbo test script using HuggingFace diffusers.

Compare this output with Lemonade Server output to verify quality.

Requirements:
    pip install diffusers transformers accelerate torch

Usage:
    python test_sdxl_turbo_diffusers.py
"""

import torch
from diffusers import AutoPipelineForText2Image

# Check device
if torch.cuda.is_available():
    device = "cuda"
    dtype = torch.float16
    print(f"Using CUDA: {torch.cuda.get_device_name()}")
elif hasattr(torch, "hip") and torch.hip.is_available():
    device = "cuda"  # ROCm uses cuda API
    dtype = torch.float16
    print("Using AMD ROCm")
else:
    device = "cpu"
    dtype = torch.float32
    print("Using CPU (will be slow)")

print()
print("Loading SDXL-Turbo model...")
pipeline = AutoPipelineForText2Image.from_pretrained(
    "stabilityai/sdxl-turbo",
    torch_dtype=dtype,
    variant="fp16" if dtype == torch.float16 else None,
)
pipeline = pipeline.to(device)

# Test prompts - same ones we used with Lemonade
prompts = [
    "a beautiful mountain, dramatic lighting, detailed",
    "a red circle on white background, simple, minimal",
]

print()
print("=" * 60)
print("SDXL-Turbo via diffusers (reference quality)")
print("=" * 60)

for i, prompt in enumerate(prompts):
    print(f"\nGenerating: {prompt}")

    # SDXL-Turbo settings from HuggingFace docs:
    # - guidance_scale=0.0 (trained without CFG)
    # - num_inference_steps=1-4 (1 is enough, more = better)
    # - 512x512 gives best results
    image = pipeline(
        prompt=prompt,
        guidance_scale=0.0,  # SDXL-Turbo was trained without CFG
        num_inference_steps=4,  # 1-4 steps, 4 for best quality
        height=512,
        width=512,
    ).images[0]

    filename = f"diffusers_sdxl_turbo_{i+1}.png"
    image.save(filename)
    print(f"Saved: {filename}")

print()
print("=" * 60)
print("Done! Compare these images with Lemonade output.")
print("=" * 60)
