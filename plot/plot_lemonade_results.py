import os
import json
import yaml
from pathlib import Path
import csv
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np

MODELS_BACKEND = [
        ("meta-llama/Llama-3.2-1B", "huggingface-load --device cpu --dtype bfloat16"),
        ("meta-llama/Llama-3.2-1B-Instruct", "huggingface-load --device cpu --dtype bfloat16"),

        ("meta-llama/Llama-3.2-3B", "huggingface-load --device cpu --dtype bfloat16"),
        ("meta-llama/Llama-3.2-3B-Instruct", "huggingface-load --device cpu --dtype bfloat16"),

        ("meta-llama/Meta-Llama-3.1-8B", "huggingface-load --device cpu --dtype bfloat16"),
        ("amd/Llama-3.1-8B-awq-g128-int4-asym-fp32-onnx-ryzen-strix", "oga-load --device npu --dtype int4"),
        ("meta-llama/Llama-3.1-8B-Instruct", "huggingface-load --device cpu --dtype bfloat16"),

        ("Qwen/Qwen1.5-7B-Chat", "huggingface-load --device cpu --dtype bfloat16"),
        ("amd/Qwen1.5-7B-Chat-awq-g128-int4-asym-fp32-onnx-ryzen-strix", "oga-load --device npu --dtype int4"),

        ("microsoft/Phi-3.5-mini-instruct", "huggingface-load --device cpu --dtype bfloat16"),
        ("amd/Phi-3.5-mini-instruct-awq-g128-int4-asym-fp32-onnx-ryzen-strix", "oga-load --device npu --dtype int4"),

        ("microsoft/Phi-3-mini-4k-instruct", "huggingface-load --device cpu --dtype bfloat16"),
        ("amd/Phi-3-mini-4k-instruct-awq-g128-int4-asym-fp32-onnx-ryzen-strix", "oga-load --device npu --dtype int4"),

        ("meta-llama/Llama-2-7b-hf", "huggingface-load --device cpu --dtype bfloat16"),
        ("amd/Llama-2-7b-hf-awq-g128-int4-asym-fp32-onnx-ryzen-strix", "oga-load --device npu --dtype int4"),

        ("meta-llama/Llama-2-7b-chat", "huggingface-load --device cpu --dtype bfloat16"),
        ("amd/Llama2-7b-chat-awq-g128-int4-asym-fp32-onnx-ryzen-strix", "oga-load --device npu --dtype int4"),

        ("mistralai/Mistral-7B-Instruct-v0.3", "huggingface-load --device cpu --dtype bfloat16"),
        ("amd/Mistral-7B-Instruct-v0.3-awq-g128-int4-asym-fp32-onnx-ryzen-strix", "oga-load --device npu --dtype int4"),
    ]

MODEL_SIZES = [
    ("meta-llama/Llama-3.2-1B", 1),
    ("meta-llama/Llama-3.2-1B-Instruct", 1),
    ("meta-llama/Llama-3.2-3B", 3),
    ("meta-llama/Llama-3.2-3B-Instruct", 3),
    ("meta-llama/Meta-Llama-3.1-8B", 8),
    ("amd/Llama-3.1-8B-awq-g128-int4-asym-fp32-onnx-ryzen-strix", 8),
    ("meta-llama/Llama-3.1-8B-Instruct", 8),
    ("Qwen/Qwen1.5-7B-Chat", 7),
    ("amd/Qwen1.5-7B-Chat-awq-g128-int4-asym-fp32-onnx-ryzen-strix", 7),
    ("microsoft/Phi-3.5-mini-instruct", 3.8),
    ("amd/Phi-3.5-mini-instruct-awq-g128-int4-asym-fp32-onnx-ryzen-strix", 3.8),
    ("microsoft/Phi-3-mini-4k-instruct", 3.8),
    ("amd/Phi-3-mini-4k-instruct-awq-g128-int4-asym-fp32-onnx-ryzen-strix", 3.8),
    ("meta-llama/Llama-2-7b-hf", 7),
    ("amd/Llama-2-7b-hf-awq-g128-int4-asym-fp32-onnx-ryzen-strix", 7),
    ("meta-llama/Llama-2-7b-chat", 7),
    ("amd/Llama2-7b-chat-awq-g128-int4-asym-fp32-onnx-ryzen-strix", 7),
    ("mistralai/Mistral-7B-Instruct-v0.3", 7.3),
    ("amd/Mistral-7B-Instruct-v0.3-awq-g128-int4-asym-fp32-onnx-ryzen-strix", 7.3)
]
# Citations:
# [1] https://www.infoq.com/news/2024/08/microsoft-phi-3-5/
# [2] https://www.aimodels.fyi/models/replicate/phi-3-mini-4k-instruct-lucataco
# [3] https://huggingface.co/microsoft/Phi-3-mini-4k-instruct-gguf
# [4] https://aws.amazon.com/blogs/aws/introducing-llama-3-2-models-from-meta-in-amazon-bedrock-a-new-generation-of-multimodal-vision-and-lightweight-models/
# [5] https://ollama.com/library/llama3.2:1b
# [6] https://openrouter.ai/meta-llama/llama-3.2-1b-instruct

def extract_stats():
    results = {}
    home_dir = Path.home()

    for model, backend in MODELS_BACKEND:
        cache_path = home_dir / ".cache" / "lemonade" / model.replace("/", "_")
        yaml_path = cache_path / "turnkey_stats.yaml"
        print(f"{yaml_path}\n")

        if yaml_path.exists():
            with open(yaml_path, 'r') as yaml_file:
                stats = yaml.safe_load(yaml_file)
                # Remove 'system_info' and 'timestamp' from stats
                stats.pop('system_info', None)
                stats.pop('timestamp', None)
                results[model] = {
                    "path": str(cache_path),
                    "stats": stats
                }
        else:
            print(f"File not found: {yaml_path}")

    output_path = "lemonade_stats.json"
    with open(output_path, 'w') as json_file:
        json.dump(results, json_file, indent=2, default=lambda o: f"<<non-serializable: {type(o).__qualname__}>>")

    print(f"Results saved to: {os.path.abspath(output_path)}")

def analyze_mmlu_accuracy(sort_order='original'):
    # Read the JSON file
    with open('lemonade_stats.json', 'r') as json_file:
        data = json.load(json_file)

    # Extract MMLU management accuracy for each model, maintaining original order
    results = []
    for model, _ in MODELS_BACKEND:
        if model in data:
            accuracy = data[model]['stats'].get('mmlu_management_accuracy')
            if accuracy is not None:
                results.append((model, accuracy))

    # Sort results based on the sort_order parameter
    if sort_order == 'ascending':
        results.sort(key=lambda x: x[1])
    elif sort_order == 'descending':
        results.sort(key=lambda x: x[1], reverse=True)
    # If sort_order is 'original' or any other value, keep the original order

    # Write to CSV
    with open('mmlu_management_accuracy.csv', 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['Model', 'MMLU Management Accuracy'])
        for model, accuracy in results:
            writer.writerow([model, f"{accuracy:.1f}"])  # One decimal place

    # Create plot with extra width
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(16, len(results) * 0.4 + 2))
    y_pos = range(len(results))
    models, accuracies = zip(*results)

    # Define colors
    default_color = '#00BFFF'  # Bright sky blue
    amd_color = '#FF4500'  # Bright red-orange

    # Create color list
    colors = [amd_color if model.startswith('amd/') else default_color for model in models]

    # Plot bars
    bars = ax.barh(y_pos, accuracies, color=colors)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(models, color='white')
    ax.invert_yaxis()  # Labels read top-to-bottom
    ax.set_xlabel('Accuracy (%)', color='white')
    ax.set_title('MMLU Management Accuracy by Model', color='white')
    ax.tick_params(colors='white')
    ax.xaxis.label.set_color('white')
    ax.yaxis.label.set_color('white')

    # Set x-axis ticks to one decimal place
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{x:.1f}"))

    # Add accuracy values at the end of each bar
    for i, v in enumerate(accuracies):
        ax.text(v, i, f' {v:.1f}%', va='center', color='white')

    # Set the background color
    fig.patch.set_facecolor('black')
    ax.set_facecolor('black')

    # Remove top and right spines
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['bottom'].set_color('white')
    ax.spines['left'].set_color('white')

    # Adjust layout and save with extra space on the right
    plt.tight_layout()
    plt.subplots_adjust(right=0.85)  # This adds extra space on the right
    plt.savefig('mmlu_management_accuracy.png', dpi=300, bbox_inches='tight', facecolor='black', edgecolor='none')
    plt.close()

    print("CSV and plot generated successfully.")

def analyze_mmlu_accuracy_vs_size(show_labels=False):
    # Read the JSON file
    with open('lemonade_stats.json', 'r') as json_file:
        data = json.load(json_file)

    # Extract MMLU management accuracy and size for each model
    results = []
    for model, size in MODEL_SIZES:
        if model in data:
            accuracy = data[model]['stats'].get('mmlu_management_accuracy')
            if accuracy is not None:
                results.append((model, size, accuracy))

    # Create plot
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(12, 8))

    # Plot non-AMD models in blue
    non_amd = [(size, accuracy) for model, size, accuracy in results if not model.startswith('amd/')]
    amd = [(size, accuracy) for model, size, accuracy in results if model.startswith('amd/')]

    ax.scatter(*zip(*non_amd), color='#00BFFF', label='Non-AMD Models', s=100)
    ax.scatter(*zip(*amd), color='#FF4500', label='AMD Models', s=100)

    if show_labels:
        for model, size, accuracy in results:
            ax.annotate(model, (size, accuracy), xytext=(5, 5), textcoords='offset points', color='white', fontsize=8)

    # Set labels and title
    ax.set_xlabel('Model Size (Billion Parameters)', color='white')
    ax.set_ylabel('MMLU Management Accuracy (%)', color='white')
    ax.set_title('MMLU Management Accuracy vs Model Size', color='white')

    # Customize the plot
    ax.tick_params(colors='white')
    ax.set_facecolor('black')
    fig.patch.set_facecolor('black')

    # Add legend
    ax.legend()

    # Add grid
    ax.grid(True, linestyle='--', alpha=0.3)

    # Adjust layout and save
    plt.tight_layout()
    plt.savefig('mmlu_accuracy_vs_size.png', dpi=300, bbox_inches='tight', facecolor='black', edgecolor='none')
    plt.close()

    print(f"Scatter plot of MMLU Accuracy vs Model Size {'with' if show_labels else 'without'} labels generated successfully.")

if __name__ == "__main__":
    extract_stats()
    # analyze_mmlu_accuracy('original')
    # analyze_mmlu_accuracy('descending')
    analyze_mmlu_accuracy('ascending')
    analyze_mmlu_accuracy_vs_size(show_labels=False)  # Set to True if you want labels
