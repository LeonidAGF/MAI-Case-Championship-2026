'use client';

import { useEffect, useState } from 'react';
import { useAtlas } from '@/lib/store';
import { postCompare, CompareResponse } from '@/lib/api';

export function CompareView({ ids }: { ids: number[] }) {
	const profile = useAtlas((s) => s.profile);
	const [data, setData] = useState<CompareResponse | null>(null);

	useEffect(() => {
		if (ids.length < 2) return;
		postCompare(ids, profile).then(setData).catch(console.error);
	}, [ids, profile]);

	if (!data) return <div className="p-4">Загрузка...</div>;

	return (
		<div className="p-4">
			<h2 className="text-xl font-bold mb-4">Сравнение (Кластер: {data.cluster_name})</h2>
			<div className="flex gap-4">
				{data.items.map((r) => (
					<div key={r.territory_id} className="flex-1 border rounded p-3 shadow-sm">
						<div className="text-lg font-semibold">{r.name}</div>
						<div className="text-3xl font-bold">{r.total.toFixed(1)}</div>
						<SubindicesBar sub={r.subindices} />
						{r.attractiveness && (
							<div className="mt-2 text-xs">Привлекательность: {r.attractiveness.score} ({r.attractiveness.badge})</div>
						)}
					</div>
				))}
			</div>
			<Radar data={data} />
		</div>
	);
}

function SubindicesBar({ sub }: { sub: Record<string, number | null> }) {
	return (
		<div className="mt-2 space-y-1 text-xs">
			{Object.entries(sub).map(([k, v]) => (
				<div key={k} className="flex items-center gap-2">
					<span className="w-6">{k}</span>
					<div className="flex-1 h-1.5 bg-gray-100">
						<div className="h-1.5 bg-blue-500" style={{ width: `${v ?? 0}%` }} />
					</div>
					<span>{v !== null ? v.toFixed(0) : '—'}</span>
				</div>
			))}
		</div>
	);
}

function Radar({ data }: { data: CompareResponse }) {
	const { axes, series } = data.radar;
	const cx = 150, cy = 150, R = 120;
	
	const angle = (i: number) => (Math.PI * 2 * i) / axes.length - Math.PI / 2;
	const point = (i: number, val: number) => {
		const r = (val / 100) * R;
		return [cx + r * Math.cos(angle(i)), cy + r * Math.sin(angle(i))];
	};
	const colors = ['#2563eb', '#16a34a', '#dc2626'];

	return (
		<svg viewBox="0 0 300 300" className="w-72 h-72 mt-6 mx-auto">
			{[25,50,75,100].map((ring) => (
				<polygon key={ring}
					points={axes.map((_: any, i: number) => point(i, ring).join(',')).join(' ')}
					fill="none" stroke="#e5e7eb" />
			))}
			{axes.map((l: any, i: number) => {
				const [x, y] = point(i, 108);
				return <text key={l.code} x={x} y={y} textAnchor="middle" className="text-xs" fill="#6b7280">{l.code}</text>;
			})}
			{series.map((r: any, idx: number) => (
				<polygon key={r.territory_id}
					points={r.values.map((v: number | null, i: number) => point(i, v ?? 0).join(',')).join(' ')}
					fill={colors[idx]} fillOpacity={0.15} stroke={colors[idx]} />
			))}
		</svg>
	);
}