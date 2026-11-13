import type { ClimateTarget } from '@/lib/api';
import type { ClimateMode } from '@/lib/climate';
import { COMFORT_DOMAIN, COMFORT_STOPS, cssGradient, TEMPERATURE_DOMAIN, TEMPERATURE_STOPS } from '@/lib/palette';

const TEMPERATURE_TICKS = [-10, 0, 10, 20, 30];

const position = (value: number, [min, max]: readonly [number, number]) => ((value - min) / (max - min)) * 100;
const degrees = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v)}°`;

export function ClimateLegend({ mode, target, seasonLabel }: { mode: ClimateMode; target: ClimateTarget; seasonLabel: string }) {
	return (
		<div className="glass pointer-events-none absolute bottom-[10.75rem] left-3 z-10 w-[min(19rem,calc(100vw-1.5rem))] rounded-2xl px-4 py-3.5 animate-fade-up [animation-delay:200ms] md:bottom-11 md:left-4">
			{mode === 'temperature' ? (
				<>
					<div className="flex items-baseline justify-between gap-2">
						<span className="text-[11px] font-bold uppercase tracking-[0.12em] text-ink-500">Средняя температура</span>
						<span className="text-[11px] font-semibold text-ink-400">{seasonLabel} 2025</span>
					</div>
					<div className="mt-2.5 h-3 rounded-full shadow-[inset_0_1px_2px_rgb(23_27_53/0.12)] ring-1 ring-white/80" style={{ backgroundImage: cssGradient(TEMPERATURE_STOPS, TEMPERATURE_DOMAIN) }} />
					<div className="relative mt-1.5 h-4 text-[11px] font-semibold tabular-nums text-ink-500">
						{TEMPERATURE_TICKS.map((v) => {
							const left = position(v, TEMPERATURE_DOMAIN);
							return (
								<span key={v} className={`absolute ${left >= 99 ? '-translate-x-full' : '-translate-x-1/2'}`} style={{ left: `${left}%` }}>
									{degrees(v)}
								</span>
							);
						})}
					</div>
					<p className="mt-2 text-[11px] leading-snug text-ink-400">
						Плавная поверхность между узлами сетки NASA POWER; белые линии — изотермы через 2°.
					</p>
				</>
			) : (
				<>
					<div className="flex items-baseline justify-between gap-2">
						<span className="text-[11px] font-bold uppercase tracking-[0.12em] text-ink-500">Отклонение от цели</span>
						<span className="text-[11px] font-semibold text-ink-400">{seasonLabel} 2025</span>
					</div>
					<div className="mt-2.5 h-3 rounded-full shadow-[inset_0_1px_2px_rgb(23_27_53/0.12)] ring-1 ring-white/80" style={{ backgroundImage: cssGradient(COMFORT_STOPS, COMFORT_DOMAIN) }} />
					<div className="relative mt-1.5 h-4 text-[11px] font-semibold text-ink-500">
						<span className="absolute left-0">в диапазоне</span>
						<span className="absolute -translate-x-1/2" style={{ left: `${position(1, COMFORT_DOMAIN)}%` }}>порог</span>
						<span className="absolute right-0">сильное</span>
					</div>
					<p className="mt-2 text-[11px] leading-snug text-ink-400">
						Цель сезона: {degrees(target.temperature_min_c)}…{degrees(target.temperature_max_c)}, влажность {target.humidity_min_pct}–{target.humidity_max_pct}%.
						Проектный ориентир, не климатическая норма.
					</p>
				</>
			)}
		</div>
	);
}
