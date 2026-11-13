'use client';

import { useEffect, useState } from 'react';
import { useAtlas } from '@/lib/store';
import { 
	fetchExplain, ExplainResponse, WaterfallStep,
	fetchAmbulanceSettlement, AmbulanceSettlement, AmbulanceNormStatus
} from '@/lib/api';
import { FEATURE_LABELS } from '@/lib/dictionary';

const NORM_LABEL: Record<AmbulanceNormStatus, string> = {
	met: 'в нормативе',
	borderline: 'на границе норматива',
	not_met: 'дольше норматива',
};

const NORM_COLOR: Record<AmbulanceNormStatus, string> = {
	met: 'bg-green-100 text-green-800',
	borderline: 'bg-yellow-100 text-yellow-800',
	not_met: 'bg-red-100 text-red-800',
};

export function TerritoryCard() {
	const selectedTerritoryId = useAtlas((s) => s.selectedTerritoryId);
	const selectTerritory = useAtlas((s) => s.selectTerritory);
	const profile = useAtlas((s) => s.profile);
	
	const [data, setData] = useState<ExplainResponse | null>(null);
	const [ambulance, setAmbulance] = useState<AmbulanceSettlement | null>(null);

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

	useEffect(() => {
		if (selectedTerritoryId == null) return;
		let cancelled = false;
		fetchAmbulanceSettlement(selectedTerritoryId)
			.then((a) => { if (!cancelled) setAmbulance(a); })
			.catch(() => { if (!cancelled) setAmbulance(null); });
		return () => { cancelled = true; };
	}, [selectedTerritoryId]);

	if (!data) return null;

	const amb = ambulance?.territory_id === selectedTerritoryId ? ambulance : null;

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

			{amb && amb.available && amb.estimate && (
				<div className="mt-6 pt-4 border-t border-gray-100">
					<h3 className="text-sm font-semibold text-gray-800 mb-2">Скорая помощь</h3>
					
					<div className="flex items-baseline gap-2 mb-1">
						<span className="text-3xl font-bold text-gray-900">
							{amb.estimate.minutes < 1 ? '< 1' : Math.round(amb.estimate.minutes)}
						</span>
						<span className="text-sm font-medium text-gray-500">мин</span>
					</div>
					
					<div className="text-xs text-gray-500 mb-2">
						от {Math.round(amb.estimate.minutes_low)} до {Math.round(amb.estimate.minutes_high)} мин · время в пути
					</div>

					<div className={`inline-block px-2 py-1 rounded text-xs font-medium mb-3 ${NORM_COLOR[amb.estimate.norm_status]}`}>
						{NORM_LABEL[amb.estimate.norm_status]} {amb.estimate.norm_minutes} мин
					</div>

					<p className="text-xs text-gray-600 leading-relaxed mb-2">
						{amb.text}
					</p>

					{amb.notes && amb.notes.length > 0 && (
						<ul className="text-[10px] text-gray-400 space-y-1 list-disc pl-4">
							{amb.notes.map((note, i) => <li key={i}>{note}</li>)}
						</ul>
					)}
					
					{amb.stale && (
						<div className="mt-2 text-xs text-orange-600 bg-orange-50 p-2 rounded">
							Данные нужно пересчитать
						</div>
					)}
				</div>
			)}

			{amb && !amb.available && amb.text && (
				<div className="mt-6 pt-4 border-t border-gray-100">
					<h3 className="text-sm font-semibold text-gray-800 mb-2">Скорая помощь</h3>
					<div className="inline-block px-2 py-1 rounded text-xs font-medium bg-gray-100 text-gray-600 mb-2">
						нет оценки
					</div>
					<p className="text-xs text-gray-600">{amb.text}</p>
				</div>
			)}

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