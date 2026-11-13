'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { fetchClimateGrid, fetchClimateRegions, Season } from '@/lib/api';
import { ClimateMap } from '@/components/Climate/ClimateMap';

const SEASONS: { id: Season; label: string }[] = [
	{ id: 'winter', label: 'Зима' },
	{ id: 'spring', label: 'Весна' },
	{ id: 'summer', label: 'Лето' },
	{ id: 'autumn', label: 'Осень' },
];

const COLOR_MAP = {
	green: 'bg-green-100 text-green-800 border-green-200',
	yellow: 'bg-yellow-100 text-yellow-800 border-yellow-200',
	red: 'bg-red-100 text-red-800 border-red-200',
};

export default function ClimatePage() {
	const [season, setSeason] = useState<Season>('summer');

	const { data: grid, isLoading: gridLoading } = useQuery({
		queryKey: ['climate-grid', season],
		queryFn: () => fetchClimateGrid(season),
	});

	const { data: regions } = useQuery({
		queryKey: ['climate-regions', season],
		queryFn: () => fetchClimateRegions(season),
	});

	return (
		<main className="min-h-screen bg-gray-50 pb-12 flex flex-col h-screen overflow-hidden">
			<header className="bg-white p-3 md:p-4 shadow-sm flex justify-between items-center z-20 shrink-0">
				<div>
					<h1 className="text-base md:text-xl font-bold text-gray-800">Карта климата 2025</h1>
					<p className="text-xs text-gray-500">Данные NASA POWER, {grid?.period_label || '2025 год'}</p>
				</div>
				<Link href="/" className="text-xs md:text-sm font-medium text-blue-600 hover:underline shrink-0">
					← Назад к атласу
				</Link>
			</header>

			{/* Переключатель сезонов */}
			<div className="bg-white border-b px-4 py-3 flex gap-2 overflow-x-auto shrink-0 z-10 shadow-sm">
				{SEASONS.map((s) => (
					<button
						key={s.id}
						onClick={() => setSeason(s.id)}
						className={`px-4 py-1.5 rounded-full text-sm font-medium transition whitespace-nowrap ${
							season === s.id ? 'bg-blue-600 text-white shadow' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
						}`}
					>
						{s.label}
					</button>
				))}
			</div>

			{/* Карта и легенда */}
			<div className="relative flex-1 bg-gray-100 min-h-[400px]">
				{gridLoading || !grid ? (
					<div className="absolute inset-0 flex items-center justify-center bg-white/50 z-10">
						<div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
					</div>
				) : (
					<ClimateMap cells={grid.cells} />
				)}

				{/* Легенда */}
				<div className="absolute bottom-6 left-4 bg-white/95 backdrop-blur p-3 rounded-xl shadow-lg border text-xs z-10 space-y-1.5">
					<div className="font-semibold text-gray-800 mb-1">Отклонение от проектного диапазона</div>
					<div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-green-500 block"></span> В пределах диапазона</div>
					<div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-yellow-500 block"></span> Небольшое отклонение</div>
					<div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-red-500 block"></span> Значительное отклонение</div>
				</div>
			</div>

			{/* Таблица регионов */}
			<div className="bg-white p-4 shadow-[0_-4px_6px_-1px_rgb(0,0,0,0.05)] z-10 shrink-0 max-h-[40vh] overflow-y-auto md:max-h-64">
				<h3 className="font-bold text-gray-800 mb-3 text-sm">Сводка по субъектам СКФО</h3>
				<div className="overflow-x-auto">
					<table className="w-full text-left text-sm whitespace-nowrap">
						<thead>
							<tr className="text-gray-500 border-b">
								<th className="pb-2 font-medium">Регион</th>
								<th className="pb-2 font-medium text-center">Ср. темп.</th>
								<th className="pb-2 font-medium text-center">Влажность</th>
								<th className="pb-2 font-medium text-center">Оценка</th>
							</tr>
						</thead>
						<tbody className="divide-y">
							{regions?.regions.map((r) => (
								<tr key={r.region_code} className="hover:bg-gray-50">
									<td className="py-2 pr-4">{r.name}</td>
									<td className="py-2 text-center">{r.temperature_c ? `${r.temperature_c.toFixed(1)}°C` : '—'}</td>
									<td className="py-2 text-center">{r.humidity_pct ? `${r.humidity_pct.toFixed(0)}%` : '—'}</td>
									<td className="py-2 text-center">
										{r.color && (
											<span className={`inline-block px-2 py-0.5 rounded border text-xs ${COLOR_MAP[r.color]}`}>
												{r.color === 'green' ? 'В норме' : r.color === 'yellow' ? 'Отклонение' : 'Сильное отклонение'}
											</span>
										)}
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			</div>
		</main>
	);
}