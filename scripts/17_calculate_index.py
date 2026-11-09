from pathlib import Path
import json
import duckdb

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data/processed/accessibility/v1"

CLOSENESS = BASE / "settlement_closeness.parquet"
GRAVITY = BASE / "settlement_gravity_observed.parquet"

OUTPUT = BASE / "index"
OUTPUT.mkdir(parents=True, exist_ok=True)

OUT_PARQUET = OUTPUT / "settlement_index_provisional.parquet"
OUT_CSV = OUTPUT / "settlement_index_provisional.csv"
OUT_RANKING = OUTPUT / "settlement_ranking.csv"
OUT_REPORT = OUTPUT / "index_report.json"

WEIGHT_C = 0.5
WEIGHT_G = 0.5


def sql_path(path):
    return "'" + str(path).replace("'", "''") + "'"


def main():
    for path in (CLOSENESS, GRAVITY):
        if not path.exists():
            raise FileNotFoundError(path)

    con = duckdb.connect()
    con.execute("SET threads = 4")

    con.execute(f"""
        CREATE VIEW closeness AS
        SELECT
            CAST(settlement_id AS VARCHAR) AS settlement_id,
            settlement_name,
            node_id,
            cluster_id,
            closeness_raw,
            reachability_ratio,
            mean_reachable_time_min
        FROM read_parquet({sql_path(CLOSENESS)})
    """)

    con.execute(f"""
        CREATE VIEW gravity AS
        SELECT
            CAST(settlement_id AS VARCHAR) AS settlement_id,
            population_known,
            population_data_coverage,
            known_population_reachability,
            gravity_observed_30,
            gravity_observed_60,
            gravity_observed_120
        FROM read_parquet({sql_path(GRAVITY)})
    """)

    n_c = con.execute(
        "SELECT COUNT(*) FROM closeness"
    ).fetchone()[0]

    n_g = con.execute(
        "SELECT COUNT(*) FROM gravity"
    ).fetchone()[0]

    matched = con.execute("""
        SELECT COUNT(*)
        FROM closeness c
        INNER JOIN gravity g
            ON c.settlement_id = g.settlement_id
    """).fetchone()[0]

    if n_c != 3403 or n_g != 3403 or matched != 3403:
        raise ValueError(
            "Количество поселений или совпадений некорректно"
        )

    for name in ("closeness", "gravity"):
        duplicates = con.execute(f"""
            SELECT COUNT(*) - COUNT(DISTINCT settlement_id)
            FROM {name}
        """).fetchone()[0]

        if duplicates:
            raise ValueError(
                f"Повторяющиеся settlement_id в {name}"
            )

    invalid = con.execute("""
        SELECT COUNT(*)
        FROM closeness c
        JOIN gravity g USING (settlement_id)
        WHERE c.closeness_raw IS NULL
           OR g.gravity_observed_60 IS NULL
           OR NOT isfinite(c.closeness_raw)
           OR NOT isfinite(g.gravity_observed_60)
           OR c.closeness_raw NOT BETWEEN 0 AND 1
           OR g.gravity_observed_60 NOT BETWEEN 0 AND 1
    """).fetchone()[0]

    if invalid:
        raise ValueError(
            f"Некорректных значений компонентов: {invalid}"
        )

    con.execute(f"""
        CREATE TEMP TABLE index_results AS

        SELECT
            c.settlement_id,
            c.settlement_name,
            c.node_id,
            c.cluster_id,

            g.population_known,
            g.population_data_coverage,
            g.known_population_reachability,

            c.reachability_ratio,
            c.mean_reachable_time_min,

            c.closeness_raw,
            g.gravity_observed_60 AS gravity_raw,

            100.0 * (
                {WEIGHT_C} * c.closeness_raw
                + {WEIGHT_G} * g.gravity_observed_60
            ) AS transport_index,

            100.0 * (
                {WEIGHT_C} * c.closeness_raw
                + {WEIGHT_G} * g.gravity_observed_30
            ) AS index_gravity_30,

            100.0 * (
                {WEIGHT_C} * c.closeness_raw
                + {WEIGHT_G} * g.gravity_observed_120
            ) AS index_gravity_120,

            'provisional' AS index_status

        FROM closeness c
        JOIN gravity g USING (settlement_id)
    """)

    con.execute("""
        CREATE TEMP TABLE ranked AS
        SELECT
            ROW_NUMBER() OVER (
                ORDER BY transport_index DESC,
                         settlement_id
            ) AS rank,

            *
        FROM index_results
    """)

    stats = con.execute("""
        SELECT
            COUNT(*) AS settlements,
            MIN(transport_index) AS minimum,
            AVG(transport_index) AS mean,
            MEDIAN(transport_index) AS median,
            MAX(transport_index) AS maximum,
            STDDEV_POP(transport_index) AS stddev,
            COUNT(*) FILTER (
                WHERE transport_index = 0
            ) AS zero_index,
            COUNT(*) FILTER (
                WHERE cluster_id IS NULL
            ) AS without_cluster
        FROM ranked
    """).fetchone()

    keys = [
        "settlements",
        "minimum",
        "mean",
        "median",
        "maximum",
        "stddev",
        "zero_index",
        "without_cluster"
    ]

    report = dict(zip(keys, stats))

    con.execute(f"""
        COPY (
            SELECT *
            FROM ranked
            ORDER BY rank
        )
        TO {sql_path(OUT_PARQUET)}
        (FORMAT PARQUET)
    """)

    con.execute(f"""
        COPY (
            SELECT *
            FROM ranked
            ORDER BY rank
        )
        TO {sql_path(OUT_CSV)}
        (HEADER, DELIMITER ',')
    """)

    con.execute(f"""
        COPY (
            SELECT
                rank,
                settlement_id,
                settlement_name,
                transport_index,
                closeness_raw,
                gravity_raw
            FROM ranked
            ORDER BY rank
        )
        TO {sql_path(OUT_RANKING)}
        (HEADER, DELIMITER ',')
    """)

    report["weights"] = {
        "closeness": WEIGHT_C,
        "gravity": WEIGHT_G
    }
    report["gravity_half_life_minutes"] = 60
    report["index_status"] = "provisional"
    report["population_coverage_note"] = (
        "Gravity uses known positive population only."
    )

    with OUT_REPORT.open("w", encoding="utf-8") as f:
        json.dump(
            report,
            f,
            ensure_ascii=False,
            indent=2
        )

    con.close()


if __name__ == "__main__":
    main()