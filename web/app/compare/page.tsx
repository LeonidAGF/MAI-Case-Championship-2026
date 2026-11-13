'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { CompareView, SERIES } from '@/components/Compare/CompareView';
import { PageHeader } from '@/components/UI/PageHeader';
import { Select } from '@/components/UI/Field';
import { Icon } from '@/components/UI/Icon';
import { fetchTerritories } from '@/lib/api';

export default function ComparePage() {
	const [selectedIds, setSelectedIds] = useState<number[]>([]);
	const { data } = useQuery({
		queryKey: ['territories', 'settlement', 'scored', 50],
		queryFn: () => fetchTerritories({ level: 'settlement', scored_only: true, limit: 50 }),
	});
	const territories = data?.items ?? [];

	return (
		<main className="min-h-dvh bg-mesh pb-16">
			<PageHeader title="Сравнение территорий" subtitle="Индекс и подиндексы бок о бок" icon="compare" />

			<div className="mx-auto mt-6 max-w-6xl space-y-6 px-3 md:mt-8 md:px-6">
				<section className="surface rounded-3xl p-4 md:p-6 animate-fade-up">
					<div className="flex flex-col gap-1 md:flex-row md:items-baseline md:justify-between">
						<h2 className="font-display text-base font-semibold tracking-tight text-ink-900">Выберите территории</h2>
						<p className="text-xs font-medium text-ink-500">Две или три из одной группы сравнимости</p>
					</div>

					<div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
						{[0, 1, 2].map((slot) => (
							<div key={slot} className="rounded-2xl bg-white/70 p-3 ring-1 ring-ink-100">
								<div className="mb-2 flex items-center gap-2 text-xs font-bold text-ink-500">
									<span className={`h-2.5 w-2.5 rounded-full ${SERIES[slot].dot}`} />
									Территория {slot + 1}
									{slot === 2 && <span className="font-medium text-ink-400">· необязательно</span>}
								</div>
								<Select
									aria-label={`Территория ${slot + 1}`}
									value={selectedIds[slot] || ''}
									onChange={(e) => {
										const newIds = [...selectedIds];
										if (e.target.value) newIds[slot] = Number(e.target.value);
										else newIds.splice(slot, 1);
										setSelectedIds(newIds);
									}}
								>
									<option value="">Не выбрано</option>
									{territories.map((t) => (
										<option key={t.id} value={t.id}>{t.name}</option>
									))}
								</Select>
							</div>
						))}
					</div>
				</section>

				{selectedIds.length >= 2 ? (
					<CompareView ids={selectedIds.filter(Boolean)} />
				) : (
					<div className="flex flex-col items-center py-14 text-center animate-fade-up">
						<div className="grid h-16 w-16 place-items-center rounded-3xl bg-brand-gradient text-white shadow-glow animate-float">
							<Icon name="compare" className="h-8 w-8" />
						</div>
						<p className="mt-5 max-w-sm text-sm font-medium text-ink-500">Выберите хотя бы две территории в списках выше.</p>
					</div>
				)}
			</div>
		</main>
	);
}
