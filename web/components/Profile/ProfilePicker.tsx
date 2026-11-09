'use client';

import { useAtlas } from '@/lib/store';
import { DEFAULT_PROFILE, Profile } from '@/types/profile';
import { ProfileBuilder } from '@/components/Profile/ProfileBuilder';

const PRESETS: Array<{ id: string; label: string; weights: Profile['weights'] }> = [
	{ id: 'family',           label: 'Семья с детьми',     weights: { S1: 25, S2: 30, S3: 15, S4: 15, S5: 10, S6: 5 } },
	{ id: 'young_specialist', label: 'Молодой специалист', weights: { S1: 10, S2: 10, S3: 25, S4: 20, S5: 30, S6: 5 } },
	{ id: 'pensioner',        label: 'Пенсионер',          weights: { S1: 35, S2: 5,  S3: 10, S4: 20, S5: 10, S6: 20 } },
	{ id: 'remote_worker',    label: 'Удалёнщик',          weights: { S1: 15, S2: 15, S3: 15, S4: 15, S5: 20, S6: 20 } },
	{ id: 'realtor',          label: 'Риелтор',            weights: { S1: 16.67, S2: 11.11, S3: 11.11, S4: 27.78, S5: 27.78, S6: 5.56 } },
];

export function ProfilePicker() {
	const { profile, setProfile } = useAtlas();

	const applyPreset = (p: typeof PRESETS[number]) => {
		let filters = { ...DEFAULT_PROFILE.filters };
		
		if (p.id === 'pensioner') {
			filters = { ...filters, mobility: 'no_car', medicine_threshold_min: 15, climate_sensitivity: 'high', pace: 'calm' };
		} else if (p.id === 'remote_worker') {
			filters = { ...filters, work: 'remote', climate_sensitivity: 'high' };
		} else if (p.id === 'young_specialist') {
			filters = { ...filters, pace: 'active' };
		} else if (p.id === 'realtor') {
			filters = { ...filters, realtor_mode: true };
		}

		setProfile({
			...DEFAULT_PROFILE,
			preset: p.id,
			weights: p.weights,
			filters,
		});
	};

	return (
		<div className="flex gap-2 flex-wrap">
			{PRESETS.map((p) => (
				<button
					key={p.id}
					onClick={() => applyPreset(p)}
					className={`px-3 py-1 text-sm rounded transition ${profile.preset === p.id ? 'bg-blue-600 text-white shadow-sm' : 'bg-gray-100 hover:bg-gray-200'}`}
				>
					{p.label}
				</button>
			))}
			<ProfileBuilder />
		</div>
	);
}