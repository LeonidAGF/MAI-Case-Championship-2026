from pathlib import Path
import json

import duckdb


ROOT = Path(__file__).resolve().parent.parent

ACCESS = ROOT / "data/processed/accessibility/v1"

INDEX_FILE = (
    ACCESS / "index/settlement_index_provisional.parquet"
)

WORKING_FILE = (
    ROOT / "data/processed/analysis/v1/"
    "settlements_working.parquet"
)

OUTPUT = ACCESS / "municipal"
OUTPUT.mkdir(parents=True, exist_ok=True)

OUT_PARQUET = OUTPUT / "municipal_index_provisional.parquet"
OUT_CSV = OUTPUT / "municipal_index_provisional.csv"
OUT_RANKING = OUTPUT / "municipal_ranking.csv"
OUT_ISSUES = OUTPUT / "municipal_issues.csv"
OUT_REPORT = OUTPUT / "municipal_report.json"


def sql_path(path):
    return "'" + str(path).replace("'", "''") + "'"


def main():
    for path in (INDEX_FILE, WORKING_FILE):
        if not path.exists():
            raise FileNotFoundError(path)

    con = duckdb.connect()
    con.execute("SET threads = 4")

    con.execute(f"""
        CREATE VIEW settlement_index AS
        SELECT
            CAST(settlement_id AS VARCHAR) AS settlement_id,
            settlement_name,
            transport_index,
            closeness_raw,
            gravity_raw,
            population_known,
            index_status
        FROM read_parquet({sql_path(INDEX_FILE)})
    """)

    con.execute(f"""
        CREATE VIEW municipality_assignment AS
        SELECT
            CAST(settlement_osm_id AS VARCHAR) AS settlement_id,
            CAST(
                municipality_osm_relation_id AS VARCHAR
            ) AS municipality_id,
            municipality_name,
            municipality_oktmo,
            assignment_status
        FROM read_parquet({sql_path(WORKING_FILE)})
    """)

    n_index = con.execute("""
        SELECT COUNT(*) FROM settlement_index
    """).fetchone()[0]

    n_assignment = con.execute("""
        SELECT COUNT(*) FROM municipality_assignment
    """).fetchone()[0]

    for view in ("settlement_index", "municipality_assignment"):
        duplicate_count = con.execute(f"""
            SELECT COUNT(*) - COUNT(DISTINCT settlement_id)
            FROM {view}
        """).fetchone()[0]

        if duplicate_count:
            raise ValueError(
                f"Дублирующиеся settlement_id в {view}: "
                f"{duplicate_count}"
            )

    con.execute("""
        CREATE TEMP TABLE joined AS
        SELECT
            i.*,
            a.municipality_id,
            a.municipality_name,
            a.municipality_oktmo,
            a.assignment_status
        FROM settlement_index i
        LEFT JOIN municipality_assignment a
            ON i.settlement_id = a.settlement_id
    """)

    matched = con.execute("""
        SELECT COUNT(*)
        FROM joined
        WHERE municipality_id IS NOT NULL
    """).fetchone()[0]

    unmatched = con.execute("""
        SELECT COUNT(*)
        FROM joined
        WHERE municipality_id IS NULL
    """).fetchone()[0]

    con.execute(f"""
        COPY (
            SELECT *
            FROM joined
            WHERE municipality_id IS NULL
               OR municipality_name IS NULL
               OR assignment_status IS NULL
            ORDER BY settlement_name
        )
        TO {sql_path(OUT_ISSUES)}
        (HEADER, DELIMITER ',')
    """)

    con.execute("""
        CREATE TEMP TABLE municipal_results AS
        SELECT
            municipality_id,
            MAX(municipality_name) AS municipality_name,
            MAX(municipality_oktmo) AS municipality_oktmo,

            COUNT(*) AS settlements_count,

            AVG(transport_index) AS mean_index,
            MEDIAN(transport_index) AS median_index,
            MIN(transport_index) AS min_index,
            MAX(transport_index) AS max_index,

            STDDEV_POP(transport_index) AS stddev_index,

            AVG(closeness_raw) * 100.0 AS mean_closeness,
            AVG(gravity_raw) * 100.0 AS mean_gravity,

            COUNT(*) FILTER (
                WHERE transport_index = 0
            ) AS zero_index_count,

            COUNT(*) FILTER (
                WHERE population_known > 0
            ) AS known_population_count,

            COUNT(*) FILTER (
                WHERE population_known IS NULL
                   OR population_known <= 0
            ) AS missing_population_count,

            SUM(
                CASE
                    WHEN population_known > 0
                    THEN population_known
                    ELSE 0
                END
            ) AS known_population_total,

            SUM(
                CASE
                    WHEN population_known > 0
                    THEN transport_index * population_known
                    ELSE 0
                END
            ) /
            NULLIF(
                SUM(
                    CASE
                        WHEN population_known > 0
                        THEN population_known
                        ELSE 0
                    END
                ),
                0
            ) AS population_weighted_index_known,

            COUNT(*) FILTER (
                WHERE population_known > 0
            ) * 1.0 / COUNT(*) AS population_coverage,

            'provisional' AS index_status

        FROM joined
        WHERE municipality_id IS NOT NULL
        GROUP BY municipality_id
    """)

    con.execute("""
        CREATE TEMP TABLE municipal_ranked AS
        SELECT
            ROW_NUMBER() OVER (
                ORDER BY mean_index DESC, municipality_id
            ) AS rank,
            *
        FROM municipal_results
    """)

    stats = con.execute("""
        SELECT
            COUNT(*) AS municipalities,
            SUM(settlements_count) AS assigned_settlements,
            MIN(mean_index) AS min_mean_index,
            AVG(mean_index) AS mean_municipal_index,
            MAX(mean_index) AS max_mean_index,
            COUNT(*) FILTER (
                WHERE known_population_count = 0
            ) AS without_population_data
        FROM municipal_ranked
    """).fetchone()

    keys = [
        "municipalities",
        "assigned_settlements",
        "min_mean_index",
        "mean_municipal_index",
        "max_mean_index",
        "without_population_data"
    ]

    report = dict(zip(keys, stats))
    report["settlements_total"] = n_index
    report["settlements_without_municipality"] = unmatched

    con.execute(f"""
        COPY (
            SELECT *
            FROM municipal_ranked
            ORDER BY rank
        )
        TO {sql_path(OUT_PARQUET)}
        (FORMAT PARQUET)
    """)

    con.execute(f"""
        COPY (
            SELECT *
            FROM municipal_ranked
            ORDER BY rank
        )
        TO {sql_path(OUT_CSV)}
        (HEADER, DELIMITER ',')
    """)

    con.execute(f"""
        COPY (
            SELECT
                rank,
                municipality_id,
                municipality_name,
                municipality_oktmo,
                settlements_count,
                mean_index,
                median_index,
                population_weighted_index_known,
                population_coverage
            FROM municipal_ranked
            ORDER BY rank
        )
        TO {sql_path(OUT_RANKING)}
        (HEADER, DELIMITER ',')
    """)

    report["aggregation_method"] = (
        "unweighted_mean_of_settlement_indices"
    )
    report["index_status"] = "provisional"
    report["population_weighted_note"] = (
        "Population-weighted index uses only "
        "settlements with known positive population."
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