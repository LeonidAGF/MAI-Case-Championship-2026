"""
The ML road graph for ambulance routing: graph/nodes.parquet and graph/edges.parquet (read-only).

The edge file is the extended routing graph of the ML part (4.6 M directed edges, Git LFS, ~150 MB);
only the columns below are read. Edges keep their direction, so one-way roads stay one-way. Speeds
are the backend's own (app/ambulance/speeds.py): the ML columns speed_kmh and generalized_time_s
are not used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

NODE_COLUMNS = ["node_id", "lon", "lat"]
EDGE_COLUMNS = ["u", "v", "length_m", "highway", "maxspeed", "bridge", "tunnel", "access"]
NOT_A_STRUCTURE = {"no", ""}


@dataclass
class RoadGraph:
    node_ids: np.ndarray  # int64, sorted
    lon: np.ndarray
    lat: np.ndarray
    u: np.ndarray  # int64 node positions
    v: np.ndarray
    length_m: np.ndarray
    highway: np.ndarray  # int32 codes into highway_names, -1 = none
    highway_names: list[str]
    maxspeed: np.ndarray  # int32 codes into maxspeed_names, -1 = no tag
    maxspeed_names: list[str]
    structure: np.ndarray  # bridge or tunnel: SRTM shows the ground, not the deck
    main: np.ndarray  # per node: in the largest weakly connected component
    stats: dict[str, int] = field(default_factory=dict)


def check_graph_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found (GRAPH_DIR must point to the ML graph folder)")
    if path.stat().st_size < 2048 and path.read_bytes()[:40].startswith(b"version https://git-lfs"):
        raise FileNotFoundError(f"{path} is a Git LFS pointer, not the graph: run `git lfs pull`")


def _codes(column) -> tuple[np.ndarray, list[str]]:  # noqa: ANN001 - pyarrow ChunkedArray
    import pyarrow.compute as pc

    encoded = pc.dictionary_encode(column.combine_chunks())
    codes = pc.fill_null(encoded.indices, -1).to_numpy(zero_copy_only=False).astype(np.int32)
    return codes, [str(x) for x in encoded.dictionary.to_pylist()]


def load_graph(graph_dir: Path, *, closed_access: list[str] | tuple[str, ...] = ()) -> RoadGraph:
    import pyarrow.parquet as pq
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    graph_dir = Path(graph_dir)
    nodes_path, edges_path = graph_dir / "nodes.parquet", graph_dir / "edges.parquet"
    check_graph_file(nodes_path)
    check_graph_file(edges_path)

    nodes = pq.read_table(nodes_path, columns=NODE_COLUMNS)
    node_ids = nodes["node_id"].to_numpy()
    if node_ids.size == 0 or np.any(node_ids[1:] <= node_ids[:-1]):
        raise ValueError(f"{nodes_path} must be sorted by unique node_id")
    lon = nodes["lon"].to_numpy().astype(np.float64)
    lat = nodes["lat"].to_numpy().astype(np.float64)
    del nodes
    n = node_ids.size

    edges = pq.read_table(edges_path, columns=EDGE_COLUMNS)
    eu = edges["u"].to_numpy()
    ev = edges["v"].to_numpy()
    u = np.searchsorted(node_ids, eu)
    v = np.searchsorted(node_ids, ev)
    known = (u < n) & (v < n)
    known[known] = (node_ids[u[known]] == eu[known]) & (node_ids[v[known]] == ev[known])
    length = edges["length_m"].to_numpy(zero_copy_only=False).astype(np.float64)
    highway, highway_names = _codes(edges["highway"])
    maxspeed, maxspeed_names = _codes(edges["maxspeed"])
    bridge, bridge_names = _codes(edges["bridge"])
    tunnel, tunnel_names = _codes(edges["tunnel"])
    access, access_names = _codes(edges["access"])
    del edges

    def flag(codes: np.ndarray, names: list[str], values: set[str], *, inside: bool) -> np.ndarray:
        hit = np.array([(name.strip().lower() in values) == inside for name in names] + [False])
        return hit[codes]

    structure = flag(bridge, bridge_names, NOT_A_STRUCTURE, inside=False) | flag(
        tunnel, tunnel_names, NOT_A_STRUCTURE, inside=False
    )
    closed = flag(access, access_names, {a.strip().lower() for a in closed_access}, inside=True)
    keep = known & np.isfinite(length) & (length > 0) & (u != v) & ~closed
    stats = {
        "nodes": int(n),
        "edges": int(eu.size),
        "edges_used": int(keep.sum()),
        "edges_closed": int((known & closed).sum()),
        "edges_invalid": int((~known).sum() + (known & ~((length > 0) & (u != v))).sum()),
    }
    u, v = u[keep].astype(np.int64), v[keep].astype(np.int64)

    adjacency = csr_matrix((np.ones(u.size, dtype=np.int8), (u, v)), shape=(n, n))
    _, labels = connected_components(adjacency, directed=True, connection="weak")
    del adjacency
    main = labels == np.bincount(labels).argmax()
    stats["main_component_nodes"] = int(main.sum())

    return RoadGraph(
        node_ids=node_ids, lon=lon, lat=lat, u=u, v=v, length_m=length[keep],
        highway=highway[keep], highway_names=highway_names, maxspeed=maxspeed[keep], maxspeed_names=maxspeed_names,
        structure=structure[keep], main=main, stats=stats,
    )
