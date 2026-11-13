import Link from 'next/link';
import type { ReactNode } from 'react';
import { BrandMark } from '@/components/UI/Brand';
import { Icon, IconName } from '@/components/UI/Icon';

/** Sticky glass header of the pages without a map: mark, title, subtitle and the way back to the map. */
export function PageHeader({ title, subtitle, icon, children }: { title: string; subtitle?: string; icon?: IconName; children?: ReactNode }) {
	return (
		<header className="sticky top-0 z-30 px-3 pt-3 md:px-6 md:pt-4">
			<div className="glass mx-auto flex max-w-6xl items-center gap-3 rounded-2xl p-2 pr-3 animate-fade-up md:gap-4 md:pr-4">
				<Link href="/" aria-label="На карту" className="rounded-xl">
					<BrandMark className="h-10 w-10" />
				</Link>
				<div className="min-w-0 flex-1">
					<h1 className="flex items-center gap-2 truncate font-display text-[15px] font-semibold tracking-tight text-ink-900 md:text-lg">
						{icon && <Icon name={icon} className="hidden h-5 w-5 shrink-0 text-brand-500 sm:block" />}
						<span className="truncate">{title}</span>
					</h1>
					{subtitle && <p className="truncate text-xs font-medium text-ink-500 md:text-[13px]">{subtitle}</p>}
				</div>
				{children}
				<Link
					href="/"
					className="inline-flex h-10 shrink-0 items-center gap-1.5 rounded-xl px-3 text-sm font-bold text-ink-600 transition hover:bg-white hover:text-ink-900 hover:shadow-soft"
				>
					<Icon name="arrow-left" className="h-4 w-4" />
					<span className="hidden sm:inline">Назад к карте</span>
				</Link>
			</div>
		</header>
	);
}
