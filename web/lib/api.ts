import { Profile } from '@/types/profile';
import { encodeProfile } from './profile';

export const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

async function json<T>(res: Response): Promise<T> {
	if (!res.ok) {
		const err = await res.json().catch(() => ({}));
		throw new Error(err.message || `API Error: ${res.status}`);
	}
	return res.json();
}

export interface Point { lon: number; lat: number; }

export interface TerritoryBrief {
	id: number;
	external_id: string;
	name: string;
	level: 'region' | 'municipality' | 'settlement';
	kind: string;
	region_code: string | null;
	parent_id: number | null;
	cluster: string | null;
	population: number | null;
	is_admin_center: boolean;
	is_scored: boolean;
	centroid: Point | null;
}

export interface TerritoryList {
	items: TerritoryBrief[];
	total: number;
	limit: number;
	offset: number;
}

export interface Attractiveness {
	score: number | null;
	badge: string | null;
	label: string | null;
}

export interface IndexItem {
	territory_id: number;
	name: string;
	total: number;
	rank: number;
	subindices: Record<string, number | null>;
	attractiveness: Attractiveness | null;
}

export interface ClusterStats {
	n: number;
	median: number | null;
	min: number | null;
	max: number | null;
}

export interface IndexSingle {
	profile: any;
	model_version: string;
	data_revision: number;
	scenario_run_id: number | null;
	territory_id: number;
	name: string;
	cluster: string;
	cluster_name: string;
	total: number;
	rank: number;
	cluster_size: number;
	subindices: Record<string, number | null>;
	attractiveness: Attractiveness | null;
	result_id: number;
}

export interface IndexCluster {
	profile: any;
	model_version: string;
	data_revision: number;
	scenario_run_id: number | null;
	cluster: string;
	cluster_name: string;
	cluster_allowed_by_profile: boolean;
	stats: ClusterStats;
	items: IndexItem[];
	total: number;
	limit: number;
	offset: number;
}

export interface WaterfallStep {
	feature_code: string;
	label: string;
	subindex: string;
	raw_value: number | null;
	unit: string;
	normalized: number | null;
	weight: number;
	contribution: number;
	median_contribution: number;
	delta: number;
	imputed: boolean;
	inherited_from: string | null;
	value_mode: string;
	text: string;
	typical_text: string | null;
}

export interface ProCon {
	feature_code: string;
	subindex: string;
	delta: number;
	text: string;
}

export interface ExplainResponse {
	territory_id: number;
	name: string;
	cluster: string;
	cluster_name: string;
	total: number;
	subindices: Record<string, number | null>;
	attractiveness: Attractiveness | null;
	baseline: number;
	waterfall: WaterfallStep[];
	pluses: ProCon[];
	minuses: ProCon[];
	attractiveness_factors: any[];
	summary: string;
	result_id: number;
}

export interface CompareResponse {
	cluster: string;
	cluster_name: string;
	cluster_stats: ClusterStats;
	items: IndexItem[];
	radar: any;
	leaders: any;
	summary: string[];
}

export interface WhatIfResult {
	territory_id: number;
	name: string;
	cluster: string;
	region_code: string | null;
	total_before: number;
	total_after: number;
	delta: number;
	delta_subindices: Record<string, number>;
	changes: any[];
}

export interface WhatIfResponse {
	run_id: number;
	scenario_code: string;
	scenario_name: string;
	method_used: string;
	params: any;
	plan: any[] | null;
	affected: number;
	results: WhatIfResult[];
	winners: any[];
	losers: any[];
	summary: any;
	explanation: string;
	warnings: string[];
	profile: any;
}

export async function fetchIndex(territoryId: number, p: Profile) {
	const q = new URLSearchParams({ territory_id: String(territoryId), p: encodeProfile(p) });
	return json<IndexSingle>(await fetch(`${API}/index?${q}`));
}

export async function fetchClusterIndex(cluster: string, p: Profile) {
	const q = new URLSearchParams({ cluster, p: encodeProfile(p) });
	return json<IndexCluster>(await fetch(`${API}/index?${q}`));
}

export async function fetchExplain(territoryId: number, p: Profile) {
	const q = new URLSearchParams({ p: encodeProfile(p) });
	return json<ExplainResponse>(await fetch(`${API}/explain/${territoryId}?${q}`));
}

export async function postCompare(territory_ids: number[], p: Profile) {
	return json<CompareResponse>(await fetch(`${API}/compare`, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ territory_ids, profile: p }),
	}));
}

export async function runWhatIf(scenario_code: string, params: Record<string, unknown>, p: Profile) {
	return json<WhatIfResponse>(await fetch(`${API}/whatif/run`, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ scenario_code, params, profile: p }),
	}));
}

export async function fetchTerritoriesGeoJSON() {
	const res = await fetch(`${API}/territories/geojson?level=settlement`);
	if (!res.ok) throw new Error('Failed to load GeoJSON');
	return res.json();
}