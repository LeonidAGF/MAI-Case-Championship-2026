from pathlib import Path
import csv
import json
import math

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "results" / "geo_embeddings"

NODES_FILE = DATA_DIR / "small_graph_infrastructure_features.csv"
EDGES_FILE = DATA_DIR / "small_graph_edges.csv"

X_FILE = DATA_DIR / "graphsage_x.npy"
EDGE_INDEX_FILE = DATA_DIR / "graphsage_edge_index.npy"
EDGE_TIME_FILE = DATA_DIR / "graphsage_edge_time_s.npy"
IDS_FILE = DATA_DIR / "graphsage_settlement_ids.csv"
METADATA_FILE = DATA_DIR / "graphsage_metadata.json"

CATEGORIES = (
    "school",
    "kindergarten",
    "hospital",
    "clinic",
)

RADII = (5, 10, 20)

DISTANCE_CAP_KM = 100.0


def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(path)

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:
        return list(csv.DictReader(file))


def parse_float(value, default=None):
    if value is None or str(value).strip() == "":
        return default

    try:
        number = float(value)
    except (ValueError, TypeError):
        return default

    return number if math.isfinite(number) else default


def build_feature_matrix(rows):
    feature_names = []
    columns = []

    for category in CATEGORIES:
        for radius in RADII:
            name = f"{category}_{radius}km"

            values = np.array([
                parse_float(row.get(name), 0.0)
                for row in rows
            ], dtype=np.float64)

            values = np.maximum(values, 0.0)

            values = np.log1p(values)

            feature_names.append(f"log1p_{name}")
            columns.append(values)

    for category in CATEGORIES:
        name = f"nearest_{category}_km"

        raw = [
            parse_float(row.get(name))
            for row in rows
        ]

        missing = np.array([
            value is None
            for value in raw
        ], dtype=np.float64)

        distances = np.array([
            DISTANCE_CAP_KM
            if value is None
            else min(max(value, 0.0), DISTANCE_CAP_KM)
            for value in raw
        ], dtype=np.float64)

        distances = np.log1p(distances)

        feature_names.append(f"log1p_{name}")
        columns.append(distances)

        feature_names.append(f"missing_{name}")
        columns.append(missing)

    matrix = np.column_stack(columns)

    return matrix, feature_names


def standardize_features(matrix, feature_names):
    means = matrix.mean(axis=0)
    stds = matrix.std(axis=0)

    safe_stds = np.where(
        stds < 1e-8,
        1.0,
        stds
    )

    normalized = (matrix - means) / safe_stds

    if not np.isfinite(normalized).all():
        raise RuntimeError(
            "Feature matrix contains NaN or Inf."
        )

    stats = {}

    for i, name in enumerate(feature_names):
        stats[name] = {
            "mean": float(means[i]),
            "std": float(stds[i]),
        }

    return normalized.astype(np.float32), stats


def build_edges(edge_rows, id_to_index):
    directed_edges = {}
    skipped = 0

    for row in edge_rows:
        source_id = str(row["source"]).strip()
        target_id = str(row["target"]).strip()

        if (
            source_id not in id_to_index
            or target_id not in id_to_index
        ):
            skipped += 1
            continue

        source = id_to_index[source_id]
        target = id_to_index[target_id]

        if source == target:
            continue

        travel_time = parse_float(
            row.get("travel_time_s")
        )

        if travel_time is None or travel_time <= 0:
            skipped += 1
            continue

        key = (source, target)

        if key not in directed_edges:
            directed_edges[key] = travel_time
        else:
            directed_edges[key] = min(
                directed_edges[key],
                travel_time
            )

    message_edges = set(directed_edges)

    for source, target in directed_edges:
        message_edges.add((target, source))

    ordered_message_edges = sorted(message_edges)

    edge_index = np.array(
        ordered_message_edges,
        dtype=np.int64
    ).T

    ordered_original_edges = sorted(directed_edges)

    original_edge_index = np.array(
        ordered_original_edges,
        dtype=np.int64
    ).T

    original_edge_times = np.array([
        directed_edges[edge]
        for edge in ordered_original_edges
    ], dtype=np.float32)

    return (
        edge_index,
        original_edge_index,
        original_edge_times,
        skipped,
    )


def main():
    nodes = read_csv(NODES_FILE)
    edge_rows = read_csv(EDGES_FILE)

    if not nodes:
        raise RuntimeError("Node file is empty.")

    settlement_ids = [
        str(row["settlement_id"]).strip()
        for row in nodes
    ]

    if len(settlement_ids) != len(set(settlement_ids)):
        raise RuntimeError(
            "Duplicate settlement IDs found."
        )

    id_to_index = {
        settlement_id: index
        for index, settlement_id in enumerate(settlement_ids)
    }

    raw_x, feature_names = build_feature_matrix(nodes)

    x, normalization_stats = standardize_features(
        raw_x,
        feature_names
    )

    (
        edge_index,
        original_edge_index,
        original_edge_times,
        skipped,
    ) = build_edges(edge_rows, id_to_index)

    n = len(nodes)

    if edge_index.size == 0:
        raise RuntimeError("No valid graph edges.")

    if edge_index.min() < 0 or edge_index.max() >= n:
        raise RuntimeError(
            "Edge index out of node range."
        )

    degrees = np.bincount(
        edge_index[0],
        minlength=n
    )

    np.save(X_FILE, x)
    np.save(EDGE_INDEX_FILE, edge_index)

    np.save(
        DATA_DIR / "graphsage_original_edge_index.npy",
        original_edge_index
    )

    np.save(EDGE_TIME_FILE, original_edge_times)

    with IDS_FILE.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as file:
        writer = csv.writer(file)
        writer.writerow([
            "node_index",
            "settlement_id",
            "settlement_name",
        ])

        for index, row in enumerate(nodes):
            writer.writerow([
                index,
                row["settlement_id"],
                row["settlement_name"],
            ])

    metadata = {
        "num_nodes": n,
        "num_features": x.shape[1],
        "num_message_edges": int(edge_index.shape[1]),
        "num_original_edges": int(
            original_edge_index.shape[1]
        ),
        "feature_names": feature_names,
        "normalization": normalization_stats,
        "distance_cap_km": DISTANCE_CAP_KM,
        "skipped_edges": skipped,
        "transport_index_included": False,
    }

    with METADATA_FILE.open(
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            metadata,
            file,
            ensure_ascii=False,
            indent=2
        )


if __name__ == "__main__":
    main()