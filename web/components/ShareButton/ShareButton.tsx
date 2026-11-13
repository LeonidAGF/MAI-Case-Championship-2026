'use client';

import { useEffect, useRef, useState } from 'react';
import { useAtlas } from '@/lib/store';
import { encodeProfile } from '@/lib/profile';
import { Icon } from '@/components/UI/Icon';

export function ShareButton() {
	const profile = useAtlas((s) => s.profile);
	const [toast, setToast] = useState<{ ok: boolean } | null>(null);
	const timer = useRef<number | undefined>(undefined);

	useEffect(() => () => window.clearTimeout(timer.current), []);

	const onClick = async () => {
		let ok = true;
		try {
			const url = new URL(window.location.href);
			url.searchParams.set('p', encodeProfile(profile));
			await navigator.clipboard.writeText(url.toString());
		} catch {
			ok = false;
		}
		setToast({ ok });
		window.clearTimeout(timer.current);
		timer.current = window.setTimeout(() => setToast(null), 2600);
	};

	return (
		<>
			<button
				onClick={onClick}
				title="Поделиться ссылкой"
				aria-label="Скопировать ссылку с этим профилем"
				className="glass fixed bottom-[5.75rem] right-6 z-40 grid h-14 w-14 place-items-center rounded-2xl text-brand-600 transition-all duration-300 ease-spring hover:-translate-y-0.5 hover:text-brand-700 active:scale-95"
			>
				<Icon name="share" className="h-6 w-6" />
			</button>

			{toast && (
				<div
					role="status"
					className="glass-strong fixed bottom-[6.1rem] right-24 z-40 flex items-center gap-2.5 rounded-2xl py-2.5 pl-3 pr-4 text-sm font-bold text-ink-800 animate-scale-in"
				>
					<span className={`grid h-6 w-6 place-items-center rounded-full text-white ${toast.ok ? 'bg-emerald-500' : 'bg-rose-500'}`}>
						<Icon name={toast.ok ? 'check' : 'x'} className="h-3.5 w-3.5" strokeWidth={3} />
					</span>
					{toast.ok ? 'Ссылка скопирована!' : 'Не удалось скопировать ссылку'}
				</div>
			)}
		</>
	);
}
