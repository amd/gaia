import os
import argparse
import json
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

from gaia.agents.Datalin.model_encode import encode_model


def find_match(model_path, dequantize=False, replace=False, dark_mode=False):

    # Get all features
    models_json = []
    for root, _, files in os.walk("features"):
        for file in files:
            if file.endswith(".json"):
                models_json.append(os.path.join(root, file))

    # Open feature of target model
    model_name = os.path.splitext(os.path.basename(model_path))[0]
    target_model_path = os.path.join("features", f"{model_name}.json")
    if os.path.exists(target_model_path):
        print(f"{target_model_path} found.")
        if replace:
            print("Replacing it!")
            encode_model(model_path=model_path, dequantize=dequantize)
        else:
            print(
                "Using encoded model found in the cache. Use `--replace` if you want to overwrite it."
            )
    else:
        print("Encoding model for the first time.")
        encode_model(model_path=model_path, dequantize=dequantize)

    with open(target_model_path, "r", encoding="utf-8") as json_file:
        target_model = json.load(json_file)

    # Filter columns we will show
    target_op_dict = target_model["op_list"]
    target_op_dict.pop("input", None)
    target_op_dict.pop("output", None)

    # Only keep the top num_subgraphs subgraphs and all ops
    num_subgraphs = 20
    subgraphs = {k: v for k, v in target_op_dict.items() if len(k) == 32}
    subgraphs = sorted(subgraphs, key=subgraphs.get, reverse=True)[:num_subgraphs]
    ops = list({k: v for k, v in target_op_dict.items() if len(k) != 32}.keys())
    target_op_dict = {k: v for k, v in target_op_dict.items() if k in subgraphs + ops}

    df = pd.DataFrame(columns=["Name", "Score"] + list(target_op_dict))

    scores = {}
    for reference_json in models_json:
        reference_name = (reference_json.split("\\")[-1]).split(".json")[0]
        with open(reference_json, "r", encoding="utf-8") as reference_json:
            reference_model = json.load(reference_json)

        score = 0
        row = {"Name": reference_name}

        for subgraph in target_op_dict:
            if subgraph in reference_model["op_list"]:

                ratio = reference_model["op_list"][subgraph] / target_op_dict[subgraph]
                if ratio > 1:
                    ratio = 1 / ratio
                # Make sure the colors show
                # if ratio > 0:
                #    ratio = float(min(ratio * 1.2 + 0.2, 1))
                row[subgraph] = ratio
                score += row[subgraph]
            else:
                row[subgraph] = 0.0
        score = score / len(target_op_dict)
        row["Score"] = score
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        scores[reference_name] = score

    # Convert to pandas and sort by score
    # df = pd.DataFrame(list(scores.items()), columns=["ModelName", "Score"])
    df_sorted = df.sort_values(by="Score", ascending=False)

    # Only keep top and bottom values
    df_sorted = pd.concat([df_sorted.head(25), df_sorted.tail(5)])

    name_list = df_sorted["Name"].tolist()
    df_sorted.drop("Score", axis=1, inplace=True)
    df_sorted.drop("Name", axis=1, inplace=True)

    df_sorted = pd.DataFrame(
        np.array(df_sorted.values),
        index=range(len(name_list)),
        columns=df_sorted.columns,
    )

    if dark_mode:
        plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(12, 10))
    im = ax.imshow(df_sorted, cmap="viridis")

    # Show all ticks and label them with the respective list entries
    ax.set_xticks(np.arange(len(df_sorted.columns)))
    ax.set_yticks(np.arange(len(name_list)))
    ax.set_xticklabels([a[:5] for a in df_sorted.columns])
    ax.set_yticklabels(name_list)

    # Rotate the tick labels and set their alignment
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right", rotation_mode="anchor")

    ax.set_title(f"Model Similarity Heatmap - {model_name}")

    cb = plt.colorbar(
        im,
        ax=ax,
        shrink=0.5,
        format="%.2f",
        label="Correlation Ratio",
        orientation="vertical",
        # pad=0.02,
    )
    cb.set_ticks([0, 0.5, 1])  # Set colorbar ticks at 0, 0.5, and 1
    cb.set_ticklabels(["0.0 (Low)", "0.5 (Medium)", "1.0 (High)"])  # Set corresponding labels
    cb.set_label("Correlation Ratio", labelpad=-100)

    fig.tight_layout()

    fig.savefig("heatmap.png")

    plt.close(fig)

    return name_list, reference_model


def main():
    parser = argparse.ArgumentParser(description="Subgraph Similarity Plot")
    parser.add_argument("model_path", type=str, help="Path to the ONNX model file")
    parser.add_argument("--dequantize", action="store_true", help="Remove quantization nodes")
    parser.add_argument("--replace", action="store_true", help="Replace models previously encoded")
    parser.add_argument("--dark-mode", action="store_true", help="Plot image in dark mode")

    args = parser.parse_args()
    find_match(args.model_path, args.dequantize, args.replace, args.dark_mode)


if __name__ == "__main__":
    main()
