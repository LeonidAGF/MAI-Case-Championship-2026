'use client';

import { useEffect, useState } from 'react';
import { useAtlas } from '@/lib/store';
import { postCompare, CompareResponse } from '@/lib/api';
import { SUBINDEX_LABELS } from '@/lib/dictionary';

export function CompareView({ ids }: { ids: number[] }) {
	const profile = useAtlas((s) => s.profile);
	const [data, setData] = useState<CompareResponse | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);

	useEffect(() => {
		if (ids.length < 2) {
			setData(null);
			return;
		}
		setLoading(true);
		setError(null);
		postCompare(ids, profile)
			.then(setData)
			.catch((e) => setError(e.message || 'Ошибка при сравнении (возможно, выбраны разные кластеры)'))
			.finally(() => setLoading(false));
	}, [ids, profile]);

	if (ids.length < 2) return <div className="text-center p-8 text-gray-500">Выберите минимум две территории для сравнения.</div>;
	if (loading) return <div className="text-center p-8 text-blue-600 animate-pulse">Считаем индексы...</div>;
	if (error) return <div className="text-center p-8 text-red-600 bg-red-50 rounded-lg">{error}</div>;
	if (!data) return null;

	return (
		<div className="space-y-8">
			<div className="flex md:grid md:grid-cols-3 gap-4 overflow-x-auto snap-x snap-mandatory pb-4 md:pb-0 scrollbar-hide">
				{data.items.map((r, idx) => {
					const color = idx === 0 ? 'border-blue-500' : idx === 1 ? 'border-green-500' : 'border-red-500';
					return (
						<div key={r.territory_id} className={`shrink-0 w-[85vw] md:w-auto snap-center bg-white rounded-2xl shadow-lg border-t-4 ${color} p-5 md:p-6 flex flex-col`}>
							<div className="text-sm font-medium text-gray-500 mb-1">{data.cluster_name}</div>
							<h3 className="text-lg md:text-xl font-bold text-gray-900 mb-4">{r.name}</h3>
							
							<div className="text-4xl md:text-5xl font-black text-gray-800 mb-6">
								{r.total.toFixed(1)}
							</div>
							
							<div className="space-y-4 mt-auto">
								{Object.entries(r.subindices).map(([key, val]) => (
									<div key={key} className="flex flex-col gap-1 text-xs">
										<div className="flex justify-between items-center text-gray-600">
											<span className="font-medium">
												{SUBINDEX_LABELS[key] || key}
											</span>
											<span className="font-bold text-gray-800">{val ? val.toFixed(0) : '—'}</span>
										</div>
										<div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
											<div 
												className={`h-full rounded-full ${idx === 0 ? 'bg-blue-500' : idx === 1 ? 'bg-green-500' : 'bg-red-500'}`} 
												style={{ width: `${val || 0}%` }} 
											/>
										</div>
									</div>
								))}
							</div>
						</div>
					);
				})}
			</div>

			<div className="grid grid-cols-1 md:grid-cols-2 gap-6 md:gap-8 bg-white p-4 md:p-6 rounded-2xl shadow-lg">
				<div className="flex flex-col items-center justify-center">
					<h3 className="text-lg font-bold text-gray-800 mb-4">Профиль территорий</h3>
					<Radar data={data} />
				</div>
				
				<div className="flex flex-col justify-center space-y-4">
					<h3 className="text-lg font-bold text-gray-800 border-b pb-2">Ключевые различия</h3>
					{data.summary.map((text, i) => (
						<div key={i} className="flex gap-3 text-sm text-gray-700 bg-gray-50 p-3 rounded-lg">
							<p>{text}</p>
						</div>
					))}
				</div>
			</div>
		</div>
	);
}

function Radar({ data }: { data: CompareResponse }) {
	const { axes, series } = data.radar;
	const cx = 200, cy = 200, R = 140;
	
	const angle = (i: number) => (Math.PI * 2 * i) / axes.length - Math.PI / 2;
	
	const point = (i: number, val: number) => {
		const r = (val / 100) * R;
		return [cx + r * Math.cos(angle(i)), cy + r * Math.sin(angle(i))];
	};
	
	const colors = ['#2563eb', '#16a34a', '#dc2626'];

	return (
		<svg viewBox="0 0 400 400" className="w-full max-w-sm overflow-visible">
			{[25, 50, 75, 100].map((ring) => (
				<polygon key={ring}
					points={axes.map((_: any, i: number) => point(i, ring).join(',')).join(' ')}
					fill="none" stroke="#e5e7eb" strokeDasharray={ring === 100 ? "none" : "4 4"} />
			))}
		
			{axes.map((l: any, i: number) => {
				const [x, y] = point(i, 118);
				return (
					<g key={l.code}>
						<line x1={cx} y1={cy} x2={point(i, 100)[0]} y2={point(i, 100)[1]} stroke="#e5e7eb" />
						<text x={x} y={y} textAnchor="middle" className="text-xs font-medium" fill="#4b5563">
							{SUBINDEX_LABELS[l.code] || l.name}
						</text>
					</g>
				);
			})}
		
			{series.map((r: any, idx: number) => (
				<polygon key={r.territory_id}
					points={r.values.map((v: number | null, i: number) => point(i, v ?? 0).join(',')).join(' ')}
					fill={colors[idx]} fillOpacity={0.15} stroke={colors[idx]} strokeWidth={2} />
			))}
		</svg>
	);
}