'use client';

import { useState } from 'react';
import { useAtlas } from '@/lib/store';
import { API } from '@/lib/api';

export function ChatWidget() {
	const profile = useAtlas((s) => s.profile);
	const [q, setQ] = useState('');
	const [log, setLog] = useState<Array<{ role: 'user' | 'assistant'; text: string }>>([]);

	const send = async () => {
		if (!q.trim()) return;
		setLog((l) => [...l, { role: 'user', text: q }]);
		const res = await fetch(`${API}/chat`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ messages: [{ role: 'user', content: q }], profile }),
		});
		if (!res.body) return;
		const reader = res.body.getReader();
		const dec = new TextDecoder();
		let acc = '';
		setLog((l) => [...l, { role: 'assistant', text: '' }]);
		while (true) {
			const { done, value } = await reader.read();
			if (done) break;
			acc += dec.decode(value, { stream: true });
			setLog((l) => {
				const copy = [...l];
				copy[copy.length - 1] = { role: 'assistant', text: acc };
				return copy;
			});
		}
		setQ('');
	};

	return (
		<div className="fixed bottom-4 right-4 w-80 bg-white shadow-xl rounded-lg p-3">
			<div className="max-h-60 overflow-y-auto text-sm space-y-1">
				{log.map((m, i) => (
				<div key={i} className={m.role === 'user' ? 'text-right' : ''}>
					<b>{m.role === 'user' ? 'Я' : 'Атлас'}:</b> {m.text}
				</div>
				))}
			</div>
			<div className="flex gap-2 mt-2">
				<input value={q} onChange={(e) => setQ(e.target.value)}
					onKeyDown={(e) => e.key === 'Enter' && send()}
					className="flex-1 border rounded px-2 py-1 text-sm" placeholder="Спросить…" />
				<button onClick={send} className="px-2 rounded bg-blue-600 text-white text-sm">→</button>
			</div>
		</div>
	);
}