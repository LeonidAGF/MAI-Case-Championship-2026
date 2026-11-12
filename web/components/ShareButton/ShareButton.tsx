'use client';

import { useAtlas } from '@/lib/store';
import { encodeProfile } from '@/lib/profile';

export function ShareButton() {
	const profile = useAtlas((s) => s.profile);

	const onClick = async () => {
		try {
			const url = new URL(window.location.href);
			
			const encoded = encodeProfile(profile);
			url.searchParams.set('p', encoded);
			
			const finalUrl = url.toString();
			await navigator.clipboard.writeText(finalUrl);
			alert(`Ссылка скопирована! Длина: ${finalUrl.length} симв.`);
		} catch (e) {
			console.error(e);
			alert('Не удалось скопировать ссылку');
		}
	};

	return (
		<button 
			onClick={onClick} 
			className="px-3 py-1 rounded bg-emerald-600 text-white hover:bg-emerald-700 transition text-sm shrink-0"
		>
			Поделиться
		</button>
	);
}