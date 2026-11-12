'use client';

import { useEffect, useState } from 'react';
import { useAtlas } from '@/lib/store';
import { fetchExplain, ExplainResponse, WaterfallStep } from '@/lib/api';
import { FEATURE_LABELS } from '@/lib/dictionary';

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
		<aside className="absolute z-20 bg-white/95 backdrop-blur shadow-2xl border border-gray-100 overflow-y-auto transition-all
			bottom-0 left-0 w-full max-h-[60vh] rounded-t-2xl p-4
			md:top-24 md:left-4 md:bottom-auto md:w-96 md:max-h-[80vh] md:rounded-xl md:p-5"
		>
			<div className="md:hidden w-12 h-1.5 bg-gray-300 rounded-full mx-auto mb-3" />

			<div className="flex justify-between items-start mb-4">
				<div>
					<h2 className="text-gray-500 text-xs md:text-sm font-medium">{data.cluster_name}</h2>
					<h3 className="text-base md:text-lg font-bold text-gray-800">{data.name}</h3>
					<div className="text-4xl md:text-5xl font-extrabold text-blue-900 mt-1">{data.total.toFixed(1)}</div>
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
							<li key={c.feature_code} className="text-sm flex justify-between gap-4">
								<span className="text-gray-700 leading-tight">{c.text}</span>
								<span className="text-green-600 font-medium whitespace-nowrap">+{c.delta.toFixed(1)}</span>
							</li>
						))}
					</ul>
				</div>
				<div>
					<h3 className="text-sm font-semibold text-red-700 mb-2">Топ-минусы</h3>
					<ul className="space-y-1">
						{data.minuses.map((c) => (
							<li key={c.feature_code} className="text-sm flex justify-between gap-4">
								<span className="text-gray-700 leading-tight">{c.text}</span>
								<span className="text-red-600 font-medium whitespace-nowrap">{c.delta.toFixed(1)}</span>
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
		<div className="space-y-3 mt-4 border-t pt-4">
			{sorted.map((c) => (
				<div key={c.feature_code} className="flex flex-col gap-1 text-xs">
					<div className="flex justify-between items-center text-gray-700 font-medium pr-1">
						<span className="truncate pr-2" title={FEATURE_LABELS[c.feature_code] || c.label || c.feature_code}>
							{FEATURE_LABELS[c.feature_code] || c.label || c.feature_code}
						</span>
						<span className={`shrink-0 ${c.contribution >= 0 ? 'text-green-600' : 'text-red-600'}`}>
							{c.contribution > 0 ? '+' : ''}{c.contribution.toFixed(1)}
						</span>
					</div>
					
					<div className="w-full h-1.5 bg-gray-100 relative rounded overflow-hidden">
						<div
							className={`absolute h-1.5 rounded ${c.contribution >= 0 ? 'bg-green-500' : 'bg-red-500'}`}
							style={{ width: `${(Math.abs(c.contribution) / max) * 100}%` }}
						/>
					</div>
				</div>
			))}
		</div>
	);
}