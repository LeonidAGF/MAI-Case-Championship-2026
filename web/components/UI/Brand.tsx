import Link from 'next/link';

/** The Pulse Score mark: a heartbeat line on the brand gradient. */
export function BrandMark({ className = 'h-9 w-9' }: { className?: string }) {
	return (
		<span className={`relative inline-grid shrink-0 place-items-center overflow-hidden rounded-xl bg-brand-gradient shadow-glow ${className}`}>
			<span className="absolute inset-0 bg-[radial-gradient(circle_at_30%_20%,rgb(255_255_255/0.45),transparent_60%)]" />
			<svg viewBox="0 0 24 24" className="relative h-[62%] w-[62%] text-white" fill="none" stroke="currentColor" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
				<path d="M2 12.5h4.2l2.3-6 4 12 3-8.5 1.8 2.5H22" />
			</svg>
		</span>
	);
}

/** Mark and name, linking to the map. */
export function Brand({ subtitle = 'Атлас доступности СКФО' }: { subtitle?: string }) {
	return (
		<Link href="/" className="group flex items-center gap-2.5 rounded-xl" aria-label="Pulse Score — на карту">
			<BrandMark className="h-9 w-9 transition-transform duration-500 ease-spring group-hover:-rotate-6 group-hover:scale-105" />
			<span className="leading-none">
				<span className="block font-display text-[15px] font-semibold tracking-tight text-ink-900">
					Pulse <span className="text-brand-gradient">Score</span>
				</span>
				<span className="mt-1 block text-[11px] font-semibold text-ink-500">{subtitle}</span>
			</span>
		</Link>
	);
}
