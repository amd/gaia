import os
import json
import hashlib
import onnx
import networkx as nx
from collections import Counter
from typing import Dict
import numpy as np
import onnx
import argparse


def remove_node(model, op_type):
    graph = model.graph
    nodes_to_remove = []

    for node in graph.node:
        if node.op_type == op_type:
            for output_name in node.output:
                for idx, input_node in enumerate(graph.node):
                    if output_name in input_node.input:
                        # Re-route inputs of subsequent nodes to inputs of op_type
                        for i, input_node_input in enumerate(input_node.input):
                            if input_node_input == output_name:
                                input_node.input[i] = node.input[0]
            nodes_to_remove.append(node)

    # Remove DequantizeLinear nodes from the graph
    for node in nodes_to_remove:
        graph.node.remove(node)


def dequantize(onnx_model):
    remove_node(onnx_model, "DequantizeLinear")
    remove_node(onnx_model, "QuantizeLinear")


def count_parameters(onnx_model) -> int:
    """
    Returns the number of parameters of a given model
    """
    return int(
        sum(
            np.prod(tensor.dims, dtype=np.int64)
            for tensor in onnx_model.graph.initializer
            if tensor.name not in onnx_model.graph.input
        )
    )


def get_onnx_ops_list(onnx_model) -> Dict:
    """
    List unique ops found in the onnx model
    """
    onnx_ops_counter = {}
    for node in onnx_model.graph.node:  # pylint: disable=E1101
        onnx_ops_counter[node.op_type] = onnx_ops_counter.get(node.op_type, 0) + 1
    return onnx_ops_counter


def populate_onnx_model_info(onnx_model) -> Dict:
    """
    Read the model metadata to populate IR, Opset and model size
    """
    result_dict = {
        "ir_version": None,
        "opset": None,
        "size on disk (KiB)": None,
    }

    result_dict.update(
        {
            "ir_version": getattr(onnx_model, "ir_version", None),
            "opset": getattr(onnx_model.opset_import[0], "version", None),
        }
    )
    try:
        result_dict.update(
            {
                "size on disk (KiB)": round(
                    onnx_model.SerializeToString().__sizeof__() / 1024, 4
                ),
            }
        )
    except ValueError:
        # Models >2GB on disk cannot have their model size measured this
        # way and will throw a ValueError https://github.com/onnx/turnkeyml/issues/41
        pass

    return result_dict


def onnx_input_dimensions(onnx_model) -> Dict:
    """
    Read model input dimensions
    """
    input_shape = {}
    for input in onnx_model.graph.input:
        shape = str(input.type.tensor_type.shape.dim)
        input_shape[input.name] = [int(s) for s in shape.split() if s.isdigit()]
    return input_shape


def onnx_output_dimensions(onnx_model) -> Dict:
    """
    Read model output dimensions
    """
    output_shape = {}
    for output in onnx_model.graph.output:
        shape = str(output.type.tensor_type.shape.dim)
        output_shape[output.name] = [int(s) for s in shape.split() if s.isdigit()]
    return output_shape


class WeisfeilerLehmanMachine:
    """
    Weisfeiler Lehman feature extractor class.
    """

    def __init__(self, graph, features, iterations):
        """
        Initialization method which also executes feature extraction.
        :param graph: The Nx graph object.
        :param features: Feature hash table.
        :param iterations: Number of WL iterations.
        """
        self.iterations = iterations
        self.graph = graph
        self.features = features
        self.nodes = self.graph.nodes()
        self.extracted_features = [str(v) for k, v in features.items()]
        self.do_recursions()

    def do_a_recursion(self):
        """
        The method does a single WL recursion.
        :return new_features: The hash table with extracted WL features.
        """
        new_features = {}
        for node in self.nodes:
            nebs = self.graph.neighbors(node)
            degs = [self.features[neb] for neb in nebs]
            features = [str(self.features[node])] + sorted([str(deg) for deg in degs])
            features = "_".join(features)
            hash_object = hashlib.md5(features.encode())
            hashing = hash_object.hexdigest()
            new_features[node] = hashing
        self.extracted_features = self.extracted_features + list(new_features.values())
        return new_features

    def do_recursions(self):
        """
        The method does a series of WL recursions.
        """
        for _ in range(self.iterations):
            self.features = self.do_a_recursion()


def feature_extractor(path, rounds):
    """
    Function to extract WL features from a graph.
    :param path: The path to the graph json.
    :param rounds: Number of WL iterations.
    :return doc: Document collection object.
    """
    edges, features, name = dataset_reader(path)

    # Create undirected graph
    graph = nx.from_edgelist(edges)

    # Return a single doc for each WL machine
    machine = WeisfeilerLehmanMachine(graph, features, rounds)
    return machine.extracted_features


def dataset_reader(path):
    """
    Function to read the graph and features from a json file.
    :param path: The path to the graph json.
    :return graph: The graph object.
    :return features: Features hash table.
    :return name: Name of the graph.
    """
    name = path2name(path)
    data = json.load(open(path))
    edges = data["edges"]

    if "features" in data.keys():
        features = data["features"]
        features = {int(k): v for k, v in features.items()}
    else:
        features = nx.degree(graph)
        features = {int(k): v for k, v in features}

    return edges, features, name


def path2name(path):
    base = os.path.basename(path)
    return os.path.splitext(base)[0]


class GraphConverter:
    def __init__(self):
        self.node_name_to_id = {}
        self.node_id_counter = 0
        self.edges = []
        self.features = {}
        self.edge_is_input_of_node = {}  # edge -> node
        self.edge_is_output_of_node = {}  # edge -> node
        self.node_has_inputs_check = {}
        self.node_has_outputs_check = {}
        self.edge_expects_no_input_nodes = (
            []
        )  # This is an edge of an input node or identity node

    def add_node(self, node_name, feature):
        # Keep track of node
        if node_name in self.node_name_to_id:
            print(f"Adding node twice: {node_name}")
            return

        self.node_name_to_id[node_name] = self.node_id_counter
        self.node_id_counter += 1

        # Keep track of node feature
        self.features[self.node_name_to_id[node_name]] = feature

        # Starting checkers to False
        self.node_has_outputs_check[node_name] = False
        self.node_has_inputs_check[node_name] = False

    def add_edge(self, from_node_name, to_node_name_name):
        from_node_id = self.node_name_to_id[from_node_name]
        to_node_id = self.node_name_to_id[to_node_name_name]
        self.edges.append([from_node_id, to_node_id])

        # Keep track of which nodes have inputs and outputs assigned
        self.node_has_outputs_check[from_node_name] = True
        self.node_has_inputs_check[to_node_name_name] = True

    def onnx_to_json(self, onnx_model, skip_input_edge_check=False):
        """
        skip_input_edge_check   Do not check if edges are missing a source node.
                                This is ok as many edges are simply constants.
                                Set it to False to debug smaller graphs.
        """
        # Keep track of all edges in the graph
        for node in onnx_model.graph.node:

            # Create a mapping of nodes and node ids
            self.add_node(node.name, feature=node.op_type)

            # Keep track of all input edges
            for input_edge in node.input:

                # Edges that are inputs to identity nodes are not expected to come from any nodes
                # We also don't expect weights and biases to come from any nodes
                if (
                    node.op_type == "Identity"
                    or input_edge.endswith(".weight")
                    or input_edge.endswith(".bias")
                ):
                    self.edge_expects_no_input_nodes.append(input_edge)

                # A single edge can be the input of multiple nodes!
                if input_edge not in self.edge_is_input_of_node:
                    self.edge_is_input_of_node[input_edge] = [node.name]
                else:
                    self.edge_is_input_of_node[input_edge].append(node.name)

            # Keep track of all output edges
            for output_edge in node.output:
                self.edge_is_output_of_node[output_edge] = node.name

        # Add inputs as both nodes and input edges
        for input_node in onnx_model.graph.input:
            self.add_node(input_node.name, feature="input")
            self.edge_is_output_of_node[input_node.name] = input_node.name

        # Add outputs as both nodes and output edges
        for output_node in onnx_model.graph.output:
            self.add_node(output_node.name, feature="output")
            if output_node.name not in self.edge_is_input_of_node:
                self.edge_is_input_of_node[output_node.name] = [output_node.name]
            else:
                self.edge_is_input_of_node[output_node.name].append(output_node.name)

        # Create a list containing all edges
        all_edges = list(
            set(
                list(self.edge_is_output_of_node.keys())
                + list(self.edge_is_input_of_node.keys())
            )
        )

        # Add all edges to our IR
        for edge in all_edges:
            if edge not in self.edge_expects_no_input_nodes:

                if edge not in self.edge_is_output_of_node and (
                    "onnx::" in edge or skip_input_edge_check
                ):
                    # Some nodes have input edges named "onnx::" that are constants, but are unfortunately
                    # not marked as so. This means that we can't always verify wether inputs
                    # from some nodes are actually outputs from another node or simply constants.
                    # Here, we assume we are correctly identifying all relationships and skip
                    # checking "onnx::" input edges.
                    continue

                edge_from_node = self.edge_is_output_of_node[edge]
                try:
                    edges_to_node = self.edge_is_input_of_node[edge]
                except:
                    print(f"Could not find {edge}")
                    continue
                for edge_to_node in edges_to_node:
                    self.add_edge(edge_from_node, edge_to_node)

        # Ensuse we are not finding other artifacts and thinking that those are nodes
        # We expect to have all existing graph nodes + inputs + outputs as nodes
        nodes_found = len(
            set(list(self.node_has_inputs_check) + list(self.node_has_outputs_check))
        )
        nodes_expected = (
            len(onnx_model.graph.node)
            + len(onnx_model.graph.input)
            + len(onnx_model.graph.output)
        )
        # print(
        #    nodes_found == nodes_expected
        # ), "Number of nodes found does not match number of nodes in the graph"

        # Check if all nodes have inputs and outputs assigned to them
        input_edges = [i.name for i in onnx_model.graph.input]
        output_edges = [o.name for o in onnx_model.graph.output]
        for node_name in self.node_has_inputs_check:
            if self.node_has_inputs_check[node_name] == False:
                if (
                    "Constant" not in node_name
                    and "Identity" not in node_name
                    and node_name not in input_edges
                ):
                    print(f"\t\t{node_name} has no inputs!")
        for node_name in self.node_has_outputs_check:
            if self.node_has_outputs_check[node_name] == False:
                if "Identity" not in node_name and node_name not in output_edges:
                    print(f"{node_name} has no outputs!")

    def save(self, output_path):
        # Save JSON data to a file
        with open(output_path, "w") as outfile:
            json.dump({"edges": self.edges, "features": self.features}, outfile)


def find_onnx_files(directory):
    onnx_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(".onnx"):
                onnx_files.append(os.path.join(root, file))
    return onnx_files


def encode_model(model_path, model_name=None, dequantize=False):
    onnx_model = onnx.load(model_path)

    # Set a default name
    if not model_name:
        model_name = os.path.splitext(os.path.basename(model_path))[0]

    # Dequantize if needed
    if dequantize:
        dequantize(onnx_model)

    # Get model info
    input_dims = onnx_input_dimensions(onnx_model)
    parameters = count_parameters(onnx_model)
    onnx_model_info = populate_onnx_model_info(onnx_model)

    # Convert to nx graph
    converter = GraphConverter()
    converter.onnx_to_json(onnx_model, skip_input_edge_check=True)
    converter.save(f"{model_name}.json")

    # Extract subgraphs from nx graph
    features = feature_extractor(f"{model_name}.json", 1)
    subgraphs = dict(Counter(features))

    # Save as json
    features = {
        "input_dims": input_dims,
        "parameters": parameters,
        "onnx_model_info": onnx_model_info,
        "op_list": subgraphs,
    }
    out_location = os.path.join("features", f"{model_name}.json")
    with open(out_location, "w") as json_file:
        json.dump(features, json_file)
    print(f"Model encoded and saved to {out_location}")


def main():
    parser = argparse.ArgumentParser(description="Encode ONNX model")
    parser.add_argument("model_path", type=str, help="Path to the ONNX model file")
    parser.add_argument("--model_name", type=str, help="Name of the model (optional)")
    parser.add_argument(
        "--dequantize", action="store_true", help="Remove quantization nodes"
    )

    args = parser.parse_args()

    encode_model(args.model_path, args.model_name, args.dequantize)


if __name__ == "__main__":
    main()
