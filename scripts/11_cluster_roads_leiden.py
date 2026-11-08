from pathlib import Path
import json

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import igraph as ig
import leidenalg


ROOT = Path(__file__).resolve().parent.parent

GRAPH_DIR = ROOT / "graphs/routing/v1/extended"

FEATURES_PATH = (
    ROOT / "data/processed/graph_features/v1/extended"
    / "node_graph_features.parquet"
)

OUT_DIR = ROOT / "data/processed/clustering/v1/leiden"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    features = pq.read_table(
        FEATURES_PATH,
        columns=["node_id", "weak_component_id"]
    ).to_pandas()

    largest_id = (
        features["weak_component_id"]
        .value_counts()
        .idxmax()
    )

    main_nodes = features.loc[
        features["weak_component_id"] == largest_id,
        "node_id"
    ].to_numpy(dtype=np.int64)

    edges = pq.read_table(
        GRAPH_DIR / "edges.parquet",
        columns=["u", "v"]
    ).to_pandas()

    mask = (
        edges["u"].isin(main_nodes)
        & edges["v"].isin(main_nodes)
    )

    edges = edges.loc[mask, ["u", "v"]].copy()

    node_index = pd.Index(main_nodes)

    u = node_index.get_indexer(edges["u"])
    v = node_index.get_indexer(edges["v"])

    pairs = np.column_stack([
        np.minimum(u, v),
        np.maximum(u, v)
    ])

    pairs = np.unique(pairs, axis=0)

    graph = ig.Graph(
        n=len(main_nodes),
        edges=pairs.tolist(),
        directed=False
    )

    del edges, pairs, u, v

    partition = leidenalg.find_partition(
        graph,
        leidenalg.RBConfigurationVertexPartition,
        resolution_parameter=1.0,
        seed=42
    )

    labels = np.asarray(
        partition.membership,
        dtype=np.int32
    )

    result = pd.DataFrame({
        "node_id": main_nodes,
        "cluster_id": labels
    })

    result.to_parquet(
        OUT_DIR / "node_clusters.parquet",
        index=False
    )

    sizes = (
        result.groupby("cluster_id")
        .size()
        .sort_values(ascending=False)
    )

    sizes.rename("node_count").to_csv(
        OUT_DIR / "cluster_sizes.csv",
        encoding="utf-8-sig"
    )

    report = {
        "method": "Leiden",
        "graph_type": "undirected_unweighted",
        "resolution": 1.0,
        "seed": 42,
        "nodes": int(graph.vcount()),
        "edges": int(graph.ecount()),
        "clusters": int(len(partition)),
        "largest_cluster_nodes": int(sizes.iloc[0]),
        "smallest_cluster_nodes": int(sizes.iloc[-1]),
    }

    with open(
        OUT_DIR / "clustering_report.json",
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            report,
            file,
            ensure_ascii=False,
            indent=2
        )


if __name__ == "__main__":
    main()