from pathlib import Path
import json
import duckdb

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data/processed/accessibility/v1"

ROUTES = BASE / "travel_times_pilot.parquet"
SETTLEMENTS = BASE / "settlements_prepared.parquet"
POPULATION = (
    ROOT / "data/processed/settlements/settlements.parquet"
)

OUT_PARQUET = BASE / "settlement_gravity_observed.parquet"
OUT_CSV = BASE / "settlement_gravity_observed.csv"
OUT_REPORT = BASE / "gravity_observed_report.json"


def sql_path(path):
    return "'" + str(path).replace("'", "''") + "'"


def main():
    for path in (ROUTES, SETTLEMENTS, POPULATION):
        if not path.exists():
            raise FileNotFoundError(path)

    con = duckdb.connect()
    con.execute("SET threads = 4")

    con.execute(f"""
        CREATE VIEW settlements AS
        SELECT
            CAST(settlement_id AS BIGINT) AS settlement_id,
            settlement_name,
            node_id,
            cluster_id
        FROM read_parquet({sql_path(SETTLEMENTS)})
    """)

    con.execute(f"""
        CREATE VIEW population_source AS
        SELECT
            osm_id AS settlement_id,
            population
        FROM read_parquet({sql_path(POPULATION)})
    """)

    con.execute(f"""
        CREATE VIEW routes AS
        SELECT
            CAST(origin_id AS BIGINT) AS origin_id,
            CAST(destination_id AS BIGINT) AS destination_id,
            TRY_CAST(travel_time_s AS DOUBLE) AS travel_time_s,
            TRY_CAST(reachable AS BOOLEAN) AS reachable
        FROM read_parquet({sql_path(ROUTES)})
    """)

    con.execute("""
        CREATE TEMP TABLE settlement_population AS
        SELECT
            s.*,
            CASE
                WHEN p.population > 0
                THEN CAST(p.population AS DOUBLE)
                ELSE NULL
            END AS population_known
        FROM settlements s
        LEFT JOIN population_source p
            ON s.settlement_id = p.settlement_id
    """)

    n, known, total_population = con.execute("""
        SELECT
            COUNT(*),
            COUNT(population_known),
            SUM(population_known)
        FROM settlement_population
    """).fetchone()

    if n != 3403:
        raise ValueError(
            "Количество поселений отличается от ожидаемого"
        )

    if known == 0:
        raise ValueError("Нет данных о населении")

    actual = con.execute("""
        SELECT COUNT(*) FROM routes
    """).fetchone()[0]

    expected = n * (n - 1)

    if actual != expected:
        raise ValueError("Матрица маршрутов неполная")

    errors = con.execute("""
        SELECT
            COUNT(*) FILTER (
                WHERE origin_id = destination_id
                   OR reachable IS NULL
                   OR (
                       reachable = TRUE
                       AND (
                           travel_time_s IS NULL
                           OR NOT isfinite(travel_time_s)
                           OR travel_time_s < 0
                       )
                   )
            ),
            COUNT(*) - COUNT(DISTINCT (
                origin_id, destination_id
            ))
        FROM routes
    """).fetchone()

    if any(errors):
        raise ValueError(
            f"Ошибки матрицы маршрутов: {errors}"
        )

    con.execute("""
        CREATE TEMP TABLE route_population AS
        SELECT
            r.origin_id,
            r.destination_id,
            r.travel_time_s,
            r.reachable,
            p.population_known
        FROM routes r
        LEFT JOIN settlement_population p
            ON r.destination_id = p.settlement_id
    """)

    matched = con.execute("""
        SELECT COUNT(*)
        FROM route_population rp
        JOIN settlement_population s
            ON rp.destination_id = s.settlement_id
    """).fetchone()[0]

    if matched != expected:
        raise ValueError(
            "Не все пункты назначения сопоставлены"
        )

    con.execute("""
        CREATE TEMP TABLE gravity_metrics AS

        SELECT
            origin_id,

            COUNT(*) FILTER (
                WHERE population_known IS NOT NULL
            ) AS known_destination_count,

            SUM(population_known)
                AS known_destination_population,

            SUM(
                CASE
                    WHEN population_known IS NOT NULL
                         AND reachable = TRUE
                    THEN population_known
                    ELSE 0.0
                END
            ) AS reachable_known_population,

            SUM(
                CASE
                    WHEN population_known IS NOT NULL
                         AND reachable = TRUE
                    THEN population_known * EXP(
                        -LN(2.0)
                        * (travel_time_s / 60.0)
                        / 30.0
                    )
                    ELSE 0.0
                END
            ) AS gravity_numerator_30,

            SUM(
                CASE
                    WHEN population_known IS NOT NULL
                         AND reachable = TRUE
                    THEN population_known * EXP(
                        -LN(2.0)
                        * (travel_time_s / 60.0)
                        / 60.0
                    )
                    ELSE 0.0
                END
            ) AS gravity_numerator_60,

            SUM(
                CASE
                    WHEN population_known IS NOT NULL
                         AND reachable = TRUE
                    THEN population_known * EXP(
                        -LN(2.0)
                        * (travel_time_s / 60.0)
                        / 120.0
                    )
                    ELSE 0.0
                END
            ) AS gravity_numerator_120

        FROM route_population
        GROUP BY origin_id
    """)

    con.execute(f"""
        CREATE TEMP TABLE result AS
        SELECT
            s.settlement_id,
            s.settlement_name,
            s.node_id,
            s.cluster_id,
            s.population_known,

            m.known_destination_count,
            {n - 1} AS total_destinations,

            m.known_destination_count * 1.0
                / {n - 1}
                AS population_data_coverage,

            m.known_destination_population,
            m.reachable_known_population,

            CASE
                WHEN m.known_destination_population > 0
                THEN m.reachable_known_population
                    / m.known_destination_population
                ELSE NULL
            END AS known_population_reachability,

            m.gravity_numerator_30
                / NULLIF(
                    m.known_destination_population, 0
                ) AS gravity_observed_30,

            m.gravity_numerator_60
                / NULLIF(
                    m.known_destination_population, 0
                ) AS gravity_observed_60,

            m.gravity_numerator_120
                / NULLIF(
                    m.known_destination_population, 0
                ) AS gravity_observed_120

        FROM settlement_population s
        LEFT JOIN gravity_metrics m
            ON s.settlement_id = m.origin_id
    """)

    stats = con.execute("""
        SELECT
            COUNT(*) AS settlements_count,
            MIN(gravity_observed_60) AS min_gravity,
            AVG(gravity_observed_60) AS mean_gravity,
            MAX(gravity_observed_60) AS max_gravity,
            AVG(population_data_coverage)
                AS mean_population_coverage,
            AVG(known_population_reachability)
                AS mean_known_population_reachability,
            COUNT(*) FILTER (
                WHERE gravity_observed_60 IS NULL
            ) AS missing_gravity
        FROM result
    """).fetchone()

    keys = [
        "settlements_count",
        "min_gravity",
        "mean_gravity",
        "max_gravity",
        "mean_population_coverage",
        "mean_known_population_reachability",
        "missing_gravity"
    ]

    report = dict(zip(keys, stats))

    con.execute(f"""
        COPY (
            SELECT *
            FROM result
            ORDER BY settlement_id
        )
        TO {sql_path(OUT_PARQUET)}
        (FORMAT PARQUET)
    """)

    con.execute(f"""
        COPY (
            SELECT *
            FROM result
            ORDER BY settlement_id
        )
        TO {sql_path(OUT_CSV)}
        (HEADER, DELIMITER ',')
    """)

    with OUT_REPORT.open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(
            report,
            f,
            ensure_ascii=False,
            indent=2
        )

    con.close()


if __name__ == "__main__":
    main()