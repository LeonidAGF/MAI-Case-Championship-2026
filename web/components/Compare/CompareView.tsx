'use client';

import { useId } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAtlas } from '@/lib/store';
import { postCompare, CompareResponse } from '@/lib/api';
import { encodeProfile } from '@/lib/profile';
import { SUBINDEX_LABELS } from '@/lib/dictionary';
import { Icon } from '@/components/UI/Icon';

/** Colours of the compared territories, in selection order (cards, bars, radar, slot dots). */
export const SERIES = [
	{ hex: '#6d5dfc', dot: 'bg-[#6d5dfc]', bar: 'from-brand-300 to-brand-600', accent: 'from-brand-500 via-violet-500 to-fuchsia-500' },
	{ hex: '#14b8a6', dot: 'bg-[#14b8a6]', bar: 'from-teal-300 to-teal-500', accent: 'from-teal-400 via-emerald-400 to-cyan-400' },
	{ hex: '#f59e0b', dot: 'bg-[#f59e0b]', bar: 'from-amber-300 to-amber-500', accent: 'from-amber-400 via-orange-400 to-rose-400' },
] as const;

export function CompareView({ ids }: { ids: number[] }) {
	const profile = useAtlas((s) => s.profile);
	const { data, error, isPending } = useQuery({
		queryKey: ['compare', ids, encodeProfile(profile)],
		queryFn: () => postCompare(ids, profile),
		enabled: ids.length >= 2,
		retry: false,
	});

	if (ids.length < 2) return <div className="p-8 text-center text-ink-500">Выберите минимум две территории для сравнения.</div>;
	if (error) {
		return (
			<div className="surface mx-auto flex max-w-xl items-start gap-3 rounded-3xl p-5 text-sm font-medium text-rose-700">
				<span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-rose-50 text-rose-500">
					<Icon name="alert" className="h-5 w-5" />
				</span>
				<p className="pt-1.5">{error.message || 'Ошибка при сравнении (возможно, выбраны разные кластеры)'}</p>
			</div>
		);
	}
	if (isPending || !data) return <CompareSkeleton count={ids.length} />;

	return (
		<div className="space-y-6 animate-fade-up">
			<div className="flex snap-x snap-mandatory gap-4 overflow-x-auto pb-2 scrollbar-hide md:grid md:grid-cols-3 md:overflow-visible md:pb-0">
				{data.items.map((r, idx) => {
					const series = SERIES[idx % SERIES.length];
					return (
						<div key={r.territory_id} className="surface relative flex w-[85vw] shrink-0 snap-center flex-col overflow-hidden rounded-3xl p-5 md:w-auto md:p-6">
							<div className={`absolute inset-x-0 top-0 h-1.5 bg-gradient-to-r ${series.accent}`} />
							<div className="flex items-center gap-2 text-xs font-bold text-ink-400">
								<span className={`h-2.5 w-2.5 rounded-full ${series.dot}`} />
								{data.cluster_name}
							</div>
							<h3 className="mt-2 font-display text-lg font-semibold leading-tight tracking-tight text-ink-900 md:text-xl">{r.name}</h3>

							<div className="mt-4 flex items-end gap-2">
								<span className="font-display text-5xl font-bold leading-none tracking-tight tabular-nums text-ink-900">{r.total.toFixed(1)}</span>
								<span className="mb-1 text-sm font-semibold text-ink-400">/ 100</span>
							</div>

							<div className="mt-6 space-y-3.5">
								{Object.entries(r.subindices).map(([key, val]) => (
									<div key={key} className="text-xs">
										<div className="mb-1 flex items-center justify-between font-semibold text-ink-600">
											<span>{SUBINDEX_LABELS[key] || key}</span>
											<span className="font-bold tabular-nums text-ink-800">{val != null ? val.toFixed(0) : '—'}</span>
										</div>
										<div className="h-2 overflow-hidden rounded-full bg-ink-100">
											<div className={`h-full rounded-full bg-gradient-to-r ${series.bar} transition-[width] duration-700 ease-spring`} style={{ width: `${val || 0}%` }} />
										</div>
									</div>
								))}
							</div>
						</div>
					);
				})}
			</div>

			<div className="surface grid grid-cols-1 gap-6 rounded-3xl p-5 md:grid-cols-2 md:gap-10 md:p-8">
				<div className="flex flex-col items-center justify-center">
					<h3 className="self-start font-display text-base font-semibold tracking-tight text-ink-900">Профиль территорий</h3>
					<Radar data={data} />
					<div className="flex flex-wrap justify-center gap-3">
						{data.radar.series.map((s, idx) => (
							<span key={s.territory_id} className="inline-flex items-center gap-1.5 text-xs font-semibold text-ink-600">
								<span className={`h-2.5 w-2.5 rounded-full ${SERIES[idx % SERIES.length].dot}`} />
								{s.name}
							</span>
						))}
					</div>
				</div>

				<div className="flex flex-col justify-center">
					<h3 className="font-display text-base font-semibold tracking-tight text-ink-900">Ключевые различия</h3>
					<div className="mt-4 space-y-2.5">
						{data.summary.map((text, i) => (
							<div key={i} className="flex gap-3 rounded-2xl bg-ink-50/80 p-3.5 text-sm leading-relaxed text-ink-700 ring-1 ring-ink-100">
								<span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-brand-50 text-brand-500">
									<Icon name="sparkles" className="h-3.5 w-3.5" />
								</span>
								<p>{text}</p>
							</div>
						))}
					</div>
				</div>
			</div>
		</div>
	);
}

function CompareSkeleton({ count }: { count: number }) {
	return (
		<div className="space-y-6">
			<div className="flex gap-4 overflow-hidden md:grid md:grid-cols-3">
				{Array.from({ length: count }, (_, i) => (
					<div key={i} className="surface w-[85vw] shrink-0 rounded-3xl p-6 md:w-auto">
						<div className="h-3 w-24 rounded-full skeleton" />
						<div className="mt-3 h-6 w-40 rounded-full skeleton" />
						<div className="mt-5 h-12 w-28 rounded-2xl skeleton" />
						<div className="mt-6 space-y-3">
							{Array.from({ length: 6 }, (_, j) => <div key={j} className="h-2 rounded-full skeleton" />)}
						</div>
					</div>
				))}
			</div>
			<p className="text-center text-sm font-semibold text-brand-600">Считаем индексы…</p>
		</div>
	);
}

function Radar({ data }: { data: CompareResponse }) {
	const { axes, series } = data.radar;
	const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '');
	const cx = 200, cy = 200, R = 140;

	const angle = (i: number) => (Math.PI * 2 * i) / axes.length - Math.PI / 2;

	const point = (i: number, val: number) => {
		const r = (val / 100) * R;
		return [cx + r * Math.cos(angle(i)), cy + r * Math.sin(angle(i))];
	};

	return (
		<svg viewBox="0 0 400 400" className="my-2 w-full max-w-sm overflow-visible">
			<defs>
				<radialGradient id={`${uid}-bg`}>
					<stop offset="0%" stopColor="#f3f2ff" />
					<stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
				</radialGradient>
				{series.map((_, idx) => (
					<radialGradient key={idx} id={`${uid}-s${idx}`}>
						<stop offset="0%" stopColor={SERIES[idx % SERIES.length].hex} stopOpacity="0.05" />
						<stop offset="100%" stopColor={SERIES[idx % SERIES.length].hex} stopOpacity="0.28" />
					</radialGradient>
				))}
			</defs>

			<polygon points={axes.map((_, i) => point(i, 100).join(',')).join(' ')} fill={`url(#${uid}-bg)`} />
			{[25, 50, 75, 100].map((ring) => (
				<polygon
					key={ring}
					points={axes.map((_, i) => point(i, ring).join(',')).join(' ')}
					fill="none"
					stroke="#dcdfeb"
					strokeWidth={ring === 100 ? 1.5 : 1}
					strokeDasharray={ring === 100 ? undefined : '3 5'}
					strokeLinejoin="round"
				/>
			))}

			{axes.map((l, i) => {
				const [x, y] = point(i, 120);
				return (
					<g key={l.code}>
						<line x1={cx} y1={cy} x2={point(i, 100)[0]} y2={point(i, 100)[1]} stroke="#dcdfeb" />
						<text x={x} y={y} textAnchor="middle" dominantBaseline="middle" fontSize="13" fontWeight="700" fill="#505678">
							{SUBINDEX_LABELS[l.code] || l.name}
						</text>
					</g>
				);
			})}

			{series.map((r, idx) => {
				const color = SERIES[idx % SERIES.length].hex;
				return (
					<g key={r.territory_id}>
						<polygon
							points={r.values.map((v, i) => point(i, v ?? 0).join(',')).join(' ')}
							fill={`url(#${uid}-s${idx})`}
							stroke={color}
							strokeWidth={2.5}
							strokeLinejoin="round"
						/>
						{r.values.map((v, i) => {
							const [px, py] = point(i, v ?? 0);
							return <circle key={i} cx={px} cy={py} r={4} fill={color} stroke="#ffffff" strokeWidth={2} />;
						})}
					</g>
				);
			})}
		</svg>
	);
}
