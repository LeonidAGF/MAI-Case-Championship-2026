import { Profile } from '@/types/profile';
import { encodeProfile } from './profile';

export const API = process.env.NEXT_PUBLIC_API_URL ?? 'https://cson.site';

/** One validation problem of a 422 answer (FastAPI): where it is and what is wrong. */
export interface ApiErrorDetail {
	loc?: (string | number)[];
	msg?: string;
	type?: string;
}

export class ApiError extends Error {
	code?: string;
	details?: ApiErrorDetail[];
	constructor(message: string, code?: string, details?: ApiErrorDetail[]) {
		super(message);
		this.name = 'ApiError';
		this.code = code;
		this.details = details;
	}
}

async function json<T>(res: Response): Promise<T> {
	if (!res.ok) {
		const err = await res.json().catch(() => ({}));
		throw new ApiError(err.message || `API Error: ${res.status}`, err.code, err.details);
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
	profile: unknown;
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
	profile: unknown;
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
	attractiveness_factors: unknown[];
	summary: string;
	result_id: number;
}

export interface RadarAxis {
	code: string;
	name: string;
}

export interface RadarSeries {
	territory_id: number;
	name: string;
	values: (number | null)[];
}

export interface CompareResponse {
	cluster: string;
	cluster_name: string;
	cluster_stats: ClusterStats;
	items: IndexItem[];
	radar: { axes: RadarAxis[]; series: RadarSeries[] };
	leaders: Record<string, { territory_id: number; gap: number }>;
	summary: string[];
}

export interface WhatIfChange {
	feature_code: string;
	label: string;
	unit: string;
	before: number;
	after: number;
	delta_points: number;
	text: string;
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
	changes: WhatIfChange[];
}

export interface ScenarioKind {
	value: string;
	label: string;
}

/** JSON Schema of one scenario parameter (pydantic): optional ones come as anyOf [type, null]. */
export interface ParamSchema {
	type?: string;
	anyOf?: ParamSchema[];
	description?: string;
	minimum?: number;
	maximum?: number;
	default?: unknown;
}

export type ScenarioParams = Record<string, unknown>;

export interface ScenarioInfo {
	code: string;
	name: string;
	description: string;
	target: 'point' | 'municipality';
	kinds: ScenarioKind[] | null;
	params_schema: { properties?: Record<string, ParamSchema>; required?: string[] } | null;
	example: ScenarioParams;
}

export interface WhatIfBrief {
	territory_id: number;
	name: string;
	delta: number;
}

export interface WhatIfResponse {
	run_id: number;
	scenario_code: string;
	scenario_name: string;
	method_used: string;
	params: ScenarioParams;
	plan: { scenario_code: string; params: ScenarioParams; reason: string }[] | null;
	affected: number;
	results: WhatIfResult[];
	winners: WhatIfBrief[];
	losers: WhatIfBrief[];
	summary: {
		affected: number;
		avg_delta: number;
		max_delta: number;
		min_delta: number;
		by_cluster?: Record<string, { affected: number; avg_delta: number }>;
	} | null;
	explanation: string;
	warnings: string[];
	profile: unknown;
}

export type Season = 'winter' | 'spring' | 'summer' | 'autumn';

export interface ClimateCell {
	season: Season;
	temperature_c: number;
	humidity_pct: number;
	wind_m_s: number;
	warm_days: number;
	extreme_days: number;
	wet_days: number;
	days_in_season: number;
	color: 'green' | 'yellow' | 'red';
	temperature_deviation_c: number;
	humidity_deviation_pct: number;
	grid_lon: number;
	grid_lat: number;
}

/** Editorial seasonal target of the climate map (not a climate normal): backend app/climate/service.py. */
export interface ClimateTarget {
	temperature_min_c: number;
	temperature_max_c: number;
	humidity_min_pct: number;
	humidity_max_pct: number;
	yellow_temperature_margin_c: number;
	yellow_humidity_margin_pct: number;
	kind: string;
}

export interface ClimateGridResponse {
	year: number;
	period_label: string;
	source: string;
	time_standard: string;
	grid_resolution_degrees?: { latitude: number; longitude: number };
	target: ClimateTarget;
	cells: ClimateCell[];
}

export interface ClimateRegion {
	territory_id: number;
	region_code: string;
	name: string;
	grid_cells: number;
	season: Season;
	temperature_c: number | null;
	humidity_pct: number | null;
	color: 'green' | 'yellow' | 'red' | null;
}

export interface ClimateRegionsResponse {
	year: number;
	period_label: string;
	source: string;
	time_standard: string;
	target: ClimateTarget;
	regions: ClimateRegion[];
}

// GeoJSON of /territories/geojson. Type aliases, not interfaces, so they also fit MapLibre's GeoJSON types.
export type PointGeometry = { type: 'Point'; coordinates: [number, number] };
export type PolygonGeometry = { type: 'Polygon'; coordinates: number[][][] };
export type MultiPolygonGeometry = { type: 'MultiPolygon'; coordinates: number[][][][] };

export type SettlementProperties = {
	id: number;
	name: string;
	level: string;
	kind: string;
	region_code: string | null;
	parent_id: number | null;
	cluster: string | null;
	population: number | null;
	is_scored: boolean;
	total?: number | null;
	rank?: number | null;
};

export type SettlementFeature = { type: 'Feature'; id?: number; geometry: PointGeometry; properties: SettlementProperties };
export type SettlementCollection = { type: 'FeatureCollection'; features: SettlementFeature[] };

export type RegionProperties = {
	id: number;
	name: string;
	region_code: string | null;
	population: number | null;
};

export type RegionFeature = {
	type: 'Feature';
	id?: number;
	geometry: PolygonGeometry | MultiPolygonGeometry;
	properties: RegionProperties;
};
export type RegionCollection = { type: 'FeatureCollection'; features: RegionFeature[] };

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

/** Settlements as points, each with its index for the profile (`total`, null when it isn't scored). */
export async function fetchSettlementsGeoJSON(p: Profile) {
	const q = new URLSearchParams({ level: 'settlement', with_index: 'true', p: encodeProfile(p) });
	return json<SettlementCollection>(await fetch(`${API}/territories/geojson?${q}`));
}

export async function fetchScenarios() {
  	return json<ScenarioInfo[]>(await fetch(`${API}/whatif/scenarios`));
}

export async function fetchTerritories(params: { level?: string; q?: string; limit?: number; parent_id?: number; scored_only?: boolean }) {
	const q = new URLSearchParams();
	if (params.level) q.append('level', params.level);
	if (params.q) q.append('q', params.q);
	if (params.limit) q.append('limit', String(params.limit));
	if (params.parent_id) q.append('parent_id', String(params.parent_id));
	if (params.scored_only) q.append('scored_only', 'true');
	
	return json<{ items: TerritoryBrief[] }>(await fetch(`${API}/territories?${q}`));
}

export async function streamChat(
	messages: { role: 'user' | 'assistant'; content: string }[],
	profile: Profile,
	onChunk: (text: string) => void
) {
	const res = await fetch(`${API}/chat`, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ messages: messages.map(({ role, content }) => ({ role, content })), profile }),
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({}));
		throw new Error(err.message || `Ошибка чата: ${res.status}`);
	}
	if (!res.body) throw new Error('Нет тела ответа');

	const reader = res.body.getReader();
	const decoder = new TextDecoder();
	let buffer = '';
	const consume = (block: string) => {
		const event = block.split('\n').find((line) => line.startsWith('event: '))?.slice(7);
		const payload = block.split('\n').filter((line) => line.startsWith('data: ')).map((line) => line.slice(6)).join('\n');
		if (!event || !payload) return;
		const data = JSON.parse(payload);
		if (event === 'delta') onChunk(data.text);
		if (event === 'error') throw new Error(data.message || 'Ошибка ассистента');
	};

	while (true) {
		const { done, value } = await reader.read();
		if (done) break;

		buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
		let boundary = buffer.indexOf('\n\n');
		while (boundary >= 0) {
			consume(buffer.slice(0, boundary));
			buffer = buffer.slice(boundary + 2);
			boundary = buffer.indexOf('\n\n');
		}
	}
	buffer += decoder.decode();
	if (buffer.trim()) consume(buffer);
}

export async function fetchClimateGrid(season: Season) {
	return json<ClimateGridResponse>(await fetch(`${API}/climate/2025/grid?season=${season}`));
}

export async function fetchClimateRegions(season: Season) {
	return json<ClimateRegionsResponse>(await fetch(`${API}/climate/2025/regions?season=${season}`));
}

export async function fetchRegionBoundaries() {
	return json<RegionCollection>(await fetch(`${API}/territories/geojson?level=region&simplify=0.01`));
}