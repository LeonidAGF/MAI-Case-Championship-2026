'use client';

import { useState } from 'react';
import { useAtlas } from '@/lib/store';
import { runWhatIf, WhatIfResponse } from '@/lib/api';

const SCENARIOS = [
	{ code: 'w1', label: 'Открыть ФАП/больницу в точке' },
	{ code: 'w2', label: 'Закрыть больницу (ремонт/карантин)' },
	{ code: 'w3', label: 'Открыть детский сад/школу' },
	{ code: 'w4', label: 'Открыть колледж/филиал вуза' },
	{ code: 'w5', label: 'Открыть ЖД-платформу' },
	{ code: 'w6', label: 'Ремонт трассы до райцентра' },
	{ code: 'w7', label: 'Автобусный маршрут село → райцентр' },
	{ code: 'w8', label: 'Расширение аэропорта' },
	{ code: 'w9', label: 'Открыть ТЦ/кинотеатр/парк' },
	{ code: 'w10', label: 'Пакет W1+W3+W7 для района' },
];

export function WhatIfPanel() {
	const profile = useAtlas((s) => s.profile);
	const [code, setCode] = useState('w1');
	const [result, setResult] = useState<WhatIfResponse | null>(null);
	const [loading, setLoading] = useState(false);
	const [params] = useState<Record<string, unknown>>({ lat: 43.15, lon: 46.86, kind: 'fap' });

	const run = async () => {
		setLoading(true);
		try {
			const res = await runWhatIf(code, params, profile);
			setResult(res);
		} catch (e) {
			console.error(e);
		} finally {
			setLoading(false);
		}
	};

	return (
		<div className="p-4 space-y-3">
			<select value={code} onChange={(e) => setCode(e.target.value)} className="border rounded px-2 py-1">
				{SCENARIOS.map((s) => <option key={s.code} value={s.code}>{s.label}</option>)}
			</select>

			<button onClick={run} disabled={loading} className="px-3 py-1 rounded bg-blue-600 text-white disabled:opacity-50">
				{loading ? 'Считаем...' : 'Пересчитать'}
			</button>

			{result && (
				<div className="space-y-2">
					<div className="text-sm text-gray-600">Затронуто территорий: {result.affected}</div>
					<table className="w-full text-sm">
						<thead>
							<tr>
								<th className="text-left">Территория</th>
								<th>До</th>
								<th>После</th>
								<th>Δ</th>
							</tr>
						</thead>
						<tbody>
							{result.results.map((r) => (
								<tr key={r.territory_id} className="border-t">
									<td className="text-left py-1">{r.name}</td>
									<td className="text-center">{r.total_before.toFixed(1)}</td>
									<td className="text-center">{r.total_after.toFixed(1)}</td>
									<td className={`text-center font-medium ${r.total_after >= r.total_before ? 'text-green-700' : 'text-red-700'}`}>
										{r.delta > 0 ? '+' : ''}{r.delta.toFixed(1)}
									</td>
								</tr>
							))}
						</tbody>
					</table>
					<div className="text-sm bg-gray-50 p-2 rounded">
						<div>🏆 Победители: {result.winners.map(w => w.name).join(', ') || '—'}</div>
						<div>📉 Проигравшие: {result.losers.map(l => l.name).join(', ') || '—'}</div>
					</div>
				</div>
			)}
		</div>
	);
}