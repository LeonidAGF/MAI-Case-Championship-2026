'use client';

import { useEffect, useState } from 'react';
import { useAtlas } from '@/lib/store';
import { fetchExplain, ExplainResponse, WaterfallStep, ProCon } from '@/lib/api';

export function TerritoryCard() {
	const selectedTerritoryId = useAtlas((s) => s.selectedTerritoryId);
	const selectTerritory = useAtlas((s) => s.selectTerritory);
	const profile = useAtlas((s) => s.profile);
	
	const [data, setData] = useState<ExplainResponse | null>(null);

	useEffect(() => {
		if (selectedTerritoryId == null) {
			setData(null);
			return;
		}
		let cancelled = false;
		(async () => {
			try {
				const exp = await fetchExplain(selectedTerritoryId, profile);
				if (!cancelled) setData(exp);
			} catch (e) {
				console.error("Ошибка загрузки карточки:", e);
			}
		})();
		return () => { cancelled = true; };
	}, [selectedTerritoryId, profile]);

	if (!data) return null;

	return (
		<aside className="absolute top-24 left-4 w-96 bg-white/95 backdrop-blur shadow-2xl rounded-xl p-5 border border-gray-100 z-10 max-h-[80vh] overflow-y-auto">
			<div className="flex justify-between items-start mb-4">
				<div>
					<h2 className="text-gray-500 text-sm font-medium">{data.cluster_name}</h2>
					<h3 className="text-lg font-bold text-gray-800">{data.name}</h3>
					<div className="text-5xl font-extrabold text-blue-900 mt-1">{data.total.toFixed(1)}</div>
					<div className="text-xs text-gray-400 mt-1">Типичный балл: {data.baseline.toFixed(1)}</div>
				</div>
				<button onClick={() => selectTerritory(null)} className="text-gray-400 hover:text-black hover:bg-gray-100 p-2 rounded-full transition">
					✕
				</button>
			</div>

			<Waterfall steps={data.waterfall} />

			<div className="mt-6 space-y-4">
				<div>
					<h3 className="text-sm font-semibold text-green-700 mb-2">Топ-плюсы</h3>
					<ul className="space-y-1">
						{data.pluses.map((c) => (
							<li key={c.feature_code} className="text-sm flex justify-between">
								<span className="text-gray-700">{c.text}</span>
								<span className="text-green-600 font-medium">+{c.delta.toFixed(1)}</span>
							</li>
						))}
					</ul>
				</div>
				<div>
					<h3 className="text-sm font-semibold text-red-700 mb-2">Топ-минусы</h3>
					<ul className="space-y-1">
						{data.minuses.map((c) => (
							<li key={c.feature_code} className="text-sm flex justify-between">
								<span className="text-gray-700">{c.text}</span>
								<span className="text-red-600 font-medium">{c.delta.toFixed(1)}</span>
							</li>
						))}
					</ul>
				</div>
			</div>
		</aside>
	);
}

function Waterfall({ steps }: { steps: WaterfallStep[] }) {
	const sorted = [...steps].sort((a, b) => b.contribution - a.contribution);
	const max = Math.max(...sorted.map((c) => Math.abs(c.contribution)), 1);
	
	return (
		<div className="space-y-1 mt-4 border-t pt-4">
			{sorted.map((c) => (
				<div key={c.feature_code} className="flex items-center gap-2 text-xs" title={c.text}>
					<span className="w-10 shrink-0 text-gray-500 font-mono">{c.feature_code}</span>
					<div className="flex-1 h-2 bg-gray-100 relative rounded">
						<div
							className={`absolute h-2 rounded ${c.contribution >= 0 ? 'bg-green-500' : 'bg-red-500'}`}
							style={{ width: `${(Math.abs(c.contribution) / max) * 100}%` }}
						/>
					</div>
					<span className="w-10 text-right font-medium">{c.contribution.toFixed(1)}</span>
				</div>
			))}
		</div>
	);
}