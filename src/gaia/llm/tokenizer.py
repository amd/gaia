# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os

from transformers import AutoTokenizer
from huggingface_hub import HfFolder, HfApi

from gaia.interface.util import UIMessage


class Tokenizer:
    def __init__(self, model, cli_mode=False):
        self.model = model
        self.cli_mode = cli_mode
        self.tokenizer = self._initialize_tokenizer()

    def _initialize_tokenizer(self):
        try:
            # Check if the user is logged in to Hugging Face
            token = HfFolder.get_token()
            if not token:
                token = os.getenv("HUGGINGFACE_ACCESS_TOKEN")

            if not token:
                UIMessage.error(
                    "No Hugging Face token found. Please log in to Hugging Face.",
                    cli_mode=self.cli_mode,
                )

            # Verify the token
            api = HfApi(token=token)
            try:
                api.whoami(token)
            except Exception:
                UIMessage.error(
                    "Invalid Hugging Face token. Please provide a valid token.",
                    cli_mode=self.cli_mode,
                )

            # Set the HF_TOKEN environment variable
            os.environ["HF_TOKEN"] = token

            # Attempt to load the tokenizer
            return AutoTokenizer.from_pretrained(self.model)
        except EnvironmentError as e:
            UIMessage.error(str(e), cli_mode=self.cli_mode)
            from gaia.interface.huggingface import get_huggingface_token

            token = get_huggingface_token()
            if token:
                # Try to initialize the tokenizer again after getting the token
                return self._initialize_tokenizer()
            else:
                UIMessage.error(
                    "No token provided. Tokenizer initialization failed.",
                    cli_mode=self.cli_mode,
                )
                return None
        except Exception as e:
            UIMessage.error(
                f"An unexpected error occurred: {str(e)}", cli_mode=self.cli_mode
            )
            return None

    def chunk_text(self, text, max_chunk_size=2048):
        if self.tokenizer is None:
            UIMessage.error("Tokenizer is not initialized.", cli_mode=self.cli_mode)
            return []

        chunks = []
        encoded = self.tokenizer.encode(text)

        chunk_sizes = [128, 256, 512, 1024, 2048]
        chunk_sizes = [size for size in chunk_sizes if size <= max_chunk_size]

        for size in chunk_sizes:
            current_chunk = []
            current_size = 0
            size_chunks = []

            for token in encoded:
                current_chunk.append(token)
                current_size += 1

                if current_size == size:
                    size_chunks.append(self.tokenizer.decode(current_chunk))
                    current_chunk = []
                    current_size = 0

            if current_chunk:
                size_chunks.append(self.tokenizer.decode(current_chunk))

            chunks.append(size_chunks)

        return chunks


def test_tokenizer(tokenizer, test_text):
    if tokenizer.tokenizer is None:
        print("Tokenizer initialization failed.")
        return

    try:
        encoded = tokenizer.tokenizer.encode(test_text)
        decoded = tokenizer.tokenizer.decode(encoded)

        print(f"Original text: {test_text}")
        print(f"Encoded: {encoded}")
        print(f"Decoded: {decoded}")
        print(f"Token count: {len(encoded)}")

        # Remove leading and trailing whitespace and compare
        if test_text.strip() == decoded.strip():
            print(
                "Test passed: Original text matches decoded text (ignoring leading/trailing whitespace)."
            )
        else:
            print("Test failed: Original text does not match decoded text.")
            print("Differences:")
            print(f"  Original: '{test_text}'")
            print(f"  Decoded:  '{decoded}'")

        # Check for special tokens
        special_tokens = tokenizer.tokenizer.special_tokens_map
        print("\nSpecial tokens:")
        for token_name, token_value in special_tokens.items():
            if token_value in decoded:
                print(f"  {token_name}: '{token_value}' found in decoded text")

    except Exception as e:
        print(f"Error during encoding/decoding: {e}")


def test_chunk_text(tokenizer, test_text):
    if tokenizer.tokenizer is None:
        print("Tokenizer initialization failed. Skipping chunk_text test.")
        return

    print("\nTesting chunk_text method:")
    chunked_text = tokenizer.chunk_text(test_text)
    print(f"Number of chunks: {len(chunked_text)}")

    for i, size_chunks in enumerate(chunked_text):
        chunk_size = 128 * (2**i)
        print(f"\nChunks of size {chunk_size}:")
        print(f"  Number of chunks: {len(size_chunks)}")
        print(f"  Chunks: {size_chunks}")

        # Verify chunk sizes
        for j, chunk in enumerate(size_chunks):
            chunk_tokens = tokenizer.tokenizer.encode(chunk)
            if len(chunk_tokens) > chunk_size:
                print(
                    f"  Warning: Chunk {j+1} exceeds the expected size. "
                    f"Expected <= {chunk_size}, got {len(chunk_tokens)}"
                )

        # Verify that concatenated chunks match the original text
        reconstructed_text = "".join(size_chunks)
        if reconstructed_text == test_text:
            print("  Success: Reconstructed text matches the original.")
        else:
            print("  Error: Reconstructed text doesn't match the original.")
            print(f"  Original length: {len(test_text)}")
            print(f"  Reconstructed length: {len(reconstructed_text)}")


if __name__ == "__main__":
    # Test the tokenizer with a sample model
    test_model = "microsoft/Phi-3-mini-4k-instruct"
    tokenizer = Tokenizer(test_model)

    # Test encoding and decoding
    with open("./data/shakespeare.txt", "r", encoding="utf-8") as file:
        test_text = file.read(5000)

    # test_tokenizer(tokenizer, test_text)
    test_chunk_text(tokenizer, test_text)
