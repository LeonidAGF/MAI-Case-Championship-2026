from pathlib import Path
import json
import duckdb

ROOT = Path(__file__).resolve().parent.parent

BASE = ROOT / "data/processed/accessibility/v1"
INPUT = BASE / "travel_times_pilot.parquet"
SETTLEMENTS = BASE / "settlements_prepared.parquet"

OUTPUT_PARQUET = BASE / "settlement_closeness.parquet"
OUTPUT_CSV = BASE / "settlement_closeness.csv"
OUTPUT_REPORT = BASE / "closeness_report.json"


def sql_path(path):
    return "'" + str(path).replace("'", "''") + "'"


def main():
    con = duckdb.connect()
    con.execute("SET threads = 4")

    con.execute(f"""
        CREATE VIEW settlements AS
        SELECT
            settlement_id,
            settlement_name,
            node_id,
            cluster_id
        FROM read_parquet({sql_path(SETTLEMENTS)})
    """)

    con.execute(f"""
        CREATE VIEW routes AS
        SELECT
            origin_id,
            destination_id,
            TRY_CAST(travel_time_s AS DOUBLE) AS travel_time_s,
            TRY_CAST(reachable AS BOOLEAN) AS reachable
        FROM read_parquet({sql_path(INPUT)})
    """)

    n = con.execute(
        "SELECT COUNT(*) FROM settlements"
    ).fetchone()[0]

    route_count = con.execute(
        "SELECT COUNT(*) FROM routes"
    ).fetchone()[0]

    expected = n * (n - 1)

    if n < 2:
        raise ValueError("Недостаточно поселений")

    if route_count != expected:
        raise ValueError(
            "Матрица неполная или содержит лишние строки"
        )

    errors = con.execute("""
        SELECT
            COUNT(*) FILTER (
                WHERE origin_id = destination_id
            ) AS self_routes,

            COUNT(*) FILTER (
                WHERE reachable IS NULL
            ) AS invalid_reachable,

            COUNT(*) FILTER (
                WHERE reachable = TRUE
                  AND (
                    travel_time_s IS NULL
                    OR NOT isfinite(travel_time_s)
                    OR travel_time_s < 0
                  )
            ) AS invalid_times,

            COUNT(*) - COUNT(DISTINCT (
                origin_id,
                destination_id
            )) AS duplicates

        FROM routes
    """).fetchone()

    if any(value != 0 for value in errors):
        raise ValueError(
            "Матрица не прошла проверку целостности"
        )

    unknown_ids = con.execute("""
        SELECT COUNT(*)
        FROM routes r
        LEFT JOIN settlements a
            ON r.origin_id = a.settlement_id
        LEFT JOIN settlements b
            ON r.destination_id = b.settlement_id
        WHERE a.settlement_id IS NULL
           OR b.settlement_id IS NULL
    """).fetchone()[0]

    duplicate_settlements = con.execute("""
        SELECT COUNT(*) - COUNT(DISTINCT settlement_id)
        FROM settlements
    """).fetchone()[0]

    if unknown_ids or duplicate_settlements:
        raise ValueError(
            "Обнаружены неизвестные или "
            "повторяющиеся идентификаторы поселений"
        )

    con.execute(f"""
        CREATE TEMP TABLE closeness AS

        WITH metrics AS (
            SELECT
                origin_id AS settlement_id,

                COUNT(*) AS total_destinations,

                COUNT(*) FILTER (
                    WHERE reachable = TRUE
                ) AS reachable_destinations,

                SUM(
                    CASE
                        WHEN reachable = TRUE
                        THEN 1.0 / (
                            1.0 + travel_time_s / 3600.0
                        )
                        ELSE 0.0
                    END
                ) AS accessibility_sum,

                AVG(travel_time_s) FILTER (
                    WHERE reachable = TRUE
                ) / 60.0 AS mean_reachable_time_min

            FROM routes
            GROUP BY origin_id
        )

        SELECT
            s.settlement_id,
            s.settlement_name,
            s.node_id,
            s.cluster_id,

            COALESCE(
                m.reachable_destinations, 0
            ) AS reachable_destinations,

            {n - 1} AS total_destinations,

            COALESCE(
                m.reachable_destinations, 0
            ) * 1.0 / {n - 1}
                AS reachability_ratio,

            m.mean_reachable_time_min,

            COALESCE(
                m.accessibility_sum, 0.0
            ) / {n - 1} AS closeness_raw

        FROM settlements s
        LEFT JOIN metrics m
            ON s.settlement_id = m.settlement_id
    """)

    con.execute("""
        CREATE TEMP TABLE result AS
        SELECT
            *,
            closeness_raw AS closeness_normalized,
            100.0 * closeness_raw AS closeness_score
        FROM closeness
    """)

    stats = con.execute("""
        SELECT
            COUNT(*) AS settlements_count,
            MIN(closeness_raw) AS min_closeness,
            AVG(closeness_raw) AS mean_closeness,
            MAX(closeness_raw) AS max_closeness,
            AVG(reachability_ratio) AS mean_reachability,
            COUNT(*) FILTER (
                WHERE cluster_id IS NULL
            ) AS without_cluster
        FROM result
    """).fetchone()

    keys = [
        "settlements_count",
        "min_closeness",
        "mean_closeness",
        "max_closeness",
        "mean_reachability",
        "without_cluster"
    ]

    report = dict(zip(keys, stats))

    con.execute(f"""
        COPY (
            SELECT *
            FROM result
            ORDER BY settlement_id
        )
        TO {sql_path(OUTPUT_PARQUET)}
        (FORMAT PARQUET)
    """)

    con.execute(f"""
        COPY (
            SELECT *
            FROM result
            ORDER BY settlement_id
        )
        TO {sql_path(OUTPUT_CSV)}
        (HEADER, DELIMITER ',')
    """)

    with OUTPUT_REPORT.open(
        "w", encoding="utf-8"
    ) as file:
        json.dump(
            report,
            file,
            ensure_ascii=False,
            indent=2
        )

    con.close()


if __name__ == "__main__":
    main()