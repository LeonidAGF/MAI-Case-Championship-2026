'use client';

import { useAtlas } from "@/lib/store";
import { Profile, Subindex } from "@/types/profile";

const BASE_WEIGHTS: Record<Subindex, number> = {
  	S1: 20, S2: 20, S3: 15, S4: 15, S5: 15, S6: 15
};

export function ProfileBuilder() {
	const { profile, setProfile } = useAtlas();
	const f = profile.filters;

	const recalculateWeights = (newFilters: Profile['filters']) => {
		let w = newFilters.realtor_mode 
			? { S1: 15, S2: 10, S3: 10, S4: 25, S5: 35, S6: 5 } 
			: { ...BASE_WEIGHTS };

		// Семья с детьми -> Образование (S2) и Медицина (S1) важнее
		if (newFilters.family === 'children') {
			w.S2 += 15;
			w.S1 += 10;
			w.S5 -= 10; 
			w.S6 -= 5;
		} 
		// Без машины -> Медицина (S1) и Климат (S6) важнее
		else if (newFilters.mobility === 'no_car') {
			w.S1 += 20;
			w.S6 += 10;
			w.S5 -= 20;
			w.S3 -= 10;
		}

		// Удаленная работа -> Климат и Досуг важнее
		if (newFilters.work === 'remote') {
			w.S6 += 15;
			w.S3 += 10;
			w.S5 -= 15;
			w.S4 -= 10;
		}

		// Активный темп -> Досуг (S3) и Работа (S5) важнее
		if (newFilters.pace === 'active') {
			w.S3 += 10;
			w.S5 += 10;
			w.S2 -= 10;
			w.S6 -= 10;
		}

		// Частые дальние поездки -> Транспорт (S4) важнее
		if (newFilters.travel_frequency === 'often') {
			w.S4 += 15;
			w.S3 -= 15;
		}

		// Высокая климатическая чувствительность -> Климат (S6) важнее
		if (newFilters.climate_sensitivity === 'high') {
			w.S6 += 15;
			w.S4 -= 15;
		}

		// Медицина
		if (newFilters.medicine_threshold_min === 15) {
			w.S1 += 20;
		} else if (newFilters.medicine_threshold_min === 30) {
			w.S1 += 10;
		}

		(Object.keys(w) as Subindex[]).forEach(k => { 
			if (w[k] < 0) w[k] = 0; 
		});

		const sum = Object.values(w).reduce((a, b) => a + b, 0);
		(Object.keys(w) as Subindex[]).forEach(k => {
			w[k] = Math.round((w[k] / sum) * 100 * 10) / 10;
		});

		return w;
	};

	const updateFilter = (key: keyof Profile['filters'], value: any) => {
		const newFilters = { ...f, [key]: value };
		const newWeights = recalculateWeights(newFilters);

		setProfile({
			...profile,
			preset: null,
			filters: newFilters,
			weights: newWeights
		});
	};

	return (
		<details className="relative">
			<summary className="cursor-pointer px-3 py-1 rounded bg-gray-200 hover:bg-gray-300 transition text-sm">
				Настроить
			</summary>
		
			<div className="absolute right-0 z-20 mt-2 w-80 bg-white shadow-2xl border border-gray-100 rounded-xl p-4 text-sm space-y-3">
				<h3 className="font-bold text-gray-800 border-b pb-1 mb-2">Фильтры</h3>
				
				<Field label="Мобильность" value={f.mobility}
					options={['car','no_car']} onChange={(v) => updateFilter('mobility', v)} />
				<Field label="Порог медицины" value={String(f.medicine_threshold_min)}
					options={['15','30','60']} onChange={(v) => updateFilter('medicine_threshold_min', Number(v))} />
				<Field label="Состав семьи" value={f.family}
					options={['single','couple','children']} onChange={(v) => updateFilter('family', v)} />
				<Field label="Режим работы" value={f.work}
					options={['offline','remote']} onChange={(v) => updateFilter('work', v)} />
				<Field label="Дальние поездки" value={f.travel_frequency}
					options={['often','rare','none']} onChange={(v) => updateFilter('travel_frequency', v)} />
				<Field label="Климат" value={f.climate_sensitivity}
					options={['high','mid','low']} onChange={(v) => updateFilter('climate_sensitivity', v)} />
				<Field label="Территории" value={f.territory_types}
					options={['cities','with_towns','all']} onChange={(v) => updateFilter('territory_types', v)} />
				<Field label="Темп жизни" value={f.pace}
					options={['active','calm']} onChange={(v) => updateFilter('pace', v)} />
				
				<label className="flex items-center gap-2 mt-4 pt-2 border-t cursor-pointer">
					<input type="checkbox" checked={f.realtor_mode}
						onChange={(e) => updateFilter('realtor_mode', e.target.checked)} 
						className="w-4 h-4 text-blue-600 rounded" />
					<span className="font-medium text-gray-700">Режим риелтора</span>
				</label>
			</div>
		</details>
	);
}

function Field({ label, value, options, onChange }: {
  	label: string; value: string; options: string[]; onChange: (v: string) => void;
}) {
	return (
		<label className="flex items-center justify-between gap-2">
			<span className="text-gray-600">{label}</span>
			<select value={value} onChange={(e) => onChange(e.target.value)} className="border rounded px-1 py-0.5 bg-gray-50 focus:ring-2 focus:ring-blue-500 outline-none text-xs w-32">
				{options.map((o) => <option key={o} value={o}>{o}</option>)}
			</select>
		</label>
	);
}