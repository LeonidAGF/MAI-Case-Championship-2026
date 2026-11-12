# AGENT.md — ML part

This file covers all ML work in the repository. The repo has no `ml/` folder: the code lives in
`scripts/` (road graph → accessibility metrics → transport index) and `geo-embeddings/` (GraphSAGE
pilot), and its outputs are committed in `graph/`, `clast/`, `data/`, `ml_results/` and
`geo-embeddings/data/`. Read the root [AGENT.md](../AGENT.md) first. The ML README
(`README_ML.md`) linked from the root README does not exist yet; the root README section
«ML-часть» is the current human description.

## What ML owns

- The OSM road graph of the North Caucasus Federal District (СКФО) and its audit.
- Leiden communities of the road network ("transport clusters", `cluster_id`).
- The settlement-to-settlement travel-time matrix (Dijkstra; 3,403 settlements, 11,576,406
  directed pairs; the pilot run reached 99.53 %).
- Closeness, gravity and the provisional transport index (ИТС) for settlements and the 144
  municipalities.
- The index model contract for the backend: the YAML config, optionally an `IndexEngine`, and
  features F01–F30 (see [Contract with the backend](#contract-with-the-backend)).
- GraphSAGE settlement embeddings (pilot).

## Can the pipeline be re-run? Not from a fresh clone

Scripts are numbered by pipeline step, but steps 01–02, 04–09, 12–13 and 16 are not in the repo
(OSM extraction, the "extended" routing graph with `generalized_time_s`, settlement snapping,
`settlements_prepared.parquet`, the travel-time matrix, betweenness). The scripts read hardcoded
paths under `data/processed/...`, `graphs/...` and `results/geo_embeddings/...` relative to the
repo root; those intermediate folders are not committed either. The committed files are flattened
copies of the outputs.

So treat the committed artifacts as the source of truth. Don't regenerate or overwrite them unless
you have the original inputs. When you touch a script, prefer making its paths configurable
(constants at the top or CLI arguments) over adding more hardcoded paths.

| Script | Reads | Writes | Notes |
|---|---|---|---|
| `scripts/03_build_graph.py` | `data/processed/roads/north-caucasus-fed-district/{ways,nodes}.parquet` | `graphs/base/v1/{nodes,edges}.parquet`, `build_manifest.json` | Base directed graph: haversine length, speed from `maxspeed` or a default per `highway` class, oneway handling; scratch SQLite `build_work.sqlite` |
| `scripts/10_check_graph.py` | `graphs/routing/v1/extended/{nodes,edges}.parquet`, `data/processed/settlements/snapped/v1/extended.parquet` | `node_graph_features.parquet`, `graph_audit_report.json` | Degrees and weak components (scipy); edges need `generalized_time_s` |
| `scripts/11_cluster_roads_leiden.py` | routing edges + `node_graph_features.parquet` | `node_clusters.parquet`, `cluster_sizes.csv`, `clustering_report.json` | Leiden (RBConfiguration, resolution 1.0, seed 42) on the largest weak component, undirected and unweighted |
| `scripts/14_calculate_closeness.py` | `travel_times_pilot.parquet`, `settlements_prepared.parquet` | `settlement_closeness.{parquet,csv}` + report | Requires a complete n·(n−1) matrix |
| `scripts/15_calculate_gravity.py` | same + `settlements.parquet` (population) | `settlement_gravity_observed.{parquet,csv}` + report | Requires exactly 3,403 settlements |
| `scripts/17_calculate_index.py` | closeness + gravity | `settlement_index_provisional.{parquet,csv}`, `settlement_ranking.csv`, `index_report.json` | Weights 0.5 / 0.5, status `provisional` |
| `scripts/18_aggregate_municipal_index.py` | index + `settlements_working.parquet` | `municipal_index_provisional.*`, `municipal_ranking.csv`, `municipal_issues.csv`, report | Unweighted mean, plus a population-weighted mean over known population |
| `geo-embeddings/scripts/07_prepare_graphsage_data.py` | `results/geo_embeddings/small_graph_{infrastructure_features,edges}.csv` | `graphsage_x.npy`, `graphsage_edge_index.npy`, ids, metadata | 20 node features, see below |
| `geo-embeddings/scripts/08_train_graphsage.py` | the `.npy` files | embeddings, `graphsage_model.pt`, training metrics | CPU torch |
| `geo-embeddings/scripts/09_evaluate_embeddings.py` | embeddings + geo/infra CSVs | `graphsage_similar_settlements.csv`, `graphsage_embedding_evaluation.json` | These outputs are not committed |

## Committed artifacts

| Path | Rows | Content |
|---|---|---|
| `graph/nodes.parquet` | 2,234,894 | `node_id, lon, lat` |
| `graph/edges.parquet` (Git LFS, ~150 MB) | 4,625,149 directed | The extended routing graph, not 03's base graph: `u, v, osm_way_id, length_m, travel_time_s, speed_kmh, highway, oneway, surface, access, …, road_class, surface_group, surface_factor, access_status, speed_is_estimated, generalized_time_s` |
| `data/node_graph_features.parquet` | 2,234,894 | output of step 10 |
| `data/transport_node_types.parquet` | 2,234,894 | `node_type, is_important, is_settlement_anchor, unique_neighbor_count` (the script that produced it is not in the repo) |
| `clast/node_clusters.parquet` | 2,187,952 | `node_id → cluster_id`. The largest weak component; 1,078 clusters of 231–6,900 nodes |
| `data/settlements.parquet` | 3,467 | OSM places: `osm_id, name, name_ru, place, lon, lat, population, population_source, wikidata` |
| `data/settlement_population.parquet` | 3,467 | `population_status`: `observed_osm` (1,561) or `missing` (1,906) |
| `data/settlements_working.parquet` | 3,403 | Working sample: `settlement_osm_id, node_id, distance_m, snap_status (near/review/far), municipality_osm_relation_id, disconnected_from_main, snap_needs_review, …` |
| `data/municipalities.parquet` | 144 | Polygons as WKB (EPSG:4326) and `official_status`; `oktmo` is empty for all 144 (the backend fills it with `load-stats --learn-oktmo`) |
| `data/settlement_{connectivity,graph_snaps,municipality_assignments}.parquet` | 3,467 | Snapping and assignment details |
| `ml_results/settlement_closeness.parquet` | 3,403 | step 14 (`settlement_id` is the OSM node id as a string, plus Leiden `cluster_id`) |
| `ml_results/settlement_gravity_observed.csv` | 3,403 | step 15 |
| `ml_results/settlement_index_provisional.csv` | 3,403 | step 17 (`transport_index` ranges 0–26 on the 0–100 scale) |
| `ml_results/municipal_index_provisional.parquet` | 144 | step 18 |
| `geo-embeddings/data/*` | 500 nodes | GraphSAGE inputs, embeddings (500 × 32), model, metrics |

Facts to keep consistent across docs and code:

- 3,403 settlements are scored out of 3,467 OSM places. Population is known (`observed_osm`) for
  1,545 of them: 1,540 are positive and sum to about 8.55 M (СКФО has about 10.3 M).
- 16 settlements are disconnected from the main road component. Median snap distance is 40 m, p99
  is 360 m, and 12 settlements are farther than 1 km from the graph.
- 912 Leiden clusters contain settlements; 3,387 settlements have a cluster and 16 don't.

## Formulas (current, provisional)

- Closeness: `closeness_raw_i = Σ_reachable 1 / (1 + t_ij / 3600 s) / (n − 1)`; unreachable pairs
  count as 0.
- Gravity, half-life H ∈ {30, 60, 120} min, known positive population only:
  `G_i(H) = Σ_j pop_j · exp(−ln 2 · t_ij[min] / H) / Σ_j pop_j`.
- Transport index: `I_i = 100 · (0.5 · closeness_raw_i + 0.5 · G_i(60))`. The planned model adds
  betweenness with weights 0.4 / 0.4 / 0.2; those weights are a hypothesis, and betweenness is not
  computed yet.
- Municipal index: the mean, median, min, max and stddev of the settlement indices, plus a
  population-weighted mean over settlements with known population.
- GraphSAGE features: log1p counts of school, kindergarten, hospital and clinic within 5, 10 and
  20 km; log1p distance to the nearest of each (capped at 100 km) plus a missing flag; all
  z-scored. The transport index is not a feature.
- GraphSAGE model: 2 layers (row-normalized dense adjacency = mean aggregation, concat with self,
  linear), hidden 64, embedding 32, dropout 0.2, Adam (lr 1e-3, weight decay 1e-4), up to 300
  epochs with patience 40, seed 42. It is trained on link prediction with balanced sampled
  negatives and a 70/15/15 edge split; the adjacency is built from training edges only. Best epoch
  is 219; test AUC is 0.950 and AP is 0.944. These metrics don't measure recommendation quality.
  The dense N×N adjacency and the O(N²) negative enumeration won't scale far beyond the 500-node
  pilot.

## Contract with the backend

The backend (`backend/`) only reads ML files. Change these interfaces together with the backend
code, or tell the backend owner:

1. **Territory inputs**: `data/municipalities.parquet`, `data/settlements_working.parquet` and
   `data/settlements.parquet` are read by `backend/app/etl/territories.py`. Keep their column names
   and keys (`osm_relation_id`, `settlement_osm_id`, `osm_id`). The backend's integration tests
   expect 7 / 144 / 3,403 territories and clusters A=26, B=180, V=3197.
2. **Features F01–F30**: delivered as parquet or CSV in `data/`, loaded by
   `python -m app.cli load-features`. Long format is
   `<key>, feature_code, raw_value[, mode, as_of, imputed]`; wide format is `<key>, F01, F01@foot, …`.
   Keys are `settlement_osm_id`, `osm_id`, `municipality_osm_relation_id`, `region_code`,
   `external_id` or `territory_id`. Mark imputed values with `imputed=true`.
3. **Model config**: a YAML with the schema of `backend/config/model.default.yaml` (subindices,
   features with direction, weight, level and `time_based`, clusters with normalization params,
   presets), validated by `backend/app/modelcfg.py` with `extra="forbid"`. Point `MODEL_CONFIG_PATH`
   at it. Exactly S1–S6 count toward the total; S7 is the attractiveness badge.
4. **Optional engine**: a class implementing `backend/app/scoring/contract.py::IndexEngine`, enabled
   with `SCORING_ENGINE=module:Class`. Run `backend/tests/unit/test_engine_contract.py` against it.
5. **POI, travel-time matrix, OSRM** for exact what-if: `load-poi`, `load-travel-times`,
   `OSRM_CAR_URL` / `OSRM_FOOT_URL`.

The backend does not use `ml_results/*` or `geo-embeddings/data/*` yet, and Docker mounts only
`data/` into the API container. To expose ML results in the product, add a loader in `backend/app/etl/`
rather than reading these files from request handlers.

## Conventions

- One standalone script per pipeline step, named `NN_verb_object.py`, with a `main()` and constants
  at the top. Python, DuckDB SQL for large joins, pyarrow for parquet, seed 42 everywhere.
- Validate inputs strictly and fail loudly: expected counts, duplicates, NaN or Inf, value ranges.
  Write a JSON report next to every output. Messages may be in Russian (existing scripts mix both).
- Mark unvalidated results `provisional` (column `index_status`) until there is a quantitative
  validation.
- Never mix the two kinds of cluster: the backend's comparability clusters A/B/V (city / town /
  village, used to normalize the life index) and the ML Leiden `cluster_id` (road-network
  communities).

## Environment

- No requirements file exists. The scripts use pyarrow, duckdb, numpy, pandas, scipy,
  python-igraph, leidenalg and CPU torch. `backend/.venv` has pyarrow and numpy but not pandas or
  duckdb; create a separate venv for ML work and don't add ML dependencies to `backend/pyproject.toml`.
- The full graph is large (4.6 M edges): reading `graph/edges.parquet` into pandas needs several GB
  of RAM. Read only the columns you need (`pq.read_table(path, columns=[...])`).
- New files larger than about 50 MB go through Git LFS (see `.gitattributes`).
