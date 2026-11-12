from pathlib import Path
import csv
import json
import math

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "results" / "geo_embeddings"

EMBEDDINGS_FILE = DATA_DIR / "graphsage_embeddings.npy"
FEATURES_FILE = DATA_DIR / "graphsage_x.npy"
IDS_FILE = DATA_DIR / "graphsage_settlement_ids.csv"

NODES_FILE = DATA_DIR / "small_graph_nodes_geo.csv"
INFRA_FILE = DATA_DIR / "small_graph_infrastructure_features.csv"

OUTPUT_PAIRS = DATA_DIR / "graphsage_similar_settlements.csv"
OUTPUT_REPORT = DATA_DIR / "graphsage_embedding_evaluation.json"

TOP_K = 5
SEED = 42


def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(path)

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:
        return list(csv.DictReader(file))


def parse_float(value):
    if value is None:
        return None

    try:
        result = float(value)
    except (ValueError, TypeError):
        return None

    if not math.isfinite(result):
        return None

    return result


def cosine_matrix(matrix):
    matrix = np.asarray(matrix, dtype=np.float64)

    norms = np.linalg.norm(
        matrix,
        axis=1,
        keepdims=True
    )

    norms = np.maximum(norms, 1e-12)

    normalized = matrix / norms

    similarities = normalized @ normalized.T

    return np.clip(similarities, -1.0, 1.0)


def haversine_km(lat1, lon1, lat2, lon2):
    radius = 6371.0088

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)

    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(dlambda / 2) ** 2
    )

    a = min(1.0, max(0.0, a))

    return 2 * radius * math.asin(math.sqrt(a))


def spearman_correlation(a, b):
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)

    if len(a) < 3:
        return None

    def rank(values):
        order = np.argsort(values, kind="stable")
        ranks = np.empty(len(values), dtype=float)

        start = 0

        while start < len(values):
            end = start + 1

            while (
                end < len(values)
                and values[order[end]] == values[order[start]]
            ):
                end += 1

            average = (start + 1 + end) / 2.0
            ranks[order[start:end]] = average

            start = end

        return ranks

    ra = rank(a)
    rb = rank(b)

    if np.std(ra) < 1e-12 or np.std(rb) < 1e-12:
        return None

    return float(np.corrcoef(ra, rb)[0, 1])


def mean_or_none(values):
    if not values:
        return None

    return float(np.mean(values))


def main():

    embeddings = np.load(EMBEDDINGS_FILE)
    features = np.load(FEATURES_FILE)

    id_rows = read_csv(IDS_FILE)
    geo_rows = read_csv(NODES_FILE)
    infra_rows = read_csv(INFRA_FILE)

    n = len(id_rows)

    if embeddings.shape[0] != n:
        raise RuntimeError(
            "Embeddings and ID mapping have different sizes."
        )

    if features.shape[0] != n:
        raise RuntimeError(
            "Features and ID mapping have different sizes."
        )

    if not np.isfinite(embeddings).all():
        raise RuntimeError(
            "Embeddings contain NaN or Inf."
        )

    geo_by_id = {
        str(row["settlement_id"]).strip(): row
        for row in geo_rows
    }

    infra_by_id = {
        str(row["settlement_id"]).strip(): row
        for row in infra_rows
    }

    settlements = []

    for row in id_rows:

        settlement_id = str(
            row["settlement_id"]
        ).strip()

        geo = geo_by_id.get(settlement_id, {})
        infra = infra_by_id.get(settlement_id, {})

        settlements.append({
            "id": settlement_id,
            "name": row["settlement_name"],
            "lat": parse_float(geo.get("lat")),
            "lon": parse_float(geo.get("lon")),
            "transport_index": parse_float(
                geo.get("transport_index")
            ),
            "infra": infra,
        })

    graph_similarity = cosine_matrix(embeddings)
    feature_similarity = cosine_matrix(features)

    np.fill_diagonal(graph_similarity, -np.inf)
    np.fill_diagonal(feature_similarity, -np.inf)

    results = []

    graph_geo_distances = []
    feature_geo_distances = []

    graph_index_differences = []
    feature_index_differences = []

    graph_neighbors = []
    feature_neighbors = []

    for i in range(n):

        graph_top = np.argsort(
            -graph_similarity[i]
        )[:TOP_K]

        feature_top = np.argsort(
            -feature_similarity[i]
        )[:TOP_K]

        graph_neighbors.append(set(graph_top.tolist()))
        feature_neighbors.append(set(feature_top.tolist()))

        source = settlements[i]

        for method, indices, similarity_matrix in (
            ("graphsage", graph_top, graph_similarity),
            ("raw_features", feature_top, feature_similarity),
        ):

            for rank, j in enumerate(indices, start=1):

                target = settlements[int(j)]

                distance = None

                if (
                    source["lat"] is not None
                    and source["lon"] is not None
                    and target["lat"] is not None
                    and target["lon"] is not None
                ):
                    distance = haversine_km(
                        source["lat"],
                        source["lon"],
                        target["lat"],
                        target["lon"]
                    )

                index_difference = None

                if (
                    source["transport_index"] is not None
                    and target["transport_index"] is not None
                ):
                    index_difference = abs(
                        source["transport_index"]
                        - target["transport_index"]
                    )

                if method == "graphsage":
                    if distance is not None:
                        graph_geo_distances.append(distance)

                    if index_difference is not None:
                        graph_index_differences.append(
                            index_difference
                        )
                else:
                    if distance is not None:
                        feature_geo_distances.append(distance)

                    if index_difference is not None:
                        feature_index_differences.append(
                            index_difference
                        )

                results.append({
                    "method": method,
                    "source_id": source["id"],
                    "source_name": source["name"],
                    "rank": rank,
                    "target_id": target["id"],
                    "target_name": target["name"],
                    "cosine_similarity": float(
                        similarity_matrix[i, j]
                    ),
                    "geographic_distance_km": distance,
                    "transport_index_difference": (
                        index_difference
                    ),
                })

    rng = np.random.default_rng(SEED)

    sampled_pairs = set()

    max_pairs = min(
        20000,
        n * (n - 1) // 2
    )

    while len(sampled_pairs) < max_pairs:
        i = int(rng.integers(0, n))
        j = int(rng.integers(0, n))

        if i == j:
            continue

        sampled_pairs.add(tuple(sorted((i, j))))

    graph_scores = []
    feature_scores = []
    index_differences = []

    for i, j in sampled_pairs:

        index_a = settlements[i]["transport_index"]
        index_b = settlements[j]["transport_index"]

        if index_a is None or index_b is None:
            continue

        graph_scores.append(graph_similarity[i, j])
        feature_scores.append(feature_similarity[i, j])

        index_differences.append(
            abs(index_a - index_b)
        )

    graph_correlation = spearman_correlation(
        graph_scores,
        index_differences
    )

    feature_correlation = spearman_correlation(
        feature_scores,
        index_differences
    )

    overlaps = [
        len(graph_neighbors[i] & feature_neighbors[i]) / TOP_K
        for i in range(n)
    ]

    with OUTPUT_PAIRS.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        fieldnames = [
            "method",
            "source_id",
            "source_name",
            "rank",
            "target_id",
            "target_name",
            "cosine_similarity",
            "geographic_distance_km",
            "transport_index_difference",
        ]

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(results)

    report = {
        "num_settlements": n,
        "embedding_dimension": int(
            embeddings.shape[1]
        ),
        "top_k": TOP_K,
        "graphsage": {
            "mean_neighbor_geographic_distance_km":
                mean_or_none(graph_geo_distances),
            "mean_neighbor_transport_index_difference":
                mean_or_none(graph_index_differences),
        },
        "raw_features": {
            "mean_neighbor_geographic_distance_km":
                mean_or_none(feature_geo_distances),
            "mean_neighbor_transport_index_difference":
                mean_or_none(feature_index_differences),
        },
        "mean_top_k_overlap": float(
            np.mean(overlaps)
        ),
        "correlations": {
            "graphsage_similarity_vs_index_difference":
                graph_correlation,
            "raw_feature_similarity_vs_index_difference":
                feature_correlation,
        },
        "notes": [
            "This is descriptive embedding analysis.",
            "It is not a held-out link prediction benchmark.",
            "Geographic distances are straight-line haversine distances.",
            "Lower transport index difference means more similar index values.",
            "A correlation does not establish causal usefulness."
        ]
    }

    with OUTPUT_REPORT.open(
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