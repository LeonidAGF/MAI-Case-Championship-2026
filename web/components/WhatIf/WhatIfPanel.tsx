'use client';

import { useState, useEffect } from 'react';
import { useAtlas } from '@/lib/store';
import {
	runWhatIf, fetchScenarios, fetchTerritories,
	WhatIfResponse, ScenarioInfo, ApiError, TerritoryBrief, ParamSchema, ScenarioParams,
} from '@/lib/api';
import { Icon, IconName } from '@/components/UI/Icon';
import { FieldLabel, Select, TextInput } from '@/components/UI/Field';
import { Spinner } from '@/components/UI/Spinner';

const AIRPORTS = [
	{ name: 'Минеральные Воды', lat: 44.225, lon: 43.082 },
	{ name: 'Махачкала (Уйташ)', lat: 42.817, lon: 47.652 },
	{ name: 'Грозный', lat: 43.388, lon: 45.699 },
	{ name: 'Владикавказ (Беслан)', lat: 43.205, lon: 44.607 },
	{ name: 'Нальчик', lat: 43.513, lon: 43.637 },
	{ name: 'Магас', lat: 43.322, lon: 45.013 },
	{ name: 'Ставрополь', lat: 45.109, lon: 42.113 },
];

// Icons of the scenario tiles (by code; the codes themselves are never shown).
const SCENARIO_ICONS: Record<string, IconName> = {
	W1: 'hospital', W2: 'hospital-off', W3: 'book', W4: 'graduation', W5: 'train',
	W6: 'road', W7: 'bus', W8: 'plane', W9: 'tree', W10: 'package',
};

const METHOD_LABELS: Record<string, string> = {
	analytical: 'аналитическая оценка',
	osrm: 'маршруты по дорогам (OSRM)',
};

// Labels of parameters whose schema has no description.
const PARAM_LABELS: Record<string, string> = {
	radius_km: 'Радиус влияния, км',
	park_area_ha: 'Площадь парка, га',
	percent: 'Изменение, %',
	stop_access_min: 'Время до остановки, мин',
	poi_id: 'Объект из таблицы poi (id)',
};

const SKIPPED_PARAMS = ['lat', 'lon', 'kind', 'municipality_id', 'territory_ids'];

const numberValue = (v: unknown): number | '' => (typeof v === 'number' && Number.isFinite(v) ? v : '');
const stringValue = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '');
const isNumeric = (s: ParamSchema): boolean =>
	s.type === 'number' || s.type === 'integer' || !!s.anyOf?.some((x) => x.type === 'number' || x.type === 'integer');
const bound = (s: ParamSchema, key: 'minimum' | 'maximum'): number | undefined => s[key] ?? s.anyOf?.find((x) => x[key] !== undefined)?.[key];
const signed = (n: number, digits = 1) => `${n > 0 ? '+' : ''}${n.toFixed(digits)}`;

export function WhatIfPanel() {
	const profile = useAtlas((s) => s.profile);

	const [scenarios, setScenarios] = useState<ScenarioInfo[]>([]);
	const [selectedCode, setSelectedCode] = useState('W1');
	const [params, setParams] = useState<ScenarioParams>({});

	const [result, setResult] = useState<WhatIfResponse | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<ApiError | null>(null);

	const [searchQuery, setSearchQuery] = useState('');
	const [pickedName, setPickedName] = useState<string | null>(null);
	const [searchResults, setSearchResults] = useState<TerritoryBrief[]>([]);
	const [municipalities, setMunicipalities] = useState<TerritoryBrief[]>([]);

	useEffect(() => {
		fetchScenarios().then(data => {
			setScenarios(data);
			const first = data.find(s => s.code === selectedCode) || data[0];
			if (first) {
				setSelectedCode(first.code);
				setParams(first.example || {});
			}
		}).catch(console.error);
		// eslint-disable-next-line react-hooks/exhaustive-deps -- the catalogue is loaded once
	}, []);

	const activeScenario = scenarios.find(s => s.code === selectedCode);

	useEffect(() => {
		if (activeScenario?.target === 'municipality' && municipalities.length === 0) {
			fetchTerritories({ level: 'municipality', limit: 500 }).then(res => setMunicipalities(res.items));
		}
	}, [activeScenario, municipalities.length]);

	useEffect(() => {
		if (searchQuery.length < 2 || searchQuery === pickedName) return;
		const timer = setTimeout(() => {
			fetchTerritories({ level: 'settlement', q: searchQuery, limit: 10 })
				.then(res => setSearchResults(res.items))
				.catch(console.error);
		}, 300);
		return () => clearTimeout(timer);
	}, [searchQuery, pickedName]);

	const visibleResults = searchQuery.length >= 2 && searchQuery !== pickedName ? searchResults : [];

	const selectScenario = (code: string) => {
		if (code === selectedCode) return;
		const s = scenarios.find(x => x.code === code);
		setSelectedCode(code);
		setParams(s?.example || {});
		setResult(null);
		setError(null);
		setSearchQuery('');
		setPickedName(null);
		setSearchResults([]);
	};

	const updateParam = (key: string, value: unknown) => {
		setParams(prev => ({ ...prev, [key]: value }));
	};

	const numberInput = (key: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
		updateParam(key, e.target.value === '' ? '' : Number(e.target.value));

	const run = async () => {
		if (!activeScenario) return;
		setLoading(true);
		setError(null);

		const cleanParams = Object.fromEntries(
			Object.entries(params).filter(([, v]) => v !== '' && v !== null && v !== undefined)
		);

		try {
			const res = await runWhatIf(selectedCode, cleanParams, profile);
			setResult(res);
		} catch (e: unknown) {
			if (e instanceof ApiError) setError(e);
			else setError(new ApiError(e instanceof Error ? e.message : 'Не удалось запустить сценарий'));
		} finally {
			setLoading(false);
		}
	};

	if (scenarios.length === 0) {
		return (
			<div className="flex items-center justify-center gap-3 py-16 text-sm font-semibold text-ink-500">
				<Spinner />
				Загрузка каталога...
			</div>
		);
	}

	const scenarioName = (code: string) => scenarios.find(s => s.code === code)?.name ?? 'Шаг пакета';
	const sortedResults = result ? [...result.results].sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta)) : [];
	const maxDelta = Math.max(...sortedResults.slice(0, 50).map(r => Math.abs(r.delta)), 0.01);

	return (
		<div className="space-y-6">
			<section className="surface rounded-3xl p-4 md:p-6 animate-fade-up">
				<div className="flex flex-col gap-1 md:flex-row md:items-baseline md:justify-between">
					<h2 className="font-display text-base font-semibold tracking-tight text-ink-900">Что меняем</h2>
					<p className="text-xs font-medium text-ink-500">{scenarios.length} сценариев изменения инфраструктуры</p>
				</div>

				<div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
					{scenarios.map((s) => {
						const active = s.code === selectedCode;
						return (
							<button
								key={s.code}
								onClick={() => selectScenario(s.code)}
								aria-pressed={active}
								className={`group flex flex-col items-start gap-2.5 rounded-2xl p-3 text-left transition-all duration-300 ease-spring ${
									active
										? 'bg-white shadow-float ring-2 ring-brand-300'
										: 'bg-white/60 ring-1 ring-ink-100 hover:-translate-y-0.5 hover:bg-white hover:shadow-soft'
								}`}
							>
								<span
									className={`grid h-9 w-9 place-items-center rounded-xl transition-colors ${
										active ? 'bg-brand-gradient text-white shadow-glow' : 'bg-ink-100 text-ink-500 group-hover:bg-brand-50 group-hover:text-brand-600'
									}`}
								>
									<Icon name={SCENARIO_ICONS[s.code] ?? 'sparkles'} className="h-[18px] w-[18px]" />
								</span>
								<span className={`text-[13px] font-bold leading-snug ${active ? 'text-ink-900' : 'text-ink-700'}`}>{s.name}</span>
							</button>
						);
					})}
				</div>

				{activeScenario && (
					<div className="mt-4 flex items-start gap-2.5 rounded-2xl bg-brand-50/70 p-3.5 text-sm leading-relaxed text-ink-700 ring-1 ring-brand-100">
						<Icon name="info" className="mt-0.5 h-4 w-4 shrink-0 text-brand-500" />
						<p>{activeScenario.description}</p>
					</div>
				)}
			</section>

			<section className="surface rounded-3xl p-4 md:p-6 animate-fade-up [animation-delay:80ms]">
				<h2 className="font-display text-base font-semibold tracking-tight text-ink-900">Параметры</h2>

				<div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
					{activeScenario?.target === 'municipality' && (
						<div className="md:col-span-2">
							<FieldLabel>Район / Муниципалитет</FieldLabel>
							<Select
								value={stringValue(params.municipality_id)}
								onChange={(e) => updateParam('municipality_id', Number(e.target.value))}
							>
								<option value="">Выберите район...</option>
								{municipalities.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
							</Select>
						</div>
					)}

					{activeScenario?.target === 'point' && (
						<>
							{selectedCode === 'W8' ? (
								<div className="md:col-span-2">
									<FieldLabel>Аэропорт</FieldLabel>
									<Select
										onChange={(e) => {
											const ap = AIRPORTS.find(a => a.name === e.target.value);
											if (ap) setParams(prev => ({ ...prev, lat: ap.lat, lon: ap.lon }));
										}}
										defaultValue=""
									>
										<option value="" disabled>Выберите аэропорт...</option>
										{AIRPORTS.map(a => <option key={a.name} value={a.name}>{a.name}</option>)}
									</Select>
								</div>
							) : (
								<div className="relative md:col-span-2">
									<FieldLabel>Поиск населенного пункта (или введите координаты)</FieldLabel>
									<div className="relative">
										<Icon name="search" className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
										<TextInput
											type="text"
											placeholder="Например: Махачкала"
											value={searchQuery}
											onChange={(e) => {
												setSearchQuery(e.target.value);
												setPickedName(null);
											}}
											className="pl-10"
										/>
									</div>
									{visibleResults.length > 0 && (
										<ul className="glass-strong absolute z-10 mt-2 max-h-56 w-full overflow-auto rounded-2xl p-1.5 scrollbar-soft animate-scale-in">
											{visibleResults.map(t => (
												<li key={t.id}>
													<button
														type="button"
														className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm font-semibold text-ink-700 transition hover:bg-brand-50 hover:text-brand-700"
														onClick={() => {
															setParams(prev => ({ ...prev, lat: t.centroid?.lat, lon: t.centroid?.lon }));
															setSearchQuery(t.name);
															setPickedName(t.name);
															setSearchResults([]);
														}}
													>
														<Icon name="pin" className="h-4 w-4 shrink-0 text-ink-400" />
														{t.name}
													</button>
												</li>
											))}
										</ul>
									)}
								</div>
							)}

							<div>
								<FieldLabel>Широта</FieldLabel>
								<TextInput type="number" step="0.001" value={numberValue(params.lat)} onChange={numberInput('lat')} className="tabular-nums" />
							</div>
							<div>
								<FieldLabel>Долгота</FieldLabel>
								<TextInput type="number" step="0.001" value={numberValue(params.lon)} onChange={numberInput('lon')} className="tabular-nums" />
							</div>
						</>
					)}

					{activeScenario?.kinds && activeScenario.kinds.length > 0 && (
						<div>
							<FieldLabel>Тип объекта</FieldLabel>
							<Select value={stringValue(params.kind)} onChange={(e) => updateParam('kind', e.target.value)}>
								{activeScenario.kinds.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
							</Select>
						</div>
					)}

					{activeScenario?.params_schema?.properties && Object.entries(activeScenario.params_schema.properties).map(([key, schema]) => {
						if (SKIPPED_PARAMS.includes(key) || !isNumeric(schema)) return null;
						if (key === 'park_area_ha' && params.kind !== 'park') return null;
						const min = bound(schema, 'minimum');
						const max = bound(schema, 'maximum');
						const hint = min !== undefined && max !== undefined ? `${min}–${max}` : min !== undefined ? `от ${min}` : max !== undefined ? `до ${max}` : undefined;

						return (
							<div key={key}>
								<FieldLabel hint={hint}>{schema.description || PARAM_LABELS[key] || key}</FieldLabel>
								<TextInput
									type="number"
									min={min}
									max={max}
									value={numberValue(params[key])}
									onChange={numberInput(key)}
									className="tabular-nums"
								/>
							</div>
						);
					})}
				</div>

				<button
					onClick={run}
					disabled={loading}
					className="mt-6 inline-flex h-12 w-full items-center justify-center gap-2.5 rounded-2xl bg-brand-gradient text-[15px] font-bold text-white shadow-glow transition-all duration-300 ease-spring hover:-translate-y-0.5 hover:brightness-110 active:translate-y-0 disabled:translate-y-0 disabled:opacity-60"
				>
					{loading ? <Spinner className="h-5 w-5 text-white" /> : <Icon name="play" className="h-4 w-4" />}
					{loading ? 'Считаем...' : 'Запустить'}
				</button>
			</section>

			{error && (
				<div className="flex items-start gap-3 rounded-3xl bg-rose-50/90 p-4 text-sm text-rose-700 ring-1 ring-rose-100 animate-fade-up">
					<Icon name="alert" className="mt-0.5 h-5 w-5 shrink-0" />
					<div>
						<b className="block">{error.message}</b>
						{error.details && (
							<ul className="mt-2 list-disc space-y-1 pl-5">
								{error.details.map((d, i) => (
									<li key={i}><b>{d.loc?.join('.') || 'Поле'}:</b> {d.msg}</li>
								))}
							</ul>
						)}
					</div>
				</div>
			)}

			{result?.warnings && result.warnings.length > 0 && (
				<div className="flex items-start gap-3 rounded-3xl bg-amber-50/90 p-4 text-sm text-amber-800 ring-1 ring-amber-200 animate-fade-up">
					<Icon name="alert" className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
					<div className="space-y-1">{result.warnings.map((w, i) => <div key={i}>{w}</div>)}</div>
				</div>
			)}

			{result && (
				<div className="space-y-6 animate-fade-up">
					<section className="surface relative overflow-hidden rounded-3xl p-5 md:p-7">
						<div className="absolute inset-x-0 top-0 h-1.5 bg-brand-gradient" />
						<div className="absolute -right-24 -top-24 h-64 w-64 rounded-full bg-brand-200/40 blur-3xl" />
						<div className="relative">
							<span className="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-[11px] font-bold uppercase tracking-[0.1em] text-brand-700 ring-1 ring-brand-100">
								<Icon name="sparkles" className="h-3.5 w-3.5" />
								Результат
							</span>
							<h3 className="mt-3 font-display text-lg font-semibold tracking-tight text-ink-900 md:text-xl">{result.scenario_name}</h3>
							<p className="mt-2 max-w-3xl text-sm leading-relaxed text-ink-600">{result.explanation}</p>

							<div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
								<Stat label="Затронуто" value={String(result.affected)} />
								{result.summary && <Stat label="В среднем" value={signed(result.summary.avg_delta, 2)} tone={result.summary.avg_delta} />}
								{result.summary && <Stat label="Максимум" value={signed(result.summary.max_delta, 1)} tone={result.summary.max_delta} />}
								<Stat label="Метод" value={METHOD_LABELS[result.method_used] ?? result.method_used} small />
							</div>

							{result.winners.length > 0 && (
								<div className="mt-5">
									<div className="text-[11px] font-bold uppercase tracking-[0.12em] text-ink-400">Больше всего выиграют</div>
									<div className="mt-2 flex flex-wrap gap-2">
										{result.winners.slice(0, 6).map(w => (
											<span key={w.territory_id} className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 py-1 pl-3 pr-1 text-xs font-bold text-emerald-800 ring-1 ring-emerald-100">
												{w.name}
												<span className="rounded-full bg-white px-2 py-0.5 tabular-nums text-emerald-600">{signed(w.delta)}</span>
											</span>
										))}
									</div>
								</div>
							)}
						</div>
					</section>

					{result.plan && (
						<section className="surface rounded-3xl p-5 md:p-6">
							<h3 className="font-display text-base font-semibold tracking-tight text-ink-900">План пакетного улучшения</h3>
							<ol className="mt-4 space-y-2.5">
								{result.plan.map((step, i) => (
									<li key={i} className="flex items-start gap-3 rounded-2xl bg-white/70 p-3 ring-1 ring-ink-100">
										<span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-50 text-brand-600">
											<Icon name={SCENARIO_ICONS[step.scenario_code] ?? 'sparkles'} className="h-[18px] w-[18px]" />
										</span>
										<div className="text-sm">
											<div className="font-bold text-ink-800">{i + 1}. {scenarioName(step.scenario_code)}</div>
											<div className="mt-0.5 text-ink-500">{step.reason}</div>
										</div>
									</li>
								))}
							</ol>
						</section>
					)}

					<section className="surface overflow-hidden rounded-3xl">
						<div className="overflow-x-auto">
							<table className="w-full min-w-[560px] whitespace-nowrap text-left text-sm">
								<thead>
									<tr className="border-b border-ink-100 bg-ink-50/70 text-[11px] font-bold uppercase tracking-[0.1em] text-ink-500">
										<th className="px-4 py-3.5 md:px-6">Территория</th>
										<th className="px-3 py-3.5 text-center">Было</th>
										<th className="px-3 py-3.5 text-center">Стало</th>
										<th className="px-4 py-3.5 md:px-6">Изменение</th>
									</tr>
								</thead>
								<tbody className="divide-y divide-ink-100">
									{sortedResults.slice(0, 50).map((r) => (
										<tr key={r.territory_id} className="transition-colors hover:bg-brand-50/40">
											<td className="px-4 py-3 font-bold text-ink-800 md:px-6">{r.name}</td>
											<td className="px-3 py-3 text-center tabular-nums text-ink-400">{r.total_before.toFixed(1)}</td>
											<td className="px-3 py-3 text-center font-bold tabular-nums text-ink-800">{r.total_after.toFixed(1)}</td>
											<td className="px-4 py-3 md:px-6">
												<div className="flex items-center gap-3">
													<span
														className={`inline-flex min-w-[4.25rem] justify-center rounded-full px-2.5 py-1 text-xs font-bold tabular-nums ${
															r.delta > 0 ? 'bg-emerald-50 text-emerald-700' : r.delta < 0 ? 'bg-rose-50 text-rose-700' : 'bg-ink-100 text-ink-500'
														}`}
													>
														{signed(r.delta)}
													</span>
													<div className="h-1.5 w-28 overflow-hidden rounded-full bg-ink-100">
														<div
															className={`h-full rounded-full bg-gradient-to-r ${r.delta >= 0 ? 'from-emerald-300 to-emerald-500' : 'from-rose-300 to-rose-500'}`}
															style={{ width: `${(Math.abs(r.delta) / maxDelta) * 100}%` }}
														/>
													</div>
												</div>
											</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
						{result.results.length > 50 && (
							<div className="border-t border-ink-100 bg-ink-50/60 p-3 text-center text-xs font-semibold text-ink-500">
								Показаны топ-50 из {result.results.length}
							</div>
						)}
					</section>
				</div>
			)}
		</div>
	);
}

function Stat({ label, value, tone, small }: { label: string; value: string; tone?: number; small?: boolean }) {
	const color = tone === undefined ? 'text-ink-900' : tone > 0 ? 'text-emerald-600' : tone < 0 ? 'text-rose-600' : 'text-ink-900';
	return (
		<div className="rounded-2xl bg-white/80 p-3.5 ring-1 ring-ink-100">
			<div className="text-[11px] font-bold uppercase tracking-[0.1em] text-ink-400">{label}</div>
			<div className={`mt-1 font-bold tabular-nums ${small ? 'text-sm leading-snug text-ink-800' : `font-display text-2xl tracking-tight ${color}`}`}>{value}</div>
		</div>
	);
}
