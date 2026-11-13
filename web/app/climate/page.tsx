'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { ClimateRegion, fetchClimateGrid, fetchClimateRegions, fetchRegionBoundaries, Season } from '@/lib/api';
import type { ClimateMode } from '@/lib/climate';
import { colorAt, NO_DATA_COLOR, TEMPERATURE_STOPS } from '@/lib/palette';
import { ClimateMap, RegionFocus } from '@/components/Climate/ClimateMap';
import { ClimateLegend } from '@/components/Climate/ClimateLegend';
import { formatTemperature, StatusPill } from '@/components/Climate/ClimateStatus';
import { BrandMark } from '@/components/UI/Brand';
import { Icon, IconName } from '@/components/UI/Icon';
import { Segmented } from '@/components/UI/Segmented';
import { Spinner } from '@/components/UI/Spinner';

const SEASONS: { id: Season; label: string; icon: IconName; active: string }[] = [
	{ id: 'winter', label: 'Зима', icon: 'snowflake', active: 'from-sky-400 to-indigo-500' },
	{ id: 'spring', label: 'Весна', icon: 'sprout', active: 'from-emerald-400 to-lime-500' },
	{ id: 'summer', label: 'Лето', icon: 'sun', active: 'from-amber-400 to-orange-500' },
	{ id: 'autumn', label: 'Осень', icon: 'leaf', active: 'from-orange-400 to-rose-500' },
];

export default function ClimatePage() {
	const [season, setSeason] = useState<Season>('summer');
	const [mode, setMode] = useState<ClimateMode>('temperature');
	const [highlight, setHighlight] = useState<string | null>(null);
	const [focus, setFocus] = useState<RegionFocus | null>(null);

	const grid = useQuery({
		queryKey: ['climate-grid', season],
		queryFn: () => fetchClimateGrid(season),
		placeholderData: keepPreviousData,
	});
	const regions = useQuery({
		queryKey: ['climate-regions', season],
		queryFn: () => fetchClimateRegions(season),
		placeholderData: keepPreviousData,
	});
	const boundaries = useQuery({
		queryKey: ['region-boundaries'],
		queryFn: fetchRegionBoundaries,
		staleTime: Infinity,
	});

	const sortedRegions = useMemo(
		() => [...(regions.data?.regions ?? [])].sort((a, b) => (b.temperature_c ?? -Infinity) - (a.temperature_c ?? -Infinity)),
		[regions.data],
	);
	const seasonLabel = SEASONS.find((s) => s.id === season)?.label ?? '';
	const error = grid.error ?? boundaries.error;

	return (
		<main className="relative h-dvh w-screen overflow-hidden bg-ink-100">
			{grid.data && boundaries.data && (
				<ClimateMap
					cells={grid.data.cells}
					target={grid.data.target}
					regions={boundaries.data}
					mode={mode}
					highlight={highlight}
					focus={focus}
				/>
			)}

			{error ? (
				<div className="glass-strong absolute left-1/2 top-1/2 z-30 flex w-[min(26rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 items-start gap-3 rounded-3xl p-5 text-sm text-rose-700">
					<Icon name="alert" className="mt-0.5 h-5 w-5 shrink-0" />
					<div>
						<b className="block">Не удалось загрузить климатические данные</b>
						<span className="mt-1 block text-ink-500">{error.message}</span>
					</div>
				</div>
			) : (
				(!grid.data || !boundaries.data) && (
					<div className="glass absolute left-1/2 top-1/2 z-30 flex -translate-x-1/2 -translate-y-1/2 items-center gap-2.5 rounded-full px-4 py-2.5 text-sm font-semibold text-ink-700">
						<Spinner className="h-4 w-4 text-brand-500" />
						Загружаем данные NASA POWER…
					</div>
				)
			)}

			<div className="pointer-events-none absolute inset-x-3 top-3 z-20 flex flex-col items-start gap-2 md:inset-x-4 md:top-4 md:flex-row md:justify-between">
				<div className="glass pointer-events-auto flex items-center gap-3 rounded-2xl p-2 pr-4 animate-fade-up">
					<Link href="/" aria-label="На карту" className="rounded-xl">
						<BrandMark className="h-10 w-10" />
					</Link>
					<div>
						<h1 className="font-display text-[15px] font-semibold tracking-tight text-ink-900 md:text-lg">Климат СКФО</h1>
						<p className="text-xs font-medium text-ink-500">Сезонные средние 2025 года · NASA POWER</p>
					</div>
				</div>

				<div className="pointer-events-auto flex max-w-full flex-wrap items-center gap-2 animate-fade-up [animation-delay:80ms]">
					<div role="radiogroup" aria-label="Сезон" className="glass flex gap-1 rounded-2xl p-1.5">
						{SEASONS.map((s) => {
							const active = s.id === season;
							return (
								<button
									key={s.id}
									role="radio"
									aria-checked={active}
									onClick={() => setSeason(s.id)}
									className={`inline-flex h-10 items-center gap-1.5 rounded-xl px-3 text-[13px] font-bold transition-all duration-300 ease-spring md:px-3.5 ${
										active ? `bg-gradient-to-br ${s.active} text-white shadow-soft` : 'text-ink-600 hover:bg-white hover:text-ink-900'
									}`}
								>
									<Icon name={s.icon} className="h-4 w-4" />
									<span className={active ? '' : 'hidden sm:inline'}>{s.label}</span>
								</button>
							);
						})}
					</div>

					<div className="glass rounded-2xl p-1">
						<Segmented<ClimateMode>
							value={mode}
							onChange={setMode}
							ariaLabel="Что показать"
							options={[
								{ value: 'temperature', label: 'Температура', icon: 'thermometer' },
								{ value: 'comfort', label: 'Комфорт', icon: 'target' },
							]}
						/>
					</div>

					<Link
						href="/"
						aria-label="Назад к атласу"
						className="glass inline-flex h-[54px] items-center justify-center gap-2 rounded-2xl px-4 text-sm font-bold text-ink-700 transition hover:text-ink-900"
					>
						<Icon name="arrow-left" className="h-4 w-4" />
						<span className="hidden lg:inline">Назад к атласу</span>
					</Link>
				</div>
			</div>

			{grid.data && <ClimateLegend mode={mode} target={grid.data.target} seasonLabel={seasonLabel} />}

			<aside className="glass pointer-events-auto absolute inset-x-3 bottom-3 z-20 rounded-3xl p-3 animate-fade-up [animation-delay:160ms] md:inset-x-auto md:bottom-4 md:right-4 md:top-[5.75rem] md:flex md:w-80 md:flex-col md:p-4">
				<div className="flex items-baseline justify-between gap-2 px-1">
					<h2 className="font-display text-sm font-semibold tracking-tight text-ink-900">Субъекты СКФО</h2>
					<span className="text-[11px] font-semibold text-ink-400">от тёплых к холодным</span>
				</div>
				<div className="mt-3 flex snap-x gap-2 overflow-x-auto pb-1 scrollbar-hide md:min-h-0 md:flex-1 md:flex-col md:overflow-y-auto md:overflow-x-hidden md:pb-0">
					{sortedRegions.length === 0
						? Array.from({ length: 4 }, (_, i) => <div key={i} className="h-[4.75rem] w-56 shrink-0 rounded-2xl skeleton md:w-auto" />)
						: sortedRegions.map((r) => (
							<RegionCard
								key={r.region_code}
								region={r}
								active={highlight === r.region_code}
								onHover={setHighlight}
								onSelect={(code) => setFocus({ code, nonce: Date.now() })}
							/>
						))}
				</div>
				{grid.data && (
					<p className="mt-3 hidden px-1 text-[11px] leading-snug text-ink-400 md:block">
						{grid.data.source} · {grid.data.period_label} · время {grid.data.time_standard}. Нажмите на субъект, чтобы приблизить.
					</p>
				)}
			</aside>
		</main>
	);
}

function RegionCard({
	region,
	active,
	onHover,
	onSelect,
}: {
	region: ClimateRegion;
	active: boolean;
	onHover: (code: string | null) => void;
	onSelect: (code: string) => void;
}) {
	const t = region.temperature_c;
	const color = t == null ? NO_DATA_COLOR : colorAt(TEMPERATURE_STOPS, t);
	return (
		<button
			type="button"
			onMouseEnter={() => onHover(region.region_code)}
			onMouseLeave={() => onHover(null)}
			onFocus={() => onHover(region.region_code)}
			onBlur={() => onHover(null)}
			onClick={() => onSelect(region.region_code)}
			title={`${region.name}: средние по ${region.grid_cells} узлам сетки`}
			className={`relative w-56 shrink-0 snap-start overflow-hidden rounded-2xl p-3 pl-5 text-left transition-all duration-300 ease-spring md:w-auto ${
				active ? 'bg-white shadow-soft ring-2 ring-brand-200' : 'bg-white/70 ring-1 ring-white hover:-translate-y-0.5 hover:bg-white hover:shadow-soft'
			}`}
		>
			<span className="absolute inset-y-2.5 left-2 w-1.5 rounded-full" style={{ background: color }} />
			<div className="flex items-start justify-between gap-2">
				<span className="text-[13px] font-bold leading-snug text-ink-800">{region.name}</span>
				<span className="font-display text-lg font-bold leading-none tracking-tight tabular-nums text-ink-900">{t != null ? formatTemperature(t) : '—'}</span>
			</div>
			<div className="mt-2.5 flex items-center justify-between gap-2">
				<span className="inline-flex items-center gap-1 text-xs font-semibold tabular-nums text-ink-500">
					<Icon name="droplet" className="h-3.5 w-3.5 text-sky-500" />
					{region.humidity_pct != null ? `${region.humidity_pct.toFixed(0)}%` : '—'}
				</span>
				{region.color && <StatusPill color={region.color} />}
			</div>
		</button>
	);
}
