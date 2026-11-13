'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect } from 'react';
import { Brand } from '@/components/UI/Brand';
import { Icon, IconName } from '@/components/UI/Icon';

const PAGES: { href: string; label: string; hint: string; icon: IconName }[] = [
	{ href: '/', label: 'Карта', hint: 'Индекс доступности жизни', icon: 'map' },
	{ href: '/climate', label: 'Климат', hint: 'Сезоны 2025 года', icon: 'thermometer' },
	{ href: '/compare', label: 'Сравнить', hint: 'Две или три территории', icon: 'compare' },
	{ href: '/whatif', label: 'Что если', hint: 'Симулятор инфраструктуры', icon: 'flask' },
];

const LINKS: { href: string; label: string; icon: IconName }[] = [
	{ href: 'https://github.com/LeonidAGF/MAI-Case-Championship-2026', label: 'API', icon: 'code' },
	{ href: 'https://cson.site:3001', label: 'Grafana', icon: 'activity' },
];

export function Sidebar({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
	const pathname = usePathname();

	useEffect(() => {
		if (!isOpen) return;
		const onKey = (e: KeyboardEvent) => {
			if (e.key === 'Escape') onClose();
		};
		window.addEventListener('keydown', onKey);
		return () => window.removeEventListener('keydown', onKey);
	}, [isOpen, onClose]);

	return (
		<>
			<div
				aria-hidden="true"
				onClick={onClose}
				className={`fixed inset-0 z-40 bg-ink-950/20 backdrop-blur-[2px] transition-opacity duration-300 ${isOpen ? 'opacity-100' : 'pointer-events-none opacity-0'}`}
			/>

			<aside
				aria-label="Навигация"
				inert={!isOpen}
				className={`glass-strong fixed inset-y-3 left-3 z-50 flex w-[min(20rem,calc(100vw-1.5rem))] flex-col rounded-3xl p-3 transition-[transform,opacity] duration-500 ease-spring ${
					isOpen ? 'translate-x-0 opacity-100' : '-translate-x-[110%] opacity-0'
				}`}
			>
				<div className="flex items-center justify-between gap-2 p-2">
					<Brand />
					<button
						onClick={onClose}
						aria-label="Закрыть меню"
						className="grid h-9 w-9 place-items-center rounded-xl text-ink-500 transition hover:bg-ink-100 hover:text-ink-900"
					>
						<Icon name="x" className="h-5 w-5" />
					</button>
				</div>

				<nav className="mt-4 flex flex-col gap-1">
					<p className="px-3 pb-1 text-[11px] font-bold uppercase tracking-[0.14em] text-ink-400">Разделы</p>
					{PAGES.map((p) => {
						const active = pathname === p.href;
						return (
							<Link
								key={p.href}
								href={p.href}
								onClick={onClose}
								aria-current={active ? 'page' : undefined}
								className={`group flex items-center gap-3 rounded-2xl p-2.5 transition-all duration-300 ease-spring ${
									active ? 'bg-white shadow-soft' : 'hover:translate-x-0.5 hover:bg-white/70'
								}`}
							>
								<span
									className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl transition-colors ${
										active ? 'bg-brand-gradient text-white shadow-glow' : 'bg-ink-100 text-ink-500 group-hover:bg-brand-50 group-hover:text-brand-600'
									}`}
								>
									<Icon name={p.icon} className="h-5 w-5" />
								</span>
								<span className="min-w-0">
									<span className={`block text-sm font-bold ${active ? 'text-ink-900' : 'text-ink-700'}`}>{p.label}</span>
									<span className="block truncate text-xs font-medium text-ink-400">{p.hint}</span>
								</span>
							</Link>
						);
					})}
				</nav>

				<div className="mt-6 flex flex-col gap-0.5">
					<p className="px-3 pb-1 text-[11px] font-bold uppercase tracking-[0.14em] text-ink-400">Для разработчиков</p>
					{LINKS.map((l) => (
						<a
							key={l.href}
							href={l.href}
							target="_blank"
							rel="noopener noreferrer"
							className="group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-semibold text-ink-600 transition hover:bg-white/70 hover:text-ink-900"
						>
							<Icon name={l.icon} className="h-4 w-4 text-ink-400 transition-colors group-hover:text-brand-500" />
							{l.label}
							<Icon name="external" className="ml-auto h-3.5 w-3.5 text-ink-300 transition-colors group-hover:text-ink-500" />
						</a>
					))}
				</div>

				<div className="mt-auto overflow-hidden rounded-2xl bg-gradient-to-br from-brand-50 via-white to-pink-50 p-4 ring-1 ring-white">
					<p className="font-display text-[13px] font-semibold text-ink-800">Северный Кавказ в одном индексе</p>
					<p className="mt-1.5 text-xs leading-relaxed text-ink-500">
						3 403 населённых пункта семи субъектов СКФО. Данные OpenStreetMap и NASA POWER.
					</p>
					<p className="mt-3 text-[11px] font-semibold text-ink-400">Team_Epsilon · MAI Case Championship 2026</p>
				</div>
			</aside>
		</>
	);
}
