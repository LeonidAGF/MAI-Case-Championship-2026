import { create } from 'zustand';
import { Profile, DEFAULT_PROFILE } from '@/types/profile';
import { persistProfile } from './profile';

interface AtlasState {
	profile: Profile;
	selectedTerritoryId: number | null;
	setProfile: (p: Profile) => void;
	patchFilters: (f: Partial<Profile['filters']>) => void;
	selectTerritory: (id: number | null) => void;
}

export const useAtlas = create<AtlasState>((set) => ({
	profile: DEFAULT_PROFILE, 
	selectedTerritoryId: null,
	
	setProfile: (p) => {
		const nextProfile = JSON.parse(JSON.stringify(p));
		persistProfile(nextProfile);
		set({ profile: nextProfile });
	},
	
	patchFilters: (f) =>
		set((s) => {
			const nextProfile: Profile = { 
				...s.profile, 
				preset: null, 
				filters: { ...s.profile.filters, ...f } 
			};
			persistProfile(nextProfile);
			return { profile: nextProfile };
		}),
		
	selectTerritory: (id) => set({ selectedTerritoryId: id }),
}));