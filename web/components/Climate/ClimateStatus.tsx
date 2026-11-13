import type { ClimateRegion } from '@/lib/api';

type Status = NonNullable<ClimateRegion['color']>;

// The backend's verdict against the seasonal target (green / yellow / red).
const STATUS: Record<Status, { label: string; pill: string; dot: string }> = {
	green: { label: 'В норме', pill: 'bg-emerald-50 text-emerald-700 ring-emerald-100', dot: 'bg-emerald-500' },
	yellow: { label: 'Отклонение', pill: 'bg-amber-50 text-amber-700 ring-amber-100', dot: 'bg-amber-500' },
	red: { label: 'Сильное отклонение', pill: 'bg-rose-50 text-rose-700 ring-rose-100', dot: 'bg-rose-500' },
};

export function StatusPill({ color }: { color: Status }) {
	const s = STATUS[color];
	return (
		<span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-bold ring-1 ${s.pill}`}>
			<span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
			{s.label}
		</span>
	);
}

/** "+24.2°", "−3.4°" (with a real minus sign). */
export function formatTemperature(t: number, digits = 1): string {
	const rounded = Number(t.toFixed(digits));
	const sign = rounded > 0 ? '+' : rounded < 0 ? '−' : '';
	return `${sign}${Math.abs(rounded).toFixed(digits)}°`;
}
