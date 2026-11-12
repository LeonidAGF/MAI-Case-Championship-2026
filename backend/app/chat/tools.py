"""
The three chat functions (contract 9.3: get_index, compare_territories, run_scenario).

Every number the assistant may say comes from these results (DB + scoring pipeline).
Tools accept place names, so the model never needs ids or coordinates.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.compare.service import compare_territories
from app.core.errors import ApiError
from app.etl.names import normalize_search, region_code_from
from app.explain.service import explain_territory
from app.explain.texts import fact_text
from app.profiles.contract import Profile
from app.scoring.snapshots import r1
from app.whatif.catalog import CATALOG

POINT_SCENARIOS = {"W1": "fap", "W3": "school", "W4": "college", "W5": "railway_station", "W9": "park"}
AREA_SCENARIOS = {"W6", "W7", "W10"}


class ToolInputError(Exception):
    pass


class GetIndexInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    territory: str | None = Field(None, max_length=120)
    region: str | None = Field(None, max_length=80)
    cluster: Literal["A", "B", "V"] | None = None
    rank_by: str = "total"
    order: Literal["best", "worst"] = "best"
    limit: int = Field(5, ge=1, le=10)


class CompareInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    territories: list[str] = Field(..., min_length=2, max_length=3)


class ScenarioInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_code: Literal["W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9", "W10"]
    territory: str | None = Field(None, max_length=120)
    params: dict[str, Any] = Field(default_factory=dict)


def tool_definitions(feature_lines: str) -> list[dict[str, Any]]:
    """Anthropic tool definitions (name, description, input_schema); order is fixed for prompt caching."""
    return [
        {
            "name": "get_index",
            "description": (
                "Индекс доступности из базы «Атласа» под профиль пользователя. С параметром territory — "
                "балл 0–100, место в кластере, субиндексы, сильные и слабые стороны одной территории. "
                "Без territory — рейтинг территорий (по субъекту region и/или кластеру cluster); "
                "рейтинг строится отдельно внутри каждого кластера. Субиндекс со значением null — по нему "
                "в базе нет данных. rank_by: total, S1..S6 или код признака:\n" + feature_lines
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "territory": {"type": "string", "description": "Название населённого пункта или района, напр. «Ахты»"},
                    "region": {"type": "string", "description": "Субъект СКФО: «Дагестан», «Чечня» или код RU-DA"},
                    "cluster": {"type": "string", "enum": ["A", "B", "V"],
                                "description": "A — города, B — малые города и райцентры, V — сёла"},
                    "rank_by": {"type": "string", "description": "total | S1..S6 | F01..F30"},
                    "order": {"type": "string", "enum": ["best", "worst"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "compare_territories",
            "description": (
                "Сравнение 2–3 территорий одного кластера: баллы, субиндексы, лидеры по субиндексам, "
                "плюсы и минусы. Для территорий из разных кластеров вернёт ошибку с объяснением."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "territories": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 3}
                },
                "required": ["territories"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_scenario",
            "description": (
                "Сценарий «что если» и пересчёт индекса до/после. W1 — открыть ФАП/больницу, W2 — закрыть "
                "больницу, W3 — сад/школу, W4 — колледж, W5 — ЖД-станцию, W8 — расширить аэропорт, "
                "W9 — парк/ТЦ/кинотеатр: territory = населённый пункт, где находится объект. "
                "W6 — ремонт трассы (−X% времени), W7 — автобусный маршрут до райцентра, W10 — пакет "
                "W1+W3+W7: territory = район или населённый пункт в нём. params — необязательные уточнения. "
                "Если для сценария в базе нет исходных данных, результат скажет об этом в warnings."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "scenario_code": {"type": "string", "enum": [f"W{i}" for i in range(1, 11)]},
                    "territory": {"type": "string", "description": "Населённый пункт или район, напр. «Ахты»"},
                    "params": {
                        "type": "object",
                        "description": "Необязательные уточнения сценария",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "description": (
                                    "Тип объекта. W1, W2: hospital | clinic | fap | ambulance; W3: kindergarten | "
                                    "school; W4: college | university; W9: cafe | cinema | community_centre | "
                                    "sports | park | mall | marketplace"
                                ),
                            },
                            "percent": {"type": "number", "description": "W6, W8: сокращение времени в пути, %"},
                            "radius_km": {"type": "number", "description": "Радиус влияния объекта, км"},
                            "stop_access_min": {"type": "number", "description": "W7: время до новой остановки, мин"},
                            "park_area_ha": {"type": "number", "description": "W9: площадь нового парка, га"},
                        },
                    },
                },
                "required": ["scenario_code"],
                "additionalProperties": False,
            },
        },
    ]


class ChatTools:
    def __init__(self, container: Any) -> None:
        self.c = container
        self.sources: dict[str, str] = {}

    def _cite(self, codes: list[str] | None = None) -> list[str]:
        codes = codes or list(self.c.cfg.features)
        out = sorted({self.c.cfg.features[c].source for c in codes if c in self.c.cfg.features and self.c.cfg.features[c].source})
        for s in out:
            self.sources.setdefault(s, "")
        self.sources.setdefault("Атлас доступности СКФО: расчёт индекса (модель " + self.c.cfg.version + ")", "")
        return out

    async def execute(self, name: str, raw_input: Any, profile: Profile) -> dict[str, Any]:
        try:
            if name == "get_index":
                args = GetIndexInput.model_validate(raw_input)
                return await self._with_session(self._get_index, args, profile)
            if name == "compare_territories":
                args = CompareInput.model_validate(raw_input)
                return await self._with_session(self._compare, args, profile)
            if name == "run_scenario":
                args = ScenarioInput.model_validate(raw_input)
                return await self._with_session(self._scenario, args, profile)
        except ValidationError as exc:
            raise ToolInputError(f"INVALID_INPUT: {exc.errors()[:3]}") from exc
        raise ToolInputError(f"unknown tool {name!r}: use get_index, compare_territories or run_scenario")

    async def _with_session(self, fn, args, profile):  # noqa: ANN001, ANN202
        async with self.c.sessionmaker() as session:
            try:
                result = await fn(session, args, profile)
                await session.commit()
                return result
            except ApiError as exc:
                await session.rollback()
                return {"error": exc.code, "message": exc.message}

    async def resolve(self, session: AsyncSession, name: str, *, region: str | None = None) -> tuple[Any | None, list[str]]:
        if name.strip().isdigit():
            row = (await session.execute(text(_TERRITORY_SQL + " WHERE t.id = :id"), {"id": int(name)})).first()
            return row, []
        norm = normalize_search(name)
        params: dict[str, Any] = {"n": norm, "r": region}
        rows = (
            await session.execute(
                text(_TERRITORY_SQL + " WHERE t.name_normalized = :n AND (CAST(:r AS text) IS NULL OR t.region_code = :r) "
                     "ORDER BY (t.level = 'settlement') DESC, t.population DESC NULLS LAST LIMIT 5"),
                params,
            )
        ).all()
        if not rows:
            rows = (
                await session.execute(
                    text(_TERRITORY_SQL + " WHERE similarity(t.name_normalized, :n) > 0.35 "
                         "AND (CAST(:r AS text) IS NULL OR t.region_code = :r) "
                         "ORDER BY similarity(t.name_normalized, :n) DESC, t.population DESC NULLS LAST LIMIT 5"),
                    params,
                )
            ).all()
        if not rows:
            return None, []
        settlements = [r for r in rows if r.level == "settlement"]
        if len(settlements) > 1 and settlements[0].name_normalized == settlements[1].name_normalized:
            return None, [f"{r.name} ({r.municipality}, {r.region_code})" for r in settlements]
        return rows[0], []

    async def _get_index(self, session: AsyncSession, args: GetIndexInput, profile: Profile) -> dict[str, Any]:
        cfg = self.c.cfg
        region = region_code_from(args.region) if args.region else None
        if args.territory:
            row, ambiguous = await self.resolve(session, args.territory, region=region)
            if ambiguous:
                return {"error": "ambiguous", "candidates": ambiguous, "message": "Уточните, какой населённый пункт имеется в виду"}
            if row is None:
                return {"error": "not_found", "message": f"Территория «{args.territory}» не найдена"}
            if row.level != "settlement" or not row.is_scored:
                return await self._area_summary(session, row, profile)
            cs = await self.c.scoring.score_cluster(session, row.cluster_code, profile)
            score = cs.get(row.id)
            expl = explain_territory(cs, row.id, cfg, cluster_name=cfg.clusters[row.cluster_code].name, territory_name=row.name)
            return {
                "territory": row.name, "municipality": row.municipality, "region": row.region_code,
                "cluster": row.cluster_code, "cluster_name": cfg.clusters[row.cluster_code].name,
                "total": r1(score.total), "rank": cs.rank_of(row.id), "cluster_size": len(cs.scores),
                "cluster_median": cs.total_stats()["median"],
                "subindices": {cfg.subindices[s].name: r1(score.subindices.get(s)) for s in cfg.total_subindices},
                "strengths": [p["text"] for p in expl.pluses], "weaknesses": [m["text"] for m in expl.minuses],
                "profile": profile.preset or "custom", "sources": self._cite(),
            }

        clusters = [args.cluster] if args.cluster else list(profile.allowed_clusters)
        rank_by = args.rank_by.upper() if args.rank_by.lower() != "total" else "total"
        is_feature = rank_by in cfg.features
        if rank_by != "total" and rank_by not in cfg.total_subindices and not is_feature:
            return {"error": "bad_rank_by", "message": "rank_by: total, S1..S6 или код признака F01..F30"}
        rankings = []
        for cl in clusters:
            cs = await self.c.scoring.score_cluster(session, cl, profile)
            members = await session.execute(
                text(
                    "SELECT t.id, t.name, m.name AS municipality, t.region_code FROM territory t "
                    "LEFT JOIN territory m ON m.id = t.parent_id WHERE t.cluster_code = :c AND t.is_scored "
                    "AND (CAST(:r AS text) IS NULL OR t.region_code = :r)"
                ),
                {"c": cl, "r": region},
            )
            meta = {r.id: r for r in members}
            if is_feature:
                f = cfg.features[rank_by]
                vals = {t: cs.matrix.values[t].get(rank_by) for t in meta}
                vals = {t: v for t, v in vals.items() if v is not None}
                better_low = f.direction == "lower_better"
                ordered = sorted(vals, key=lambda t: vals[t], reverse=not better_low)
            else:
                def key(t: int) -> float:
                    s = cs.scores[t]
                    v = s.total if rank_by == "total" else s.subindices.get(rank_by)
                    return -1.0 if v is None else v

                ordered = sorted(meta, key=key, reverse=True)
            if args.order == "worst":
                ordered = list(reversed(ordered))
            items = []
            for t in ordered[: args.limit]:
                item = {"territory": meta[t].name, "municipality": meta[t].municipality, "region": meta[t].region_code,
                        "total": r1(cs.scores[t].total)}
                if is_feature:
                    prov = cs.matrix.provenance.get(t, {}).get(rank_by)
                    item["value"] = fact_text(rank_by, cs.matrix.values[t].get(rank_by), name=cfg.features[rank_by].name,
                                              unit=cfg.features[rank_by].unit, mode=prov.value_mode if prov else "any",
                                              inherited_from=prov.inherited_from if prov else None)
                elif rank_by != "total":
                    item[cfg.subindices[rank_by].name] = r1(cs.scores[t].subindices.get(rank_by))
                items.append(item)
            rankings.append({"cluster": cl, "cluster_name": cfg.clusters[cl].name, "territories_in_scope": len(meta), "items": items})
        return {
            "rank_by": rank_by, "order": args.order, "region": region, "rankings": rankings,
            "note": "Рейтинги построены отдельно внутри кластеров (город с городом, село с селом).",
            "sources": self._cite([rank_by] if is_feature else None),
        }

    async def _area_summary(self, session: AsyncSession, row: Any, profile: Profile) -> dict[str, Any]:
        col = "parent_id" if row.level == "municipality" else "region_code"
        key = row.id if row.level == "municipality" else row.region_code
        members = (
            await session.execute(
                text(f"SELECT id, cluster_code FROM territory WHERE is_scored AND {col} = :k"), {"k": key}
            )
        ).all()
        out = []
        for cl in sorted({m.cluster_code for m in members}):
            cs = await self.c.scoring.score_cluster(session, cl, profile)
            totals = [cs.scores[m.id].total for m in members if m.cluster_code == cl and m.id in cs.scores]
            if totals:
                out.append({"cluster": cl, "cluster_name": self.c.cfg.clusters[cl].name, "settlements": len(totals),
                            "mean_total": r1(sum(totals) / len(totals)), "min_total": r1(min(totals)), "max_total": r1(max(totals))})
        return {"territory": row.name, "level": row.level, "region": row.region_code, "by_cluster": out,
                "note": "Для района/субъекта показаны агрегаты по населённым пунктам внутри каждого кластера.",
                "sources": self._cite()}

    async def _compare(self, session: AsyncSession, args: CompareInput, profile: Profile) -> dict[str, Any]:
        ids, names = [], []
        for name in args.territories:
            row, ambiguous = await self.resolve(session, name)
            if ambiguous:
                return {"error": "ambiguous", "candidates": ambiguous, "message": f"Уточните «{name}»"}
            if row is None:
                return {"error": "not_found", "message": f"Территория «{name}» не найдена"}
            ids.append(row.id)
            names.append(row.name)
        result = await compare_territories(
            session, scoring=self.c.scoring, cfg=self.c.cfg, territory_ids=ids, profile=profile,
            profile_id=self.c.profiles.pristine_preset_id(profile), model_version_id=await self.c.model_version_id(),
        )
        cfg = self.c.cfg
        return {
            "cluster": result["cluster"], "cluster_name": result["cluster_name"],
            "items": [
                {"territory": it["name"], "total": it["total"], "rank": it["rank"],
                 "subindices": {cfg.subindices[s].name: v for s, v in it["subindices"].items()},
                 "strengths": [p["text"] for p in it["pluses"]], "weaknesses": [m["text"] for m in it["minuses"]]}
                for it in result["items"]
            ],
            "summary": result["summary"], "sources": self._cite(),
        }

    async def _scenario(self, session: AsyncSession, args: ScenarioInput, profile: Profile) -> dict[str, Any]:
        # Keep only what the scenario accepts: a stray "kind" for W6 must not fail the whole run.
        accepted = set(CATALOG[args.scenario_code].params_model.model_fields)
        params = {k: v for k, v in args.params.items() if k in accepted}
        ignored = sorted(set(args.params) - accepted)
        if args.territory:
            row, ambiguous = await self.resolve(session, args.territory)
            if ambiguous:
                return {"error": "ambiguous", "candidates": ambiguous, "message": "Уточните территорию"}
            if row is None:
                return {"error": "not_found", "message": f"Территория «{args.territory}» не найдена"}
            if args.scenario_code in POINT_SCENARIOS or args.scenario_code in ("W2", "W8"):
                if row.lat is None or row.lon is None:
                    return {"error": "no_location", "message": f"У территории «{row.name}» нет координат"}
                params.setdefault("lat", row.lat)
                params.setdefault("lon", row.lon)
                if args.scenario_code in POINT_SCENARIOS:
                    params.setdefault("kind", POINT_SCENARIOS[args.scenario_code])
            elif args.scenario_code in AREA_SCENARIOS:
                municipality = {"municipality": row.id, "settlement": row.parent_id}.get(row.level)
                if municipality is None:
                    return {"error": "scenario_scope",
                            "message": "Для этого сценария укажите район или населённый пункт, а не субъект"}
                params.setdefault("municipality_id", municipality)
        result = await self.c.whatif.run(
            session, scenario_code=args.scenario_code, params=params, profile=profile,
            profile_id=self.c.profiles.pristine_preset_id(profile), method="auto",
            model_version_id=await self.c.model_version_id(),
        )
        return {
            "run_id": result["run_id"], "scenario": result["scenario_name"], "method": result["method_used"],
            "affected": result["affected"], "explanation": result["explanation"],
            "summary": {k: result["summary"][k] for k in ("avg_delta", "max_delta", "min_delta")},
            "top_winners": [{"territory": w["name"], "delta": w["delta"]} for w in result["winners"][:5]],
            "top_losers": [{"territory": w["name"], "delta": w["delta"]} for w in result["losers"][:5]],
            "plan": result.get("plan"), "warnings": result["warnings"][:5], "sources": self._cite(),
            **({"ignored_params": ignored} if ignored else {}),
        }


_TERRITORY_SQL = """
    SELECT t.id, t.name, t.name_normalized, t.level, t.cluster_code, t.region_code, t.parent_id, t.is_scored,
           t.population, ST_X(t.centroid) AS lon, ST_Y(t.centroid) AS lat, m.name AS municipality
    FROM territory t LEFT JOIN territory m ON m.id = t.parent_id
"""


def compact(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
