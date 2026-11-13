"""
Command line: migrations, seeding, ETL loaders, reports, OpenAPI export, chat eval.

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


# Real data committed by the ML part (scripts/20_fetch_osm_poi.py, 21_calculate_osm_features.py):
# OpenStreetMap is queried once by those scripts, never by the backend.
OSM_POI_FILE = "osm_poi.csv"
OSM_FEATURES_FILE = "osm_features.csv"


async def cmd_seed_osm(args: argparse.Namespace) -> Any:
    """OSM objects (poi) and the features computed from them; any synthetic values are removed first."""
    from app.etl.demo import clear_demo_features
    from app.etl.features import load_features
    from app.etl.poi import load_poi

    data_dir = Path(args.osm_dir) if getattr(args, "osm_dir", None) else get_settings().osm_data_dir
    poi, features = data_dir / OSM_POI_FILE, data_dir / OSM_FEATURES_FILE
    out: dict[str, Any] = {"synthetic_removed": await _in_transaction("demo-features-clear", "synthetic", clear_demo_features)}
    if poi.exists():
        out["poi"] = await _in_transaction("poi", str(poi), lambda s: load_poi(s, poi, source="osm", replace=True))
    if features.exists():
        out["features"] = await _in_transaction(
            "features", str(features), lambda s: load_features(s, features, source="osm+ml-graph", replace=True)
        )
    if "poi" not in out or "features" not in out:
        out["warning"] = (f"{OSM_POI_FILE} / {OSM_FEATURES_FILE} not found in {data_dir}: run "
                          "scripts/20_fetch_osm_poi.py and scripts/21_calculate_osm_features.py")
    return out


async def cmd_seed(args: argparse.Namespace) -> Any:
    out = {"reference": await cmd_seed_reference(args), "territories": await cmd_load_territories(args)}
    out["osm"] = await cmd_seed_osm(args)
    out["knowledge"] = await cmd_seed_knowledge(args)
    return out


async def cmd_seed_knowledge(_: argparse.Namespace) -> Any:
    from app.chat.catalog import builtin_documents
    from app.chat.retriever import make_embedder
    from app.etl.knowledge import load_documents

    model = get_settings().embeddings_model
    embedder = make_embedder(model)
    if model and embedder is None:
        raise RuntimeError(f"Embedding model {model!r} is configured but unavailable")
    return await _in_transaction(
        "knowledge", "atlas-builtin",
        lambda session: load_documents(
            session, builtin_documents(_cfg()), source="atlas-builtin", replace=True,
            embedder=embedder, file="active model configuration",
        ),
    )


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
        raise RuntimeError("--embed requires EMBEDDINGS_MODEL and the embeddings dependency")
    return await _in_transaction(
        "knowledge", args.file,
        lambda s: load_knowledge(s, Path(args.file), source=args.source, replace=args.replace, embedder=embedder),
    )


def _graph_tiles(graph_dir: Path) -> list[tuple[int, int]]:
    """SRTM tiles under the road graph (from the node coordinates only)."""
    import pyarrow.parquet as pq

    from app.ambulance.dem import tiles_covering
    from app.ambulance.graph import check_graph_file

    nodes = graph_dir / "nodes.parquet"
    check_graph_file(nodes)
    table = pq.read_table(nodes, columns=["lon", "lat"])
    return tiles_covering(table["lon"].to_numpy(), table["lat"].to_numpy())


async def cmd_fetch_srtm(args: argparse.Namespace) -> Any:
    from app.ambulance.dem import fetch_tiles

    settings = get_settings()
    graph_dir = Path(args.graph_dir) if args.graph_dir else settings.graph_dir
    srtm_dir = Path(args.srtm_dir) if args.srtm_dir else settings.srtm_dir
    return await fetch_tiles(
        _graph_tiles(graph_dir), srtm_dir, settings.srtm_url, arcsec=args.arcsec or settings.srtm_arcsec,
        source_dir=Path(args.source_dir) if args.source_dir else None, timeout_s=settings.srtm_timeout_s,
    )


async def cmd_load_ambulance(args: argparse.Namespace) -> Any:
    """Ambulance travel times to all settlements: road graph + OSM max speeds + SRTM height and slope."""
    from app.ambulance.config import load_ambulance_config
    from app.ambulance.dem import fetch_tiles
    from app.etl.ambulance import load_ambulance

    settings = get_settings()
    cfg = load_ambulance_config(settings.ambulance_config_path)
    graph_dir = Path(args.graph_dir) if args.graph_dir else settings.graph_dir
    srtm_dir = None if args.no_terrain else (Path(args.srtm_dir) if args.srtm_dir else settings.srtm_dir)
    out: dict[str, Any] = {}
    if srtm_dir is not None:
        url = "" if args.offline else settings.srtm_url
        out["srtm"] = await fetch_tiles(_graph_tiles(graph_dir), srtm_dir, url, arcsec=settings.srtm_arcsec,
                                        timeout_s=settings.srtm_timeout_s)
    report = settings.osm_data_dir / "osm_poi_report.json"
    osm_base = json.loads(report.read_text(encoding="utf-8")).get("osm_base") if report.exists() else None
    out["ambulance"] = await _in_transaction(
        "ambulance", str(graph_dir),
        lambda s: load_ambulance(s, cfg, graph_dir=graph_dir, dem_dir=srtm_dir, osm_base=osm_base),
    )
    return out


# --- organisations, API keys and payments (/v1 data API) -----------------------------------
async def _db(fn: Callable[[Any], Awaitable[Any]]) -> Any:
    """One session for a team command; committed when the command succeeds."""
    from app.db.session import make_engine, make_sessionmaker

    engine = make_engine(get_settings(), pool=False)
    try:
        async with make_sessionmaker(engine)() as session:
            result = await fn(session)
            await session.commit()
            return result
    finally:
        await engine.dispose()


def _expiry(args: argparse.Namespace) -> dt.datetime | None:
    from app.partners.keys import MSK, end_of_day

    if getattr(args, "expires", None):
        return end_of_day(dt.date.fromisoformat(args.expires))
    if getattr(args, "days", None):
        return end_of_day(dt.datetime.now(MSK).date() + dt.timedelta(days=args.days))
    return None


async def cmd_org_add(args: argparse.Namespace) -> Any:
    from app.partners.store import add_organisation

    return await _db(lambda s: add_organisation(s, args.name, args.email, args.comment or ""))


async def cmd_org_list(_: argparse.Namespace) -> Any:
    from app.partners.store import list_organisations

    return await _db(list_organisations)


async def cmd_org_disable(args: argparse.Namespace) -> Any:
    from app.partners.store import set_organisation_active

    return await _db(lambda s: set_organisation_active(s, args.org, False))


async def cmd_org_enable(args: argparse.Namespace) -> Any:
    from app.partners.store import set_organisation_active

    return await _db(lambda s: set_organisation_active(s, args.org, True))


async def cmd_key_issue(args: argparse.Namespace) -> Any:
    from app.partners.keys import email_text
    from app.partners.store import issue_key
    from app.payments.service import issue_key_for_order

    if args.order:
        key, info = await _db(lambda s: issue_key_for_order(s, args.order, name=args.name or ""))
    else:
        key, info = await _db(lambda s: issue_key(
            s, args.org, name=args.name or "", expires_at=_expiry(args), rate_limit_per_minute=args.rate_limit,
            note=args.note or "",
        ))
    text = email_text(info["organisation"], key, get_settings().api_public_url, info["expires_at"])
    return {**info, "api_key": key, "email_text": text}


async def cmd_key_list(args: argparse.Namespace) -> Any:
    from app.partners.store import list_keys

    return await _db(lambda s: list_keys(s, args.org))


async def cmd_key_revoke(args: argparse.Namespace) -> Any:
    from app.partners.store import revoke_key

    return await _db(lambda s: revoke_key(s, args.prefix))


async def cmd_key_extend(args: argparse.Namespace) -> Any:
    from app.partners.store import extend_key

    return await _db(lambda s: extend_key(s, args.prefix, _expiry(args)))


async def cmd_order_create(args: argparse.Namespace) -> Any:
    from app.payments.service import create_order, parse_amount

    details = args.details if args.details is not None else get_settings().payment_details
    return await _db(lambda s: create_order(s, org_ref=args.org, months=args.months, amount=parse_amount(args.amount),
                                            details=details, note=args.note or ""))


async def cmd_order_confirm(args: argparse.Namespace) -> Any:
    import getpass

    from app.payments.service import confirm_order, parse_amount

    by = args.by or getpass.getuser()
    return await _db(lambda s: confirm_order(s, args.order, received=parse_amount(args.received),
                                             reference=args.reference or "", confirmed_by=by))


async def cmd_order_cancel(args: argparse.Namespace) -> Any:
    from app.payments.service import cancel_order

    return await _db(lambda s: cancel_order(s, args.order, reason=args.reason or ""))


async def cmd_order_list(args: argparse.Namespace) -> Any:
    from app.payments.service import list_orders

    return await _db(lambda s: list_orders(s, args.org, args.status))


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


async def cmd_chat_check(_: argparse.Namespace) -> Any:
    from app.chat import llm

    return await llm.check(get_settings())


async def cmd_egress_check(_: argparse.Namespace) -> Any:
    from app.chat import llm

    return await llm.egress_check(get_settings())


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

    s = sub.add_parser("seed", help="reference tables + territories + OSM objects and features (idempotent, 'make seed')")
    s.add_argument("--data-dir")
    s.add_argument("--osm-dir", help="folder with osm_poi.csv / osm_features.csv (default OSM_DATA_DIR)")
    sub.add_parser("seed-reference", help="subindices, clusters, features, presets, scenarios from the model YAML")
    lt = sub.add_parser("load-territories", help="regions, municipalities (PostGIS), settlements, clusters")
    lt.add_argument("--data-dir")
    so = sub.add_parser("seed-osm", help="OSM objects + features (osm_poi.csv / osm_features.csv in OSM_DATA_DIR), synthetic removed")
    so.add_argument("--osm-dir", help="folder with osm_poi.csv / osm_features.csv (default OSM_DATA_DIR)")
    sub.add_parser("clear-demo-features", help="remove synthetic feature values left by older versions")

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
    sub.add_parser("seed-knowledge", help="replace the model-methodology knowledge catalog with vector embeddings")

    fs = sub.add_parser("fetch-srtm", help="add the missing SRTM elevation tiles under the road graph to SRTM_DIR")
    fs.add_argument("--graph-dir", help="folder with nodes.parquet (default GRAPH_DIR)")
    fs.add_argument("--srtm-dir", help="tile folder (default SRTM_DIR)")
    fs.add_argument("--arcsec", type=int, choices=[1, 3], help="resolution to store (default SRTM_ARCSEC = 3)")
    fs.add_argument("--source-dir", help="take the tiles from this local folder instead of downloading them")
    la = sub.add_parser(
        "load-ambulance",
        help="ambulance travel times to all settlements: road graph, OSM max speeds, SRTM height and slope",
    )
    la.add_argument("--graph-dir", help="folder with nodes.parquet / edges.parquet (default GRAPH_DIR)")
    la.add_argument("--srtm-dir", help="SRTM tile folder (default SRTM_DIR)")
    la.add_argument("--offline", action="store_true", help="do not download missing SRTM tiles")
    la.add_argument("--no-terrain", action="store_true", help="ignore height and slope (no SRTM)")

    oa_ = sub.add_parser("org-add", help="add an organisation of the /v1 data API (after checking who it is)")
    oa_.add_argument("name")
    oa_.add_argument("--email", required=True, help="the only address keys and payment links are sent to")
    oa_.add_argument("--comment")
    sub.add_parser("org-list", help="organisations with their active keys and last use")
    od_ = sub.add_parser("org-disable", help="cut off all keys of an organisation")
    od_.add_argument("org", help="name or id")
    oe_ = sub.add_parser("org-enable", help="give access back to a disabled organisation")
    oe_.add_argument("org", help="name or id")
    ki = sub.add_parser("key-issue", help="issue an API key (printed once, with a letter to send)")
    target = ki.add_mutually_exclusive_group(required=True)
    target.add_argument("--org", help="name or id: a key issued by hand")
    target.add_argument("--order", type=int, help="paid order id: the first key, valid for the paid months")
    ki.add_argument("--name", help="what the key is for, e.g. the organisation's system")
    until = ki.add_mutually_exclusive_group()
    until.add_argument("--expires", help="last valid day YYYY-MM-DD (Moscow time)")
    until.add_argument("--days", type=int, help="valid for this many days")
    ki.add_argument("--rate-limit", type=int, help="requests per minute (default RATE_LIMIT_API_KEY_PER_MINUTE)")
    ki.add_argument("--note", help="for the team, e.g. the invoice")
    kl = sub.add_parser("key-list", help="keys: prefix, organisation, status, expiry, last use (never the key)")
    kl.add_argument("--org", help="name or id")
    kr = sub.add_parser("key-revoke", help="revoke a key by its prefix (pulse_...)")
    kr.add_argument("prefix")
    ke = sub.add_parser("key-extend", help="move a key's expiry (the organisation keeps the same key)")
    ke.add_argument("prefix")
    ke_until = ke.add_mutually_exclusive_group(required=True)
    ke_until.add_argument("--expires", help="last valid day YYYY-MM-DD (Moscow time)")
    ke_until.add_argument("--days", type=int, help="valid for this many days from today")
    oc = sub.add_parser("order-create", help="order for paid access: an invoice letter with the payment details")
    oc.add_argument("--org", required=True, help="name or id")
    oc.add_argument("--months", type=int, required=True)
    oc.add_argument("--amount", required=True, help="rubles, e.g. 60000 or 60000.50")
    oc.add_argument("--details", help="payment details for the letter (default PAYMENT_DETAILS)")
    oc.add_argument("--note", help="for the team, e.g. the contract number")
    ocf = sub.add_parser("order-confirm", help="the money has arrived: extend the keys (or wait for key-issue --order)")
    ocf.add_argument("order", type=int)
    ocf.add_argument("--received", required=True, help="the amount that arrived, rubles: must equal the order's")
    ocf.add_argument("--reference", help="the transfer's number, date or comment")
    ocf.add_argument("--by", help="who confirms (default: the OS user name)")
    occ = sub.add_parser("order-cancel", help="close an unpaid order")
    occ.add_argument("order", type=int)
    occ.add_argument("--reason")
    ol = sub.add_parser("order-list", help="payment orders")
    ol.add_argument("--org", help="name or id")
    ol.add_argument("--status", choices=["awaiting_payment", "paid", "fulfilled", "canceled"])

    sub.add_parser("coverage", help="data completeness report (JSON)")
    oa = sub.add_parser("export-openapi", help="write the OpenAPI contract for the frontend")
    oa.add_argument("--out", default=str(BACKEND_DIR / "openapi.json"))
    sub.add_parser("chat-check", help="chat LLM (Anthropic): key, network, model availability (free call only)")
    sub.add_parser("egress-check", help="public IP and country of this server: direct and through LLM_PROXY_URL (VPN)")
    ce = sub.add_parser("chat-eval", help="10 control questions: grounding check (calls the LLM API)")
    ce.add_argument("--questions", default=str(BACKEND_DIR / "app" / "chat" / "control_questions.yaml"))
    ce.add_argument("--yes", action="store_true")
    return p


ASYNC_COMMANDS = {
    "seed": cmd_seed,
    "seed-reference": cmd_seed_reference,
    "load-territories": cmd_load_territories,
    "seed-osm": cmd_seed_osm,
    "clear-demo-features": cmd_clear_demo,
    "load-features": cmd_load_features,
    "load-poi": cmd_load_poi,
    "load-travel-times": cmd_load_travel_times,
    "load-stats": cmd_load_stats,
    "load-od-matrix": cmd_load_od,
    "load-knowledge": cmd_load_knowledge,
    "seed-knowledge": cmd_seed_knowledge,
    "fetch-srtm": cmd_fetch_srtm,
    "load-ambulance": cmd_load_ambulance,
    "org-add": cmd_org_add,
    "org-list": cmd_org_list,
    "org-disable": cmd_org_disable,
    "org-enable": cmd_org_enable,
    "key-issue": cmd_key_issue,
    "key-list": cmd_key_list,
    "key-revoke": cmd_key_revoke,
    "key-extend": cmd_key_extend,
    "order-create": cmd_order_create,
    "order-confirm": cmd_order_confirm,
    "order-cancel": cmd_order_cancel,
    "order-list": cmd_order_list,
    "coverage": cmd_coverage,
    "chat-check": cmd_chat_check,
    "egress-check": cmd_egress_check,
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
    letter = result.pop("email_text", None) if isinstance(result, dict) else None
    _print(result)
    if letter:  # a letter to paste into an e-mail to the organisation (contains the key or payment link)
        sys.stdout.write("\n--- письмо для организации ---\n" + letter + "\n")
        sys.stdout.flush()
    if args.command == "chat-eval" and not result.get("passed"):
        return 1
    if args.command == "chat-check" and not result.get("ok"):
        return 1
    if args.command == "egress-check" and result.get("tunneled") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
