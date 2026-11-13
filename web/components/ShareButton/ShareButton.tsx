'use client';

import { useAtlas } from '@/lib/store';
import { encodeProfile } from '@/lib/profile';

export function ShareButton() {
	const profile = useAtlas((s) => s.profile);

	const onClick = async () => {
		try {
			const url = new URL(window.location.href);
			url.searchParams.set('p', encodeProfile(profile));
			await navigator.clipboard.writeText(url.toString());
			alert('Ссылка скопирована!');
		} catch {
			alert('Не удалось скопировать ссылку');
		}
	};

	return (
		<button
			onClick={onClick}
			className="fixed bottom-[5.5rem] right-6 w-14 h-14 bg-emerald-600 text-white rounded-full shadow-xl hover:bg-emerald-700 transition flex items-center justify-center z-40"
			title="Поделиться ссылкой"
		>
			<svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z" />
			</svg>
		</button>
	);
}