export type Subindex = 'S1' | 'S2' | 'S3' | 'S4' | 'S5' | 'S6';

export type Mobility = 'car' | 'no_car';
export type Family = 'single' | 'couple' | 'children';
export type Work = 'offline' | 'remote';
export type TravelFrequency = 'often' | 'rare' | 'none';
export type ClimateSensitivity = 'high' | 'mid' | 'low';
export type TerritoryTypes = 'cities' | 'with_towns' | 'all';
export type Pace = 'active' | 'calm';
export type MedicineThreshold = 15 | 30 | 60;

export interface Profile {
	v: 1;
	preset: string | null;
	weights: Record<Subindex, number>; // сумма = 100
	filters: {
		mobility: Mobility;
		medicine_threshold_min: MedicineThreshold;
		family: Family;
		work: Work;
		travel_frequency: TravelFrequency;
		climate_sensitivity: ClimateSensitivity;
		territory_types: TerritoryTypes;
		pace: Pace;
		realtor_mode: boolean;
	};
}

export const DEFAULT_PROFILE: Profile = {
	v: 1,
	preset: null,
	weights: { S1: 25, S2: 30, S3: 15, S4: 15, S5: 10, S6: 5 },
	filters: {
		mobility: 'car',
		medicine_threshold_min: 30,
		family: 'children',
		work: 'offline',
		travel_frequency: 'rare',
		climate_sensitivity: 'mid',
		territory_types: 'all',
		pace: 'active',
		realtor_mode: false,
	},
};