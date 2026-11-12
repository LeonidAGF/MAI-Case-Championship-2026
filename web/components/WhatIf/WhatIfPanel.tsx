'use client';

import { useState, useEffect } from 'react';
import { useAtlas } from '@/lib/store';
import { 
  runWhatIf, fetchScenarios, fetchTerritories, 
  WhatIfResponse, ScenarioInfo, ApiError, TerritoryBrief 
} from '@/lib/api';

const AIRPORTS = [
	{ name: 'Минеральные Воды', lat: 44.225, lon: 43.082 },
	{ name: 'Махачкала (Уйташ)', lat: 42.817, lon: 47.652 },
	{ name: 'Грозный', lat: 43.388, lon: 45.699 },
	{ name: 'Владикавказ (Беслан)', lat: 43.205, lon: 44.607 },
	{ name: 'Нальчик', lat: 43.513, lon: 43.637 },
	{ name: 'Магас', lat: 43.322, lon: 45.013 },
	{ name: 'Ставрополь', lat: 45.109, lon: 42.113 },
];

export function WhatIfPanel() {
	const profile = useAtlas((s) => s.profile);
	// const selectedTerritoryId = useAtlas((s) => s.selectedTerritoryId);

	const [scenarios, setScenarios] = useState<ScenarioInfo[]>([]);
	const [selectedCode, setSelectedCode] = useState('W1');
	const [params, setParams] = useState<Record<string, any>>({});
	
	const [result, setResult] = useState<WhatIfResponse | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<ApiError | null>(null);

	const [searchQuery, setSearchQuery] = useState('');
	const [searchResults, setSearchResults] = useState<TerritoryBrief[]>([]);
	const [municipalities, setMunicipalities] = useState<TerritoryBrief[]>([]);

	useEffect(() => {
		fetchScenarios().then(data => {
			setScenarios(data);
			const first = data.find(s => s.code === selectedCode) || data[0];
			if (first) setParams(first.example || {});
		}).catch(console.error);
	}, []);

	useEffect(() => {
		const s = scenarios.find(s => s.code === selectedCode);
		if (s) {
			setParams(s.example || {});
			setResult(null);
			setError(null);
			setSearchQuery('');
			setSearchResults([]);
		}
	}, [selectedCode, scenarios]);

	const activeScenario = scenarios.find(s => s.code === selectedCode);

	useEffect(() => {
		if (activeScenario?.target === 'municipality' && municipalities.length === 0) {
			fetchTerritories({ level: 'municipality', limit: 500 }).then(res => setMunicipalities(res.items));
		}
	}, [activeScenario]);

	useEffect(() => {
		if (searchQuery.length < 2) {
			setSearchResults([]);
			return;
		}
		const timer = setTimeout(() => {
			fetchTerritories({ level: 'settlement', q: searchQuery, limit: 10 })
				.then(res => setSearchResults(res.items))
				.catch(console.error);
		}, 300);
		return () => clearTimeout(timer);
	}, [searchQuery]);

	const updateParam = (key: string, value: any) => {
		setParams(prev => ({ ...prev, [key]: value }));
	};

	const run = async () => {
		if (!activeScenario) return;
		setLoading(true);
		setError(null);

		const cleanParams = Object.fromEntries(
			Object.entries(params).filter(([_, v]) => v !== '' && v !== null && v !== undefined)
		);

		try {
			const res = await runWhatIf(selectedCode, cleanParams, profile);
			setResult(res);
		} catch (e: any) {
			if (e instanceof ApiError) setError(e);
			else setError(new ApiError(e.message));
		} finally {
			setLoading(false);
		}
	};

	if (scenarios.length === 0) return <div>Загрузка каталога...</div>;

	return (
		<div className="space-y-6">
			{/* Панель управления */}
			<div className="bg-gray-50 p-4 rounded-xl border space-y-4">
				
				{/* Выбор сценария */}
				<div>
					<label className="block text-xs font-medium text-gray-500 mb-1">Сценарий</label>
					<select 
						value={selectedCode} 
						onChange={(e) => setSelectedCode(e.target.value)} 
						className="w-full border rounded-lg px-3 py-2 text-sm bg-white"
					>
						{scenarios.map((s) => <option key={s.code} value={s.code}>{s.code} — {s.name}</option>)}
					</select>
					{activeScenario && <p className="text-xs text-gray-500 mt-1">{activeScenario.description}</p>}
				</div>

				{/* Форма параметров */}
				<div className="grid grid-cols-1 md:grid-cols-2 gap-4">
				
					{/* ВЫБОР РАЙОНА */}
					{activeScenario?.target === 'municipality' && (
						<div className="md:col-span-2">
							<label className="block text-xs font-medium text-gray-500 mb-1">Район / Муниципалитет</label>
							<select 
								value={params.municipality_id || ''} 
								onChange={(e) => updateParam('municipality_id', Number(e.target.value))}
								className="w-full border rounded-lg px-3 py-2 text-sm bg-white"
							>
								<option value="">Выберите район...</option>
								{municipalities.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
							</select>
						</div>
					)}

					{/* ВЫБОР ТОЧКИ (Поиск или координаты) */}
					{activeScenario?.target === 'point' && (
						<>
							{/* Для W8 - выбор аэропорта */}
							{selectedCode === 'W8' ? (
								<div className="md:col-span-2">
									<label className="block text-xs font-medium text-gray-500 mb-1">Аэропорт</label>
									<select 
										onChange={(e) => {
										const ap = AIRPORTS.find(a => a.name === e.target.value);
										if (ap) setParams(prev => ({ ...prev, lat: ap.lat, lon: ap.lon }));
										}}
										className="w-full border rounded-lg px-3 py-2 text-sm bg-white"
										defaultValue=""
									>
										<option value="" disabled>Выберите аэропорт...</option>
										{AIRPORTS.map(a => <option key={a.name} value={a.name}>{a.name}</option>)}
									</select>
								</div>
							) : (
								<div className="md:col-span-2 relative">
									<label className="block text-xs font-medium text-gray-500 mb-1">Поиск населенного пункта (или введите координаты)</label>
									<input 
										type="text" 
										placeholder="Например: Махачкала"
										value={searchQuery}
										onChange={(e) => setSearchQuery(e.target.value)}
										className="w-full border rounded-lg px-3 py-2 text-sm"
									/>
									{searchResults.length > 0 && (
										<ul className="absolute z-10 w-full bg-white border mt-1 rounded-lg shadow-lg max-h-48 overflow-auto">
											{searchResults.map(t => (
												<li 
													key={t.id} 
													className="px-3 py-2 hover:bg-blue-50 cursor-pointer text-sm"
													onClick={() => {
														setParams(prev => ({ ...prev, lat: t.centroid?.lat, lon: t.centroid?.lon }));
														setSearchQuery(t.name);
														setSearchResults([]);
													}}
												>
													{t.name}
												</li>
											))}
										</ul>
									)}
								</div>
							)}

							{/* Координаты */}
							<div>
								<label className="block text-xs font-medium text-gray-500 mb-1">Lat</label>
								<input type="number" step="0.001" value={params.lat || ''} onChange={(e) => updateParam('lat', Number(e.target.value))} className="w-full border rounded-lg px-3 py-2 text-sm" />
							</div>
							<div>
								<label className="block text-xs font-medium text-gray-500 mb-1">Lon</label>
								<input type="number" step="0.001" value={params.lon || ''} onChange={(e) => updateParam('lon', Number(e.target.value))} className="w-full border rounded-lg px-3 py-2 text-sm" />
							</div>
						</>
					)}

					{/* KIND (Тип объекта) */}
					{activeScenario?.kinds && activeScenario.kinds.length > 0 && (
						<div>
							<label className="block text-xs font-medium text-gray-500 mb-1">Тип объекта</label>
							<select 
								value={params.kind || ''} 
								onChange={(e) => updateParam('kind', e.target.value)}
								className="w-full border rounded-lg px-3 py-2 text-sm bg-white"
							>
								{activeScenario.kinds.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
							</select>
						</div>
					)}

					{/* ЧИСЛОВЫЕ ПАРАМЕТРЫ (из params_schema) */}
					{activeScenario?.params_schema?.properties && Object.entries(activeScenario.params_schema.properties).map(([key, schema]: [string, any]) => {
						if (['lat', 'lon', 'kind', 'municipality_id', 'territory_ids'].includes(key)) return null;
						if (key === 'park_area_ha' && params.kind !== 'park') return null;

						return (
						<div key={key}>
							<label className="block text-xs font-medium text-gray-500 mb-1">
								{schema.description || key} {schema.minimum !== undefined && `(мин: ${schema.minimum})`}
							</label>
							<input 
								type="number" 
								min={schema.minimum} max={schema.maximum}
								value={params[key] ?? ''} 
								onChange={(e) => updateParam(key, Number(e.target.value))} 
								className="w-full border rounded-lg px-3 py-2 text-sm" 
							/>
						</div>
						);
					})}
				</div>

				<button onClick={run} disabled={loading} className="w-full py-2 rounded-lg bg-blue-600 text-white font-medium hover:bg-blue-700 disabled:opacity-50 transition">
					{loading ? 'Считаем...' : 'Запустить'}
				</button>
			</div>

			{/* Блок ошибки с деталями */}
			{error && (
				<div className="bg-red-50 text-red-700 p-4 rounded-xl text-sm border border-red-100">
					<b className="block mb-1">{error.message}</b>
					{error.details && (
						<ul className="list-disc pl-5 mt-2 space-y-1">
							{error.details.map((d: any, i: number) => (
								<li key={i}><b>{d.loc?.join('.') || 'Поле'}:</b> {d.msg}</li>
							))}
						</ul>
					)}
				</div>
			)}

			{/* Warnings */}
			{result?.warnings && result.warnings.length > 0 && (
				<div className="bg-yellow-50 text-yellow-800 p-4 rounded-xl text-sm border border-yellow-200">
					{result.warnings.map((w, i) => <div key={i}>⚠️ {w}</div>)}
				</div>
			)}

			{/* План для W10 */}
			{result?.plan && (
				<div className="bg-indigo-50 border border-indigo-100 p-4 rounded-xl">
					<h3 className="font-bold text-indigo-900 mb-2">План пакетного улучшения</h3>
					<ul className="space-y-2">
						{result.plan.map((step, i) => (
							<li key={i} className="text-sm flex gap-2 items-baseline">
								<span className="bg-indigo-200 text-indigo-800 px-2 py-0.5 rounded text-xs font-bold">{step.scenario_code}</span>
								<span className="text-indigo-700">{step.reason}</span>
							</li>
						))}
					</ul>
				</div>
			)}

			{/* Результаты */}
			{result && (
				<div className="space-y-6">
					<div className="bg-blue-50 border border-blue-100 p-4 rounded-xl">
						<h3 className="font-bold text-blue-900 mb-2">{result.scenario_name}</h3>
						<p className="text-sm text-blue-800 mb-2">{result.explanation}</p>
						<div className="text-xs text-blue-600 flex gap-4">
							<span>Затронуто: <b>{result.affected}</b></span>
							<span>Метод: <b>{result.method_used}</b></span>
						</div>
					</div>

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
								{/* Ограничиваем топ-50 по модулю дельты */}
								{result.results
									.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
									.slice(0, 50)
									.map((r) => (
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
						{result.results.length > 50 && (
							<div className="p-3 text-center text-xs text-gray-500 border-t bg-gray-50">
								Показаны топ-50 из {result.results.length}
							</div>
						)}
					</div>
				</div>
			)}
		</div>
	);
}