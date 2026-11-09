'use client';

import { useAtlas } from "@/lib/store";

export function ProfileBuilder() {
	const { profile, patchFilters } = useAtlas();
	const f = profile.filters;
	return (
		<details className="relative">
			<summary className="cursor-pointer px-3 py-1 rounded bg-gray-200">Настроить</summary>
			<div className="absolute z-10 mt-1 w-80 bg-white shadow-lg p-3 space-y-2 text-sm">
				<Field label="Мобильность" value={f.mobility}
					options={['car','no_car']} onChange={(v) => patchFilters({ mobility: v as any })} />
				<Field label="Порог медицины" value={String(f.medicine_threshold_min)}
					options={['15','30','60']} onChange={(v) => patchFilters({ medicine_threshold_min: Number(v) as any })} />
				<Field label="Состав семьи" value={f.family}
					options={['single','couple','children']} onChange={(v) => patchFilters({ family: v as any })} />
				<Field label="Режим работы" value={f.work}
					options={['offline','remote']} onChange={(v) => patchFilters({ work: v as any })} />
				<Field label="Дальние поездки" value={f.travel_frequency}
					options={['often','rare','none']} onChange={(v) => patchFilters({ travel_frequency: v as any })} />
				<Field label="Климат" value={f.climate_sensitivity}
					options={['high','mid','low']} onChange={(v) => patchFilters({ climate_sensitivity: v as any })} />
				<Field label="Территории" value={f.territory_types}
					options={['cities','with_towns','all']} onChange={(v) => patchFilters({ territory_types: v as any })} />
				<Field label="Темп жизни" value={f.pace}
					options={['active','calm']} onChange={(v) => patchFilters({ pace: v as any })} />
				<label className="flex items-center gap-2">
					<input type="checkbox" checked={f.realtor_mode}
						onChange={(e) => patchFilters({ realtor_mode: e.target.checked })} />
					Режим риелтора
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
			<span>{label}</span>
			<select value={value} onChange={(e) => onChange(e.target.value)} className="border rounded px-1">
				{options.map((o) => <option key={o} value={o}>{o}</option>)}
			</select>
		</label>
	);
}