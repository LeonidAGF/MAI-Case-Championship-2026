"""Command line: migrations, seeding, ETL loaders, reports, OpenAPI export, chat eval.

    python -m app.cli migrate
    python -m app.cli seed [--demo-features]
    python -m app.cli load-features path/to/features.parquet
    python -m app.cli --help
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.core.config import BACKEND_DIR, get_settings


def _print(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    sys.stdout.flush()


def migrate(revision: str = "head", downgrade: bool = False) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    if downgrade:
        command.downgrade(cfg, revision)
    else:
        command.upgrade(cfg, revision)


async def _in_transaction(kind: str, source: str, fn: Callable[[Any], Awaitable[Any]]) -> Any:
    from app.db.session import make_engine, make_sessionmaker
    from app.etl.runs import record_failure

    settings = get_settings()
    engine = make_engine(settings, pool=False)
    sessionmaker = make_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            try:
                result = await fn(session)
                await session.commit()
                return result
            except Exception as exc:
                await session.rollback()
                async with sessionmaker() as s2:
                    await record_failure(s2, kind, source, repr(exc))
                    await s2.commit()
                raise
    finally:
        await engine.dispose()


def _cfg():  # noqa: ANN202
    from app.modelcfg import load_model_config

    return load_model_config(get_settings().model_config_path)


async def cmd_seed_reference(_: argparse.Namespace) -> Any:
    from app.etl.reference import seed_reference

    return await _in_transaction("reference", "model config", lambda s: seed_reference(s, _cfg()))


async def cmd_load_territories(args: argparse.Namespace) -> Any:
    from app.etl.runs import finish_run, start_run
    from app.etl.territories import load_territories

    settings = get_settings()
    data_dir = Path(args.data_dir) if args.data_dir else settings.data_dir

    async def run(session):  # noqa: ANN001, ANN202
        run_id = await start_run(session, "territories", str(data_dir))
        stats = await load_territories(session, data_dir=data_dir, seeds_dir=settings.seeds_dir)
        await finish_run(session, run_id, stats)
        return {"etl_run_id": run_id, **stats}

    return await _in_transaction("territories", str(data_dir), run)


async def cmd_seed(args: argparse.Namespace) -> Any:
    out = {"reference": await cmd_seed_reference(args), "territories": await cmd_load_territories(args)}
    if args.demo_features:
        out["demo_features"] = await cmd_demo(args)
    return out


async def cmd_demo(_: argparse.Namespace) -> Any:
    from app.etl.demo import seed_demo_features

    sys.stderr.write("WARNING: loading SYNTHETIC demo features (source='synthetic-demo'). Not real data!\n")
    return await _in_transaction("demo-features", "synthetic", seed_demo_features)


async def cmd_clear_demo(_: argparse.Namespace) -> Any:
    from app.etl.demo import clear_demo_features

    return await _in_transaction("demo-features-clear", "synthetic", clear_demo_features)


async def cmd_load_features(args: argparse.Namespace) -> Any:
    from app.etl.features import load_features

    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
    return await _in_transaction(
        "features", args.file,
        lambda s: load_features(s, Path(args.file), as_of=as_of, source=args.source, replace=args.replace,
                                default_mode=args.mode),
    )


async def cmd_load_poi(args: argparse.Namespace) -> Any:
    from app.etl.poi import load_poi

    return await _in_transaction("poi", args.file, lambda s: load_poi(s, Path(args.file), source=args.source, replace=args.replace))


async def cmd_load_travel_times(args: argparse.Namespace) -> Any:
    from app.etl.travel_times import load_travel_times

    return await _in_transaction("travel_times", args.file, lambda s: load_travel_times(s, Path(args.file), replace=args.replace))


async def cmd_load_stats(args: argparse.Namespace) -> Any:
    from app.etl.stats import load_stats

    return await _in_transaction(
        "stats", args.file,
        lambda s: load_stats(s, Path(args.file), feature=args.feature, layout=args.layout, indicator=args.indicator,
                             period=args.period, year=args.year, scale=args.scale, learn_oktmo=args.learn_oktmo,
                             dry_run=args.dry_run, source=args.source),
    )


async def cmd_load_od(args: argparse.Namespace) -> Any:
    from app.etl.od_matrix import load_od_matrix

    return await _in_transaction(
        "od_matrix", args.file,
        lambda s: load_od_matrix(s, Path(args.file), unit=args.unit, out=Path(args.out) if args.out else None,
                                 to_features=args.to_features),
    )


async def cmd_load_knowledge(args: argparse.Namespace) -> Any:
    from app.chat.retriever import make_embedder
    from app.etl.knowledge import load_knowledge

    embedder = make_embedder(get_settings().embeddings_model) if args.embed else None
    if args.embed and embedder is None:
        sys.stderr.write("embeddings unavailable (set EMBEDDINGS_MODEL, pip install -e .[embeddings]); using FTS only\n")
    return await _in_transaction(
        "knowledge", args.file,
        lambda s: load_knowledge(s, Path(args.file), source=args.source, replace=args.replace, embedder=embedder),
    )


async def cmd_coverage(_: argparse.Namespace) -> Any:
    from app.etl.coverage import coverage_report

    settings = get_settings()
    return await _in_transaction("coverage", "report", lambda s: coverage_report(s, _cfg(), seeds_dir=settings.seeds_dir))


def cmd_export_openapi(args: argparse.Namespace) -> Any:
    from app.main import create_app

    spec = create_app().openapi()
    out = Path(args.out)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"written": str(out), "paths": len(spec.get("paths", {}))}


async def cmd_chat_eval(args: argparse.Namespace) -> Any:
    import yaml

    from app.container import build_container

    if not args.yes:
        raise SystemExit("chat-eval calls the LLM API (costs money). Re-run with --yes to confirm.")
    questions = yaml.safe_load(Path(args.questions).read_text(encoding="utf-8"))
    container = build_container(get_settings())
    report = []
    try:
        for q in questions:
            profile = container.profiles.resolve(q.get("profile"))
            answer, tools, done, errors = [], [], {}, []
            async for chunk in container.chat.stream([{"role": "user", "content": q["question"]}], profile):
                event, _, data = chunk.partition("\ndata: ")
                payload = json.loads(data)
                name = event.removeprefix("event: ")
                if name == "delta":
                    answer.append(payload["text"])
                elif name == "tool_call":
                    tools.append(payload["name"])
                elif name == "done":
                    done = payload
                elif name == "error":
                    errors.append(payload)
            grounding = done.get("grounding", {})
            report.append({
                "id": q["id"], "question": q["question"], "tools": tools,
                "expected_tools_used": all(t in tools for t in q.get("expect_tools", [])),
                "grounded": grounding.get("ok", False), "ungrounded": grounding.get("ungrounded"),
                "errors": errors, "answer": "".join(answer),
            })
    finally:
        await container.close()
    ok = all(r["grounded"] and not r["errors"] for r in report)
    return {"passed": ok, "grounded": sum(r["grounded"] for r in report), "total": len(report), "results": report}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.cli", description="Atlas SKFO backend tools")
    sub = p.add_subparsers(dest="command", required=True)

    m = sub.add_parser("migrate", help="alembic upgrade (or --downgrade) to a revision")
    m.add_argument("--revision", default="head")
    m.add_argument("--downgrade", action="store_true")

    s = sub.add_parser("seed", help="reference tables + territories (idempotent, 'make seed')")
    s.add_argument("--data-dir")
    s.add_argument("--demo-features", action="store_true", help="also load SYNTHETIC demo features (dev only)")
    sub.add_parser("seed-reference", help="subindices, clusters, features, presets, scenarios from the model YAML")
    lt = sub.add_parser("load-territories", help="regions, municipalities (PostGIS), settlements, clusters")
    lt.add_argument("--data-dir")
    sub.add_parser("seed-demo-features", help="DEV ONLY synthetic feature values")
    sub.add_parser("clear-demo-features", help="remove synthetic feature values")

    f = sub.add_parser("load-features", help="ML feature export (long/wide, parquet/CSV)")
    f.add_argument("file")
    f.add_argument("--as-of")
    f.add_argument("--source")
    f.add_argument("--mode", default="any", choices=["any", "car", "foot"])
    f.add_argument("--replace", action="store_true", help="delete previous values of the features in the file")

    poi = sub.add_parser("load-poi", help="POI (GeoJSON / CSV / parquet) + binding to municipalities")
    poi.add_argument("file")
    poi.add_argument("--source")
    poi.add_argument("--replace", action="store_true")

    tt = sub.add_parser("load-travel-times", help="territory -> POI OSRM matrix")
    tt.add_argument("file")
    tt.add_argument("--replace", action="store_true")

    st = sub.add_parser("load-stats", help="ЕМИСС / Росстат БДМО / ФНС / wide tables -> feature")
    st.add_argument("file")
    st.add_argument("--feature", required=True, help="feature code, e.g. F23")
    st.add_argument("--layout", default="auto", choices=["auto", "long", "wide"])
    st.add_argument("--indicator", help="substring of the indicator name (long layout)")
    st.add_argument("--period", help="substring of the period (e.g. 'год')")
    st.add_argument("--year", type=int)
    st.add_argument("--scale", type=float, default=1.0, help="multiply values (unit conversion)")
    st.add_argument("--learn-oktmo", action="store_true", help="store OKTMO of matched municipalities")
    st.add_argument("--source")
    st.add_argument("--dry-run", action="store_true")

    od = sub.add_parser("load-od-matrix", help="«Точно» inter-city travel-time matrix")
    od.add_argument("file")
    od.add_argument("--unit", choices=["minutes", "hours"])
    od.add_argument("--out", help="write normalized pairs CSV for ML")
    od.add_argument("--to-features", action="store_true", help="write F14/F21 for SKFO cities")

    kn = sub.add_parser("load-knowledge", help="chat knowledge base (md/txt/jsonl/csv)")
    kn.add_argument("file")
    kn.add_argument("--source")
    kn.add_argument("--replace", action="store_true")
    kn.add_argument("--embed", action="store_true")

    sub.add_parser("coverage", help="data completeness report (JSON)")
    oa = sub.add_parser("export-openapi", help="write the OpenAPI contract for the frontend")
    oa.add_argument("--out", default=str(BACKEND_DIR / "openapi.json"))
    ce = sub.add_parser("chat-eval", help="10 control questions: grounding check (calls the LLM API)")
    ce.add_argument("--questions", default=str(BACKEND_DIR / "app" / "chat" / "control_questions.yaml"))
    ce.add_argument("--yes", action="store_true")
    return p


ASYNC_COMMANDS = {
    "seed": cmd_seed,
    "seed-reference": cmd_seed_reference,
    "load-territories": cmd_load_territories,
    "seed-demo-features": cmd_demo,
    "clear-demo-features": cmd_clear_demo,
    "load-features": cmd_load_features,
    "load-poi": cmd_load_poi,
    "load-travel-times": cmd_load_travel_times,
    "load-stats": cmd_load_stats,
    "load-od-matrix": cmd_load_od,
    "load-knowledge": cmd_load_knowledge,
    "coverage": cmd_coverage,
    "chat-eval": cmd_chat_eval,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "migrate":
        migrate(args.revision, args.downgrade)
        return 0
    if args.command == "export-openapi":
        _print(cmd_export_openapi(args))
        return 0
    if args.command == "seed" and not hasattr(args, "data_dir"):
        args.data_dir = None
    result = asyncio.run(ASYNC_COMMANDS[args.command](args))
    _print(result)
    if args.command == "chat-eval" and not result.get("passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
