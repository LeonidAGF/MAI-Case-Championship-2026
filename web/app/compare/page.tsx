'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { CompareView } from '@/components/Compare/CompareView';
import { API, TerritoryBrief } from '@/lib/api';

export default function ComparePage() {
	const [selectedIds, setSelectedIds] = useState<number[]>([]); 
	const [territories, setTerritories] = useState<TerritoryBrief[]>([]);

	useEffect(() => {
		fetch(`${API}/territories?level=settlement&scored_only=true&limit=50`)
			.then(res => res.json())
			.then(data => setTerritories(data.items || []))
			.catch(console.error);
	}, []);

	return (
		<main className="min-h-screen bg-gray-50 pb-12">
			<header className="bg-white p-3 md:p-4 shadow-sm mb-4 md:mb-6 flex justify-between items-center sticky top-0 z-10">
				<h1 className="text-base md:text-xl font-bold text-gray-800 truncate pr-2">Сравнение территорий</h1>
				<Link href="/" className="text-xs md:text-sm font-medium text-blue-600 hover:underline shrink-0">← Назад</Link>
			</header>
		
			<div className="max-w-6xl mx-auto px-3 md:px-4 space-y-4 md:space-y-6">
				<div className="bg-white p-3 md:p-4 rounded-xl shadow-sm flex flex-col md:flex-row gap-3 items-start md:items-center">
					<span className="text-sm font-medium text-gray-600">Выберите территории:</span>
				
					<div className="flex flex-col md:flex-row gap-2 w-full md:w-auto">
						{[0, 1, 2].map((slot) => (
							<select
								key={slot}
								value={selectedIds[slot] || ''}
								onChange={(e) => {
									const newIds = [...selectedIds];
									if (e.target.value) newIds[slot] = Number(e.target.value);
									else newIds.splice(slot, 1);
									setSelectedIds(newIds);
								}}
								className="w-full md:w-auto border rounded-lg px-3 py-2 text-sm bg-gray-50 focus:ring-2 focus:ring-blue-500 outline-none"
							>
								<option value="">Не выбрано</option>
								{territories.map(t => (
									<option key={t.id} value={t.id}>{t.name}</option>
								))}
							</select>
						))}
					</div>
				</div>

				{selectedIds.length >= 2 ? (
					<CompareView ids={selectedIds.filter(Boolean)} />
				) : (
					<div className="text-center text-gray-500 py-12">Выберите хотя бы две территории в списках выше.</div>
				)}
			</div>
		</main>
	);
}