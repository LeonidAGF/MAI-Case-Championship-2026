'use client';

import { useState } from 'react';
import { useAtlas } from '@/lib/store';
import { runWhatIf, WhatIfResponse } from '@/lib/api';

const SCENARIOS = [
	{ code: 'W1', label: 'Открыть ФАП/больницу в точке' },
	{ code: 'W2', label: 'Закрыть больницу (ремонт/карантин)' },
	{ code: 'W3', label: 'Открыть детский сад/школу' },
	{ code: 'W4', label: 'Открыть колледж/филиал вуза' },
	{ code: 'W5', label: 'Открыть ЖД-платформу' },
	{ code: 'W6', label: 'Ремонт трассы до райцентра' },
	{ code: 'W7', label: 'Автобусный маршрут село → райцентр' },
	{ code: 'W8', label: 'Расширение аэропорта' },
	{ code: 'W9', label: 'Открыть ТЦ/кинотеатр/парк' },
	{ code: 'W10', label: 'Пакет W1+W3+W7 для района' },
];

export function WhatIfPanel() {
	const profile = useAtlas((s) => s.profile);
	const selectedTerritoryId = useAtlas((s) => s.selectedTerritoryId);
	
	const [code, setCode] = useState('W1');
	const [result, setResult] = useState<WhatIfResponse | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const [params, setParams] = useState<Record<string, unknown>>({ 
		lat: 42.9764, 
		lon: 47.5047, 
		kind: 'fap' 
	});

	const run = async () => {
		setLoading(true);
		setError(null);
		try {
			const reqParams = { ...params };
			if (selectedTerritoryId) {
				reqParams.territory_id = selectedTerritoryId;
			}
			
			const res = await runWhatIf(code, reqParams, profile);
			setResult(res);
		} catch (e: any) {
			setError(e.message || 'Ошибка выполнения сценария');
		} finally {
			setLoading(false);
		}
	};

	return (
		<div className="space-y-6">
			<div className="flex gap-4 items-end bg-gray-50 p-4 rounded-xl border">
				<div className="flex-1">
					<label className="block text-xs font-medium text-gray-500 mb-1">Выберите сценарий</label>
					<select value={code} onChange={(e) => setCode(e.target.value)} className="w-full border rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-blue-500 outline-none">
						{SCENARIOS.map((s) => <option key={s.code} value={s.code}>{s.code} — {s.label}</option>)}
					</select>
				</div>

				<div className="flex gap-2">
					<div>
						<label className="block text-xs font-medium text-gray-500 mb-1">Lat</label>
						<input type="number" step="0.01" value={params.lat as number} onChange={(e) => setParams({...params, lat: Number(e.target.value)})} className="w-24 border rounded-lg px-2 py-2 text-sm" />
					</div>
					<div>
						<label className="block text-xs font-medium text-gray-500 mb-1">Lon</label>
						<input type="number" step="0.01" value={params.lon as number} onChange={(e) => setParams({...params, lon: Number(e.target.value)})} className="w-24 border rounded-lg px-2 py-2 text-sm" />
					</div>
				</div>

				<button onClick={run} disabled={loading} className="px-6 py-2 rounded-lg bg-blue-600 text-white font-medium hover:bg-blue-700 disabled:opacity-50 transition">
					{loading ? 'Считаем...' : 'Запустить'}
				</button>
			</div>

			{error && (
				<div className="bg-red-50 text-red-700 p-4 rounded-lg text-sm border border-red-100">
					<b>Ошибка:</b> {error}
				</div>
			)}

			{result && (
				<div className="space-y-6">
					{/* Заголовок и объяснение */}
					<div className="bg-blue-50 border border-blue-100 p-4 rounded-xl">
						<h3 className="font-bold text-blue-900 mb-2">{result.scenario_name}</h3>
						<p className="text-sm text-blue-800">{result.explanation}</p>
					</div>

					<div className="flex gap-4">
						<div className="flex-1 bg-white border rounded-xl p-4 shadow-sm">
							<div className="text-sm text-gray-500">Затронуто территорий</div>
							<div className="text-3xl font-bold text-gray-800">{result.affected}</div>
						</div>
						<div className="flex-1 bg-white border rounded-xl p-4 shadow-sm">
							<div className="text-sm text-gray-500">Метод пересчета</div>
							<div className="text-lg font-medium text-gray-800 uppercase">{result.method_used}</div>
						</div>
					</div>

					{/* Таблица результатов */}
					<div className="bg-white border rounded-xl overflow-hidden shadow-sm">
						<table className="w-full text-sm text-left">
							<thead className="bg-gray-50 border-b">
								<tr>
									<th className="px-4 py-3 font-medium text-gray-600">Территория</th>
									<th className="px-4 py-3 font-medium text-gray-600 text-center">Было</th>
									<th className="px-4 py-3 font-medium text-gray-600 text-center">Стало</th>
									<th className="px-4 py-3 font-medium text-gray-600 text-center">Дельта</th>
								</tr>
							</thead>
							<tbody className="divide-y">
								{result.results.map((r) => (
									<tr key={r.territory_id} className="hover:bg-gray-50">
										<td className="px-4 py-3 font-medium">{r.name}</td>
										<td className="px-4 py-3 text-center text-gray-500">{r.total_before.toFixed(1)}</td>
										<td className="px-4 py-3 text-center font-medium">{r.total_after.toFixed(1)}</td>
										<td className={`px-4 py-3 text-center font-bold ${r.delta > 0 ? 'text-green-600' : r.delta < 0 ? 'text-red-600' : 'text-gray-400'}`}>
											{r.delta > 0 ? '+' : ''}{r.delta.toFixed(1)}
										</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>

					{/* Победители и проигравшие */}
					<div className="grid grid-cols-2 gap-4">
						<div className="bg-green-50 rounded-xl p-4 border border-green-100">
							<h4 className="font-bold text-green-800 mb-2 flex items-center gap-2">Победители</h4>
							<ul className="text-sm space-y-1">
								{result.winners.length > 0 ? result.winners.map(w => (
									<li key={w.territory_id} className="flex justify-between">
										<span className="text-green-900">{w.name}</span>
										<span className="font-bold text-green-700">+{w.delta.toFixed(1)}</span>
									</li>
								)) : <li className="text-green-600/70 italic">Нет</li>}
							</ul>
						</div>
						<div className="bg-red-50 rounded-xl p-4 border border-red-100">
							<h4 className="font-bold text-red-800 mb-2 flex items-center gap-2">Проигравшие</h4>
							<ul className="text-sm space-y-1">
								{result.losers.length > 0 ? result.losers.map(l => (
									<li key={l.territory_id} className="flex justify-between">
										<span className="text-red-900">{l.name}</span>
										<span className="font-bold text-red-700">{l.delta.toFixed(1)}</span>
									</li>
								)) : <li className="text-red-600/70 italic">Нет</li>}
							</ul>
						</div>
					</div>
				</div>
			)}
		</div>
	);
}