import onnx
from onnx import helper, shape_inference


def split_onnx_model(onnx_model_path, num_subgraphs):
    # Load ONNX model
    model = onnx.load(onnx_model_path, load_external_data=False)
    model_name = onnx_model_path.split("/")[-1].split(".")[0]

    # Infer shapes (optional but useful for further processing)
    model = shape_inference.infer_shapes(model)

    # Determine split points
    total_nodes = len(model.graph.node)
    split_points = [
        int(total_nodes * (i + 1) / num_subgraphs) for i in range(num_subgraphs - 1)
    ]

    # Create subgraphs
    subgraph_models = []
    start_index = 0
    for i, split_point in enumerate(split_points):
        subgraph_nodes = model.graph.node[start_index:split_point]
        subgraph_graph = helper.make_graph(
            subgraph_nodes, f"subgraph_{i+1}", model.graph.input, model.graph.output
        )
        subgraph_model = helper.make_model(subgraph_graph)
        subgraph_models.append(subgraph_model)
        start_index = split_point

    # Handle the last subgraph
    subgraph_nodes = model.graph.node[start_index:]
    subgraph_graph = helper.make_graph(
        subgraph_nodes,
        f"subgraph_{num_subgraphs}",
        model.graph.input,
        model.graph.output,
    )
    subgraph_model = helper.make_model(subgraph_graph)
    subgraph_models.append(subgraph_model)

    model_names = []
    for i, subgraph_model in enumerate(subgraph_models):
        model_path = f"{model_name}_sub{i+1}.onnx"
        model_names.append(model_path)
        onnx.save(subgraph_model, model_path)
    return model_names
