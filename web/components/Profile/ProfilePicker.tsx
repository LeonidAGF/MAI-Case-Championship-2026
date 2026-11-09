'use client';

import { useAtlas } from '@/lib/store';
import { DEFAULT_PROFILE, Profile } from '@/types/profile';
import { ProfileBuilder } from '@/components/Profile/ProfileBuilder'

const PRESETS: Array<{ id: string; label: string; weights: Profile['weights'] }> = [
	{ id: 'family',    label: 'Семья с детьми',     weights: { S1: 25, S2: 30, S3: 15, S4: 15, S5: 10, S6: 5 } },
	{ id: 'young',     label: 'Молодой специалист', weights: { S1: 10, S2: 10, S3: 25, S4: 20, S5: 30, S6: 5 } },
	{ id: 'pensioner', label: 'Пенсионер',          weights: { S1: 35, S2: 5,  S3: 10, S4: 20, S5: 10, S6: 20 } },
	{ id: 'remote',    label: 'Удалёнщик',          weights: { S1: 15, S2: 15, S3: 15, S4: 15, S5: 20, S6: 20 } },
	{ id: 'realtor',   label: 'Риелтор',            weights: { S1: 15, S2: 10, S3: 10, S4: 25, S5: 25, S6: 5 } },
];

export function ProfilePicker() {
	const { profile, setProfile } = useAtlas();

	const applyPreset = (p: typeof PRESETS[number]) => {
		setProfile({
			...DEFAULT_PROFILE,
			preset: p.id,
			weights: p.weights,
			filters: {
				...DEFAULT_PROFILE.filters,
				realtor_mode: p.id === 'realtor',
			},
		});
	};

	return (
		<div className="flex gap-2">
			{PRESETS.map((p) => (
				<button
					key={p.id}
					onClick={() => applyPreset(p)}
					className={`px-3 py-1 rounded ${profile.preset === p.id ? 'bg-blue-600 text-white' : 'bg-gray-100'}`}
				>
					{p.label}
				</button>
			))}
			<ProfileBuilder />
		</div>
	);
}