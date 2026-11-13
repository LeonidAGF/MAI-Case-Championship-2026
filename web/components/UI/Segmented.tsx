'use client';

import { Icon, IconName } from '@/components/UI/Icon';

export interface SegmentOption<T extends string | number> {
	value: T;
	label: string;
	icon?: IconName;
}

/** A row of mutually exclusive options; the chosen one is a raised white pill. */
export function Segmented<T extends string | number>({
	value,
	options,
	onChange,
	size = 'md',
	ariaLabel,
}: {
	value: T;
	options: SegmentOption<T>[];
	onChange: (value: T) => void;
	size?: 'sm' | 'md';
	ariaLabel?: string;
}) {
	return (
		<div role="radiogroup" aria-label={ariaLabel} className="grid auto-cols-fr grid-flow-col gap-1 rounded-xl bg-ink-100/80 p-1">
			{options.map((o) => {
				const active = o.value === value;
				return (
					<button
						key={String(o.value)}
						type="button"
						role="radio"
						aria-checked={active}
						onClick={() => onChange(o.value)}
						className={`inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg px-2 font-semibold transition-all duration-300 ease-spring ${
							size === 'sm' ? 'h-8 text-xs' : 'h-9 text-[13px]'
						} ${active ? 'bg-white text-ink-900 shadow-soft' : 'text-ink-500 hover:bg-white/50 hover:text-ink-800'}`}
					>
						{o.icon && <Icon name={o.icon} className="h-3.5 w-3.5" />}
						{o.label}
					</button>
				);
			})}
		</div>
	);
}
