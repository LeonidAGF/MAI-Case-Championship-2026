'use client';

import { useId } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useAtlas } from '@/lib/store';
import { fetchExplain, ProCon, WaterfallStep } from '@/lib/api';
import { encodeProfile } from '@/lib/profile';
import { FEATURE_LABELS } from '@/lib/dictionary';
import { colorAt, INDEX_STOPS } from '@/lib/palette';
import { Icon } from '@/components/UI/Icon';

export function TerritoryCard() {
	const selectedTerritoryId = useAtlas((s) => s.selectedTerritoryId);
	const selectTerritory = useAtlas((s) => s.selectTerritory);
	const profile = useAtlas((s) => s.profile);

	const { data, isFetching } = useQuery({
		queryKey: ['explain', selectedTerritoryId, encodeProfile(profile)],
		queryFn: () => fetchExplain(selectedTerritoryId as number, profile),
		enabled: selectedTerritoryId != null,
		placeholderData: keepPreviousData,
	});

	if (selectedTerritoryId == null || !data) return null;

	const diff = data.total - data.baseline;

	return (
		<aside
			className="glass-strong absolute z-20 overflow-y-auto scrollbar-soft animate-sheet-up
				inset-x-0 bottom-0 max-h-[62vh] rounded-t-3xl p-5
				md:inset-x-auto md:left-4 md:top-24 md:bottom-auto md:max-h-[calc(100dvh-7.5rem)] md:w-[25rem] md:rounded-3xl md:p-6 md:animate-scale-in"
		>
			{isFetching && <div className="absolute inset-x-6 top-0 h-0.5 overflow-hidden rounded-full skeleton" />}
			<div className="mx-auto mb-4 h-1.5 w-12 rounded-full bg-ink-200 md:hidden" />

			<div className="flex items-start justify-between gap-3">
				<div className="min-w-0">
					<span className="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-[11px] font-bold text-brand-700 ring-1 ring-brand-100">
						<Icon name="pin" className="h-3 w-3" />
						{data.cluster_name}
					</span>
					<h3 className="mt-2.5 font-display text-xl font-semibold leading-tight tracking-tight text-ink-900">{data.name}</h3>
				</div>
				<button
					onClick={() => selectTerritory(null)}
					aria-label="Закрыть карточку"
					className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-ink-400 transition hover:bg-ink-100 hover:text-ink-900"
				>
					<Icon name="x" className="h-5 w-5" />
				</button>
			</div>

			<div className="mt-4 flex items-center gap-4 rounded-2xl bg-gradient-to-br from-brand-50/80 via-white to-pink-50/70 p-4 ring-1 ring-white">
				<ScoreRing value={data.total} />
				<div className="min-w-0">
					<div className="text-[11px] font-bold uppercase tracking-[0.12em] text-ink-400">Индекс доступности</div>
					<div className="mt-2 flex flex-wrap items-center gap-1.5">
						<span
							className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-bold tabular-nums ${
								diff >= 0 ? 'bg-emerald-100/80 text-emerald-700' : 'bg-rose-100/80 text-rose-700'
							}`}
						>
							<Icon name={diff >= 0 ? 'trending-up' : 'trending-down'} className="h-3.5 w-3.5" />
							{diff > 0 ? '+' : ''}
							{diff.toFixed(1)}
						</span>
						<span className="text-xs font-medium text-ink-500">к типичному</span>
					</div>
					<div className="mt-1.5 text-xs text-ink-500">
						Типичный балл: <b className="font-bold tabular-nums text-ink-700">{data.baseline.toFixed(1)}</b>
					</div>
				</div>
			</div>

			<section className="mt-6">
				<h4 className="text-[11px] font-bold uppercase tracking-[0.12em] text-ink-400">Из чего складывается балл</h4>
				<Waterfall steps={data.waterfall} />
			</section>

			<div className="mt-6 grid gap-3">
				<ProsCons title="Плюсы" items={data.pluses} positive />
				<ProsCons title="Минусы" items={data.minuses} positive={false} />
			</div>
		</aside>
	);
}

/** The score on a ring coloured with the map's scale. */
function ScoreRing({ value }: { value: number }) {
	const gradientId = `ring${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
	const r = 40;
	const circumference = 2 * Math.PI * r;
	const share = Math.max(0, Math.min(100, value)) / 100;

	return (
		<div className="relative h-[104px] w-[104px] shrink-0">
			<svg viewBox="0 0 100 100" className="h-full w-full -rotate-90">
				<defs>
					<linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
						<stop offset="0%" stopColor={colorAt(INDEX_STOPS, Math.max(0, value - 25))} />
						<stop offset="100%" stopColor={colorAt(INDEX_STOPS, value)} />
					</linearGradient>
				</defs>
				<circle cx="50" cy="50" r={r} fill="none" stroke="currentColor" strokeWidth="9" className="text-white" />
				<circle cx="50" cy="50" r={r} fill="none" stroke="currentColor" strokeWidth="9" className="text-ink-100" strokeOpacity={0.9} />
				<circle
					cx="50"
					cy="50"
					r={r}
					fill="none"
					stroke={`url(#${gradientId})`}
					strokeWidth="9"
					strokeLinecap="round"
					strokeDasharray={circumference}
					strokeDashoffset={circumference * (1 - share)}
					className="transition-[stroke-dashoffset] duration-700 ease-spring"
				/>
			</svg>
			<div className="absolute inset-0 grid place-items-center text-center">
				<div>
					<div className="font-display text-[25px] font-bold leading-none tracking-tight text-ink-900 tabular-nums">{value.toFixed(1)}</div>
					<div className="mt-1 text-[10px] font-bold uppercase tracking-[0.14em] text-ink-400">из 100</div>
				</div>
			</div>
		</div>
	);
}

function Waterfall({ steps }: { steps: WaterfallStep[] }) {
	const sorted = [...steps].sort((a, b) => b.contribution - a.contribution);
	const max = Math.max(...sorted.map((c) => Math.abs(c.contribution)), 1);

	return (
		<div className="mt-3 space-y-3">
			{sorted.map((c) => {
				const label = FEATURE_LABELS[c.feature_code] || c.label || c.feature_code;
				const positive = c.contribution >= 0;
				return (
					<div key={c.feature_code} className="text-xs">
						<div className="mb-1 flex items-center justify-between gap-3 font-semibold text-ink-700">
							<span className="truncate" title={label}>
								{label}
							</span>
							<span className={`shrink-0 tabular-nums ${positive ? 'text-emerald-600' : 'text-rose-600'}`}>
								{c.contribution > 0 ? '+' : ''}
								{c.contribution.toFixed(1)}
							</span>
						</div>
						<div className="h-2 overflow-hidden rounded-full bg-ink-100">
							<div
								className={`h-full rounded-full bg-gradient-to-r transition-[width] duration-700 ease-spring ${
									positive ? 'from-emerald-300 to-emerald-500' : 'from-rose-300 to-rose-500'
								}`}
								style={{ width: `${(Math.abs(c.contribution) / max) * 100}%` }}
							/>
						</div>
					</div>
				);
			})}
		</div>
	);
}

function ProsCons({ title, items, positive }: { title: string; items: ProCon[]; positive: boolean }) {
	if (!items.length) return null;
	return (
		<div className={`rounded-2xl p-4 ring-1 ${positive ? 'bg-emerald-50/70 ring-emerald-100' : 'bg-rose-50/70 ring-rose-100'}`}>
			<h4 className={`flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.1em] ${positive ? 'text-emerald-700' : 'text-rose-700'}`}>
				<Icon name={positive ? 'trending-up' : 'trending-down'} className="h-4 w-4" />
				{title}
			</h4>
			<ul className="mt-2.5 space-y-2">
				{items.map((c) => (
					<li key={c.feature_code} className="flex items-start justify-between gap-3 text-[13px]">
						<span className="leading-snug text-ink-700">{c.text}</span>
						<span
							className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-bold tabular-nums ${
								positive ? 'bg-white text-emerald-600' : 'bg-white text-rose-600'
							}`}
						>
							{positive ? '+' : ''}
							{c.delta.toFixed(1)}
						</span>
					</li>
				))}
			</ul>
		</div>
	);
}
