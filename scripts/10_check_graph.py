from pathlib import Path
import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


ROOT = Path(__file__).resolve().parent.parent

NODES_PATH = (
    ROOT / "graphs/routing/v1/extended/nodes.parquet"
)

EDGES_PATH = (
    ROOT / "graphs/routing/v1/extended/edges.parquet"
)

SNAPS_PATH = (
    ROOT / "data/processed/settlements/snapped/v1/extended.parquet"
)

OUT_DIR = ROOT / "data/processed/graph_features/v1/extended"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    node_ids = pq.read_table(
        NODES_PATH,
        columns=["node_id"]
    )["node_id"].to_numpy()

    n = len(node_ids)

    order = np.argsort(node_ids)
    sorted_ids = node_ids[order]

    if np.any(sorted_ids[1:] == sorted_ids[:-1]):
        raise ValueError("Обнаружены повторяющиеся node_id")

    edges = pq.read_table(
        EDGES_PATH,
        columns=["u", "v", "generalized_time_s"]
    )

    if edges["u"].null_count or edges["v"].null_count:
        raise ValueError("Есть рёбра с пустыми концами")

    u_ids = edges["u"].to_numpy()
    v_ids = edges["v"].to_numpy()

    weights = edges["generalized_time_s"].to_numpy(
        zero_copy_only=False
    )

    m = len(u_ids)

    def map_nodes(ids):
        positions = np.searchsorted(sorted_ids, ids)

        valid = positions < n
        safe_positions = np.minimum(positions, n - 1)

        valid &= sorted_ids[safe_positions] == ids

        result = np.full(len(ids), -1, dtype=np.int32)
        result[valid] = order[safe_positions[valid]]

        return result

    u = map_nodes(u_ids)
    v = map_nodes(v_ids)

    valid_endpoints = (u >= 0) & (v >= 0)

    missing_endpoints = int(
        np.count_nonzero(~valid_endpoints)
    )

    valid_weights = (
        np.isfinite(weights) & (weights > 0)
    )

    invalid_weights = int(
        np.count_nonzero(~valid_weights)
    )

    self_loops = int(
        np.count_nonzero(
            valid_endpoints & (u == v)
        )
    )

    usable = valid_endpoints & valid_weights

    src = u[usable]
    dst = v[usable]

    out_degree = np.bincount(
        src, minlength=n
    ).astype(np.int32)

    in_degree = np.bincount(
        dst, minlength=n
    ).astype(np.int32)

    adjacency = coo_matrix(
        (
            np.ones(len(src), dtype=np.uint8),
            (src, dst)
        ),
        shape=(n, n)
    ).tocsr()

    del src, dst

    component_count, labels = connected_components(
        adjacency,
        directed=True,
        connection="weak"
    )

    del adjacency

    component_sizes = np.bincount(labels)

    largest_component = int(component_sizes.max())
    isolated_nodes = int(
        np.count_nonzero(
            (in_degree + out_degree) == 0
        )
    )

    features = pa.table({
        "node_id": pa.array(node_ids),
        "in_degree": pa.array(in_degree),
        "out_degree": pa.array(out_degree),
        "total_degree": pa.array(in_degree + out_degree),
        "weak_component_id": pa.array(
            labels.astype(np.int32)
        ),
        "weak_component_size": pa.array(
            component_sizes[labels].astype(np.int32)
        ),
    })

    pq.write_table(
        features,
        OUT_DIR / "node_graph_features.parquet",
        compression="zstd"
    )

    snaps = pq.read_table(
        SNAPS_PATH,
        columns=[
            "settlement_osm_id",
            "node_id",
            "snap_status"
        ]
    )

    snap_nodes = snaps["node_id"].to_pylist()

    valid_snap_ids = [
        x for x in snap_nodes if x is not None
    ]

    snap_positions = map_nodes(
        np.asarray(valid_snap_ids, dtype=np.int64)
    )

    matched_snaps = int(
        np.count_nonzero(snap_positions >= 0)
    )

    report = {
        "scenario": "extended",
        "nodes": int(n),
        "edges": int(m),
        "missing_endpoint_edges": missing_endpoints,
        "invalid_weight_edges": invalid_weights,
        "self_loops": self_loops,
        "weak_components": int(component_count),
        "largest_weak_component_nodes": largest_component,
        "isolated_nodes": isolated_nodes,
        "settlements_total": int(snaps.num_rows),
        "settlements_with_node_id": len(valid_snap_ids),
        "settlements_with_existing_graph_node": matched_snaps,
        "degree_definition": (
            "Directed in/out degree, counting parallel edges"
        ),
        "component_definition": (
            "Weak connectivity on edges with valid endpoints "
            "and positive finite generalized_time_s"
        )
    }

    with open(
        OUT_DIR / "graph_audit_report.json",
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            report,
            f,
            ensure_ascii=False,
            indent=2
        )


if __name__ == "__main__":
    main()