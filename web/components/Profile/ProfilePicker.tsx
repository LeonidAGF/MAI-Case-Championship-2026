'use client';

import { useAtlas } from '@/lib/store';
import { DEFAULT_PROFILE, Profile } from '@/types/profile';
import { ProfileBuilder } from '@/components/Profile/ProfileBuilder';
import { Icon, IconName } from '@/components/UI/Icon';

const PRESETS: Array<{ id: string; label: string; icon: IconName; weights: Profile['weights'] }> = [
	{ id: 'family',           label: 'Семья с детьми',     icon: 'users',     weights: { S1: 25, S2: 30, S3: 15, S4: 15, S5: 10, S6: 5 } },
	{ id: 'young_specialist', label: 'Молодой специалист', icon: 'briefcase', weights: { S1: 10, S2: 10, S3: 25, S4: 20, S5: 30, S6: 5 } },
	{ id: 'pensioner',        label: 'Пенсионер',          icon: 'heart',     weights: { S1: 35, S2: 5,  S3: 10, S4: 20, S5: 10, S6: 20 } },
	{ id: 'remote_worker',    label: 'Удалёнщик',          icon: 'laptop',    weights: { S1: 15, S2: 15, S3: 15, S4: 15, S5: 20, S6: 20 } },
	{ id: 'realtor',          label: 'Риелтор',            icon: 'home',      weights: { S1: 16.67, S2: 11.11, S3: 11.11, S4: 27.78, S5: 27.78, S6: 5.56 } },
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
		<div className="flex shrink-0 items-center gap-1">
			{PRESETS.map((p) => {
				const active = profile.preset === p.id;
				return (
					<button
						key={p.id}
						onClick={() => applyPreset(p)}
						aria-pressed={active}
						className={`inline-flex h-10 items-center gap-2 whitespace-nowrap rounded-xl px-3.5 text-[13px] font-bold transition-all duration-300 ease-spring ${
							active ? 'bg-brand-gradient text-white shadow-glow' : 'text-ink-600 hover:bg-white hover:text-ink-900 hover:shadow-soft'
						}`}
					>
						<Icon name={p.icon} className={`h-4 w-4 ${active ? 'text-white' : 'text-ink-400'}`} />
						{p.label}
					</button>
				);
			})}
			<span className="mx-1 h-6 w-px shrink-0 bg-ink-200" aria-hidden="true" />
			<ProfileBuilder />
		</div>
	);
}
