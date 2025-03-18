# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import json
import yaml
from pathlib import Path
import csv
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
from adjustText import adjust_text
import plotly.graph_objects as go
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import plotly.io as pio

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
    ("meta-llama/Meta-Llama-3.1-8B", "huggingface-load --device cpu --dtype bfloat16"),
    (
        "amd/Llama-3.1-8B-awq-g128-int4-asym-fp32-onnx-ryzen-strix",
        "oga-load --device npu --dtype int4",
    ),
    (
        "meta-llama/Llama-3.1-8B-Instruct",
        "huggingface-load --device cpu --dtype bfloat16",
    ),
    ("amd/Meta-Llama-3-8B-Instruct-int4-oga-npu", "oga-load --device npu --dtype int4"),
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

MODEL_SIZES = [
    ("meta-llama/Llama-3.2-1B", 1),
    ("meta-llama/Llama-3.2-1B-Instruct", 1),
    ("meta-llama/Llama-3.2-3B", 3),
    ("meta-llama/Llama-3.2-3B-Instruct", 3),
    ("meta-llama/Meta-Llama-3.1-8B", 8),
    ("amd/Llama-3.1-8B-awq-g128-int4-asym-fp32-onnx-ryzen-strix", 8),
    ("meta-llama/Llama-3.1-8B-Instruct", 8),
    ("amd/Meta-Llama-3-8B-Instruct-int4-oga-npu", 8),
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
    ("amd/Mistral-7B-Instruct-v0.3-awq-g128-int4-asym-fp32-onnx-ryzen-strix", 7.3),
]


# Add this function at the beginning of your script
def ensure_output_folder():
    output_folder = "output_plots"
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    return output_folder


def extract_stats():
    results = {}
    home_dir = Path.home()

    for model, backend in MODELS_BACKEND:
        cache_path = home_dir / ".cache" / "lemonade" / model.replace("/", "_")
        yaml_path = cache_path / "turnkey_stats.yaml"
        print(f"{yaml_path}\n")

        if yaml_path.exists():
            with open(yaml_path, "r") as yaml_file:
                stats = yaml.safe_load(yaml_file)
                # Remove 'system_info' and 'timestamp' from stats
                stats.pop("system_info", None)
                stats.pop("timestamp", None)
                results[model] = {"path": str(cache_path), "stats": stats}
        else:
            print(f"File not found: {yaml_path}")

    output_folder = ensure_output_folder()
    output_path = os.path.join(output_folder, "lemonade_stats.json")
    with open(output_path, "w") as json_file:
        json.dump(
            results,
            json_file,
            indent=2,
            default=lambda o: f"<<non-serializable: {type(o).__qualname__}>>",
        )

    print(f"Results saved to: {os.path.abspath(output_path)}")


def analyze_mmlu_accuracy(sort_order="original", show_labels=True):
    output_folder = ensure_output_folder()
    json_path = os.path.join(output_folder, "lemonade_stats.json")
    with open(json_path, "r") as json_file:
        data = json.load(json_file)

    # Extract MMLU management accuracy for each model, maintaining original order
    results = []
    for model, _ in MODELS_BACKEND:
        if model in data:
            accuracy = data[model]["stats"].get("mmlu_management_accuracy")
            if accuracy is not None:
                results.append((model, accuracy))

    # Check if we have any results
    if not results:
        print("No MMLU accuracy results found in the data.")
        return

    # Sort results based on the sort_order parameter
    if sort_order == "ascending":
        results.sort(key=lambda x: x[1])
    elif sort_order == "descending":
        results.sort(key=lambda x: x[1], reverse=True)
    # If sort_order is 'original' or any other value, keep the original order

    # Write to CSV
    output_folder = ensure_output_folder()
    csv_path = os.path.join(output_folder, "mmlu_management_accuracy.csv")
    with open(csv_path, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Model", "MMLU Management Accuracy"])
        for model, accuracy in results:
            writer.writerow([model, f"{accuracy:.1f}"])  # One decimal place

    # Create plot with extra width
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(16, len(results) * 0.4 + 2))
    y_pos = range(len(results))
    models, accuracies = zip(*results)

    # Define colors
    default_color = "#00BFFF"  # Bright sky blue
    amd_color = "#FF4500"  # Bright red-orange

    # Create color list
    colors = [
        amd_color if model.startswith("amd/") else default_color for model in models
    ]

    # Plot bars
    bars = ax.barh(y_pos, accuracies, color=colors)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(models, color="white")
    ax.invert_yaxis()  # Labels read top-to-bottom
    ax.set_xlabel("Accuracy (%)", color="white")
    ax.set_title("MMLU Management Accuracy by Model", color="white")
    ax.tick_params(colors="white")
    ax.xaxis.label.set_color("white")
    ax.yaxis.label.set_color("white")

    # Set x-axis ticks to one decimal place
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{x:.1f}"))

    # Add accuracy values at the end of each bar
    for i, v in enumerate(accuracies):
        ax.text(v, i, f" {v:.1f}%", va="center", color="white")

    # Set the background color
    fig.patch.set_facecolor("black")
    ax.set_facecolor("black")

    # Remove top and right spines
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color("white")
    ax.spines["left"].set_color("white")

    # Adjust layout and save with extra space on the right
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.2)  # Add space at the bottom for the legend
    if show_labels:
        ax.legend(
            loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=2
        )  # Move legend to bottom
    output_folder = ensure_output_folder()
    plt.savefig(
        os.path.join(output_folder, "mmlu_management_accuracy.png"),
        dpi=300,
        bbox_inches="tight",
        facecolor="black",
        edgecolor="none",
    )
    plt.close()

    print(f"CSV saved as {csv_path}")
    print("CSV and plot generated successfully.")


def analyze_mmlu_accuracy_vs_size(show_labels=True):
    output_folder = ensure_output_folder()
    json_path = os.path.join(output_folder, "lemonade_stats.json")
    with open(json_path, "r") as json_file:
        data = json.load(json_file)

    # Extract MMLU management accuracy and size for each model
    results = []
    for model, size in MODEL_SIZES:
        if model in data:
            accuracy = data[model]["stats"].get("mmlu_management_accuracy")
            if accuracy is not None:
                results.append((model, size, accuracy))

    # Create static plot
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(12, 8))

    # Plot each model separately to create individual legend entries
    for model, size, accuracy in results:
        color = "#FF4500" if model.startswith("amd/") else "#00BFFF"
        ax.scatter(size, accuracy, color=color, s=100, label=model)

    # Set labels and title
    ax.set_xlabel("Model Size (Billion Parameters)", color="white")
    ax.set_ylabel("MMLU Management Accuracy (%)", color="white")
    ax.set_title("MMLU Management Accuracy vs Model Size", color="white")

    # Customize the plot
    ax.tick_params(colors="white")
    ax.set_facecolor("black")
    fig.patch.set_facecolor("black")

    # Add legend at the bottom
    if show_labels:
        ax.legend(
            loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=3, fontsize="small"
        )

    # Add grid
    ax.grid(True, linestyle="--", alpha=0.3)

    # Adjust layout and save
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.3)  # Increase bottom margin to accommodate the legend
    output_folder = ensure_output_folder()
    plt.savefig(
        os.path.join(output_folder, "mmlu_accuracy_vs_size.png"),
        dpi=300,
        bbox_inches="tight",
        facecolor="black",
        edgecolor="none",
    )
    plt.close()

    print("Static plot for MMLU Accuracy vs Model Size generated successfully.")


def create_interactive_mmlu_accuracy_vs_size():
    # Read the JSON file
    output_folder = ensure_output_folder()
    json_path = os.path.join(output_folder, "lemonade_stats.json")
    with open(json_path, "r") as json_file:
        data = json.load(json_file)

    # Extract MMLU management accuracy and size for each model
    results = []
    for model, size in MODEL_SIZES:
        if model in data:
            accuracy = data[model]["stats"].get("mmlu_management_accuracy")
            if accuracy is not None:
                results.append((model, size, accuracy))

    # Create interactive Plotly plot
    fig = go.Figure()

    # Add traces for non-AMD and AMD models
    fig.add_trace(
        go.Scatter(
            x=[size for model, size, _ in results if not model.startswith("amd/")],
            y=[
                accuracy
                for model, _, accuracy in results
                if not model.startswith("amd/")
            ],
            mode="markers",
            name="Non-AMD Models",
            marker=dict(color="#00BFFF", size=10),
            text=[model for model, _, _ in results if not model.startswith("amd/")],
            hovertemplate="<b>%{text}</b><br>Size: %{x}B<br>Accuracy: %{y:.2f}%<extra></extra>",
        )
    )

    fig.add_trace(
        go.Scatter(
            x=[size for model, size, _ in results if model.startswith("amd/")],
            y=[accuracy for model, _, accuracy in results if model.startswith("amd/")],
            mode="markers",
            name="AMD Models",
            marker=dict(color="#FF4500", size=10),
            text=[model for model, _, _ in results if model.startswith("amd/")],
            hovertemplate="<b>%{text}</b><br>Size: %{x}B<br>Accuracy: %{y:.2f}%<extra></extra>",
        )
    )

    # Update layout
    fig.update_layout(
        title="MMLU Management Accuracy vs Model Size",
        xaxis_title="Model Size (Billion Parameters)",
        yaxis_title="MMLU Management Accuracy (%)",
        template="plotly_dark",
        hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
    )

    # Add grid
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor="rgba(255,255,255,0.1)")
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor="rgba(255,255,255,0.1)")

    # Save interactive plot
    output_folder = ensure_output_folder()
    pio.write_html(
        fig,
        file=os.path.join(output_folder, "mmlu_accuracy_vs_size_interactive.html"),
        auto_open=False,
    )

    print("Interactive plot for MMLU Accuracy vs Model Size generated successfully.")


def adjust_label_positions(positions, labels, min_gap=10, max_adjustment=50):
    sorted_indices = sorted(range(len(positions)), key=lambda k: positions[k])
    adjusted_positions = positions.copy()

    for i in range(1, len(sorted_indices)):
        curr_idx = sorted_indices[i]
        prev_idx = sorted_indices[i - 1]
        if adjusted_positions[curr_idx] - adjusted_positions[prev_idx] < min_gap:
            shift = min(
                min_gap - (adjusted_positions[curr_idx] - adjusted_positions[prev_idx]),
                max_adjustment,
            )
            adjusted_positions[curr_idx] += shift

    return adjusted_positions


def plot_with_spaced_labels(
    data, x_key, y_key, title, xlabel, ylabel, filename, show_labels=True
):
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(16, 10))

    # Create scatter plot
    for model, model_data in data.items():
        stats = model_data["stats"]
        x_values = []
        y_values = []
        for key, value in stats.items():
            if key.startswith(x_key) and key != f"{x_key}_input_tokens":
                try:
                    tokens = int(key.split("_")[-1])
                    x_values.append(tokens)
                    y_values.append(value)
                except ValueError:
                    pass
                    # print(f"Skipping invalid key: {key}")

        if x_values and y_values:
            color = "red" if model.startswith("amd/") else "#1f77b4"
            ax.scatter(x_values, y_values, label=model, c=color)

    ax.set_xlabel(xlabel, color="white")
    ax.set_ylabel(ylabel, color="white")
    ax.set_title(title, color="white")
    ax.tick_params(colors="white")
    ax.set_facecolor("black")
    fig.patch.set_facecolor("black")
    ax.grid(True, linestyle="--", alpha=0.3)

    if show_labels:
        # Create legend for all data points at the bottom
        handles, labels = ax.get_legend_handles_labels()
        bottom_legend = ax.legend(
            handles, labels, loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=3
        )
        ax.add_artist(bottom_legend)  # Explicitly add the legend to the axes

        # Create separate legend for CPU and NPU
        cpu_patch = Patch(color="#1f77b4", label="CPU")
        npu_patch = Patch(color="red", label="NPU")
        ax.add_artist(plt.legend(handles=[cpu_patch, npu_patch], loc="upper right"))

    plt.tight_layout()
    plt.subplots_adjust(bottom=0.2)  # Add space at the bottom for the legend
    output_folder = ensure_output_folder()
    plt.savefig(
        os.path.join(output_folder, filename),
        dpi=300,
        bbox_inches="tight",
        facecolor="black",
        edgecolor="none",
    )
    plt.close()


def create_interactive_plot(data, x_key, y_key, title, xlabel, ylabel, filename):
    fig = go.Figure()

    for model, model_data in data.items():
        stats = model_data["stats"]
        x_values = []
        y_values = []
        for key, value in stats.items():
            if key.startswith(x_key) and key != f"{x_key}_input_tokens":
                try:
                    tokens = int(key.split("_")[-1])
                    x_values.append(tokens)
                    y_values.append(value)
                except ValueError:
                    pass
                    # print(f"Skipping invalid key: {key}")

        if x_values and y_values:
            color = "red" if model.startswith("amd/") else "blue"
            fig.add_trace(
                go.Scatter(
                    x=x_values,
                    y=y_values,
                    mode="markers",
                    name=model,
                    marker=dict(color=color, size=10),
                    hovertemplate=f"{model}<br>{xlabel}: %{{x}}<br>{ylabel}: %{{y:.2f}}<extra></extra>",
                )
            )

    fig.update_layout(
        title=title,
        xaxis_title=xlabel,
        yaxis_title=ylabel,
        template="plotly_dark",
        hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5),
    )

    fig.write_html(filename)


def plot_input_tokens_vs_mean_tokens_per_second(show_labels=True):
    output_folder = ensure_output_folder()
    json_path = os.path.join(output_folder, "lemonade_stats.json")
    with open(json_path, "r") as json_file:
        data = json.load(json_file)

    # Original static plot
    plot_with_spaced_labels(
        data,
        "mean_tokens_per_second",
        "mean_tokens_per_second",
        "Input Tokens vs Mean Tokens per Second",
        "Input Tokens",
        "Mean Tokens per Second",
        "input_tokens_vs_mean_tokens_per_second.png",
        show_labels,
    )

    # New interactive plot
    output_folder = ensure_output_folder()
    create_interactive_plot(
        data,
        "mean_tokens_per_second",
        "mean_tokens_per_second",
        "Input Tokens vs Mean Tokens per Second",
        "Input Tokens",
        "Mean Tokens per Second",
        os.path.join(
            output_folder, "input_tokens_vs_mean_tokens_per_second_interactive.html"
        ),
    )


def plot_input_tokens_vs_time_to_first_token(show_labels=True):
    output_folder = ensure_output_folder()
    json_path = os.path.join(output_folder, "lemonade_stats.json")
    with open(json_path, "r") as json_file:
        data = json.load(json_file)

    # Original static plot
    plot_with_spaced_labels(
        data,
        "seconds_to_first_token",
        "seconds_to_first_token",
        "Input Tokens vs Time to First Token",
        "Input Tokens",
        "Time to First Token (seconds)",
        "input_tokens_vs_time_to_first_token.png",
        show_labels,
    )

    # New interactive plot
    output_folder = ensure_output_folder()
    create_interactive_plot(
        data,
        "seconds_to_first_token",
        "seconds_to_first_token",
        "Input Tokens vs Time to First Token",
        "Input Tokens",
        "Time to First Token (seconds)",
        os.path.join(
            output_folder, "input_tokens_vs_time_to_first_token_interactive.html"
        ),
    )


def plot_mmlu_accuracy_vs_tokens_per_second(show_labels=True):
    output_folder = ensure_output_folder()
    json_path = os.path.join(output_folder, "lemonade_stats.json")
    with open(json_path, "r") as json_file:
        data = json.load(json_file)

    results = []
    for model, _ in MODEL_SIZES:
        if model in data:
            accuracy = data[model]["stats"].get("mmlu_management_accuracy")
            tokens_per_second = data[model]["stats"].get("mean_tokens_per_second_2048")
            if accuracy is not None and tokens_per_second is not None:
                results.append((model, tokens_per_second, accuracy))

    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(12, 8))

    for model, tokens_per_second, accuracy in results:
        color = "#FF4500" if model.startswith("amd/") else "#00BFFF"
        ax.scatter(tokens_per_second, accuracy, color=color, s=100, label=model)

    ax.set_xlabel("Tokens per Second", color="white")
    ax.set_ylabel("MMLU Management Accuracy (%)", color="white")
    ax.set_title("MMLU Accuracy vs Tokens per Second", color="white")
    ax.tick_params(colors="white")
    ax.set_facecolor("black")
    fig.patch.set_facecolor("black")

    if show_labels:
        ax.legend(
            loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=3, fontsize="small"
        )

    ax.grid(True, linestyle="--", alpha=0.3)
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.3)
    output_folder = ensure_output_folder()
    plt.savefig(
        os.path.join(output_folder, "mmlu_accuracy_vs_tokens_per_second.png"),
        dpi=300,
        bbox_inches="tight",
        facecolor="black",
        edgecolor="none",
    )
    plt.close()

    print("Static plot for MMLU Accuracy vs Tokens per Second generated successfully.")


def plot_mmlu_accuracy_vs_time_to_first_token(show_labels=True):
    output_folder = ensure_output_folder()
    json_path = os.path.join(output_folder, "lemonade_stats.json")
    with open(json_path, "r") as json_file:
        data = json.load(json_file)

    results = []
    for model, _ in MODEL_SIZES:
        if model in data:
            accuracy = data[model]["stats"].get("mmlu_management_accuracy")
            time_to_first_token = data[model]["stats"].get(
                "seconds_to_first_token_2048"
            )
            if accuracy is not None and time_to_first_token is not None:
                results.append((model, time_to_first_token, accuracy))

    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(12, 8))

    for model, time_to_first_token, accuracy in results:
        color = "#FF4500" if model.startswith("amd/") else "#00BFFF"
        ax.scatter(time_to_first_token, accuracy, color=color, s=100, label=model)

    ax.set_xlabel("Time to First Token (seconds)", color="white")
    ax.set_ylabel("MMLU Management Accuracy (%)", color="white")
    ax.set_title("MMLU Accuracy vs Time to First Token", color="white")
    ax.tick_params(colors="white")
    ax.set_facecolor("black")
    fig.patch.set_facecolor("black")

    if show_labels:
        ax.legend(
            loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=3, fontsize="small"
        )

    ax.grid(True, linestyle="--", alpha=0.3)
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.3)
    output_folder = ensure_output_folder()
    plt.savefig(
        os.path.join(output_folder, "mmlu_accuracy_vs_time_to_first_token.png"),
        dpi=300,
        bbox_inches="tight",
        facecolor="black",
        edgecolor="none",
    )
    plt.close()

    print(
        "Static plot for MMLU Accuracy vs Time to First Token generated successfully."
    )


def create_interactive_mmlu_accuracy_vs_tokens_per_second():
    output_folder = ensure_output_folder()
    json_path = os.path.join(output_folder, "lemonade_stats.json")
    with open(json_path, "r") as json_file:
        data = json.load(json_file)

    results = []
    for model, _ in MODEL_SIZES:
        if model in data:
            accuracy = data[model]["stats"].get("mmlu_management_accuracy")
            tokens_per_second = data[model]["stats"].get("mean_tokens_per_second_2048")
            if accuracy is not None and tokens_per_second is not None:
                results.append((model, tokens_per_second, accuracy))

    fig = go.Figure()

    for model, tokens_per_second, accuracy in results:
        color = "#FF4500" if model.startswith("amd/") else "#00BFFF"
        fig.add_trace(
            go.Scatter(
                x=[tokens_per_second],
                y=[accuracy],
                mode="markers",
                name=model,
                marker=dict(color=color, size=10),
                hovertemplate="<b>%{text}</b><br>Tokens per Second: %{x:.2f}<br>Accuracy: %{y:.2f}%<extra></extra>",
                text=[model],
            )
        )

    fig.update_layout(
        title="MMLU Accuracy vs Tokens per Second",
        xaxis_title="Tokens per Second",
        yaxis_title="MMLU Management Accuracy (%)",
        template="plotly_dark",
        hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=-0.5, xanchor="center", x=0.5),
        margin=dict(b=150),
    )

    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor="rgba(255,255,255,0.1)")
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor="rgba(255,255,255,0.1)")

    output_folder = ensure_output_folder()
    pio.write_html(
        fig,
        file=os.path.join(
            output_folder, "mmlu_accuracy_vs_tokens_per_second_interactive.html"
        ),
        auto_open=False,
    )

    print(
        "Interactive plot for MMLU Accuracy vs Tokens per Second generated successfully."
    )


def create_interactive_mmlu_accuracy_vs_time_to_first_token():
    output_folder = ensure_output_folder()
    json_path = os.path.join(output_folder, "lemonade_stats.json")
    with open(json_path, "r") as json_file:
        data = json.load(json_file)

    results = []
    for model, _ in MODEL_SIZES:
        if model in data:
            accuracy = data[model]["stats"].get("mmlu_management_accuracy")
            time_to_first_token = data[model]["stats"].get(
                "seconds_to_first_token_2048"
            )
            if accuracy is not None and time_to_first_token is not None:
                results.append((model, time_to_first_token, accuracy))

    fig = go.Figure()

    for model, time_to_first_token, accuracy in results:
        color = "#FF4500" if model.startswith("amd/") else "#00BFFF"
        fig.add_trace(
            go.Scatter(
                x=[time_to_first_token],
                y=[accuracy],
                mode="markers",
                name=model,
                marker=dict(color=color, size=10),
                hovertemplate="<b>%{text}</b><br>Time to First Token: %{x:.2f}s<br>Accuracy: %{y:.2f}%<extra></extra>",
                text=[model],
            )
        )

    fig.update_layout(
        title="MMLU Accuracy vs Time to First Token",
        xaxis_title="Time to First Token (seconds)",
        yaxis_title="MMLU Management Accuracy (%)",
        template="plotly_dark",
        hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=-0.5, xanchor="center", x=0.5),
        margin=dict(b=150),
    )

    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor="rgba(255,255,255,0.1)")
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor="rgba(255,255,255,0.1)")

    output_folder = ensure_output_folder()
    pio.write_html(
        fig,
        file=os.path.join(
            output_folder, "mmlu_accuracy_vs_time_to_first_token_interactive.html"
        ),
        auto_open=False,
    )

    print(
        "Interactive plot for MMLU Accuracy vs Time to First Token generated successfully."
    )


if __name__ == "__main__":
    extract_stats()
    analyze_mmlu_accuracy("ascending", show_labels=True)
    analyze_mmlu_accuracy_vs_size(show_labels=True)
    create_interactive_mmlu_accuracy_vs_size()

    plot_input_tokens_vs_mean_tokens_per_second(show_labels=True)
    plot_input_tokens_vs_time_to_first_token(show_labels=True)

    plot_mmlu_accuracy_vs_tokens_per_second(show_labels=True)
    plot_mmlu_accuracy_vs_time_to_first_token(show_labels=True)
    create_interactive_mmlu_accuracy_vs_tokens_per_second()
    create_interactive_mmlu_accuracy_vs_time_to_first_token()

    print("All plots generated")
