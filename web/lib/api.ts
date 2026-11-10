import { Profile } from '@/types/profile';
import { encodeProfile } from './profile';

export const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export class ApiError extends Error {
	code?: string;
	details?: any[];
	constructor(message: string, code?: string, details?: any[]) {
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

export interface ScenarioKind {
	value: string;
	label: string;
}

export interface ScenarioInfo {
	code: string;
	name: string;
	description: string;
	target: 'point' | 'municipality';
	kinds: ScenarioKind[] | null;
	params_schema: any;
	example: Record<string, any>;
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
	params: any;
	plan: { scenario_code: string, params: any, reason: string }[] | null;
	affected: number;
	results: {
		territory_id: number;
		name: string;
		total_before: number;
		total_after: number;
		delta: number;
		delta_subindices: Record<string, number>;
		changes: { feature_code: string, label: string, unit: string, before: number, after: number, delta_points: number, text: string }[];
	}[];
	winners: WhatIfBrief[];
	losers: WhatIfBrief[];
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

export async function fetchScenarios() {
  	return json<ScenarioInfo[]>(await fetch(`${API}/whatif/scenarios`));
}

export async function fetchTerritories(params: { level?: string; q?: string; limit?: number; parent_id?: number }) {
	const q = new URLSearchParams();
	if (params.level) q.append('level', params.level);
	if (params.q) q.append('q', params.q);
	if (params.limit) q.append('limit', String(params.limit));
	if (params.parent_id) q.append('parent_id', String(params.parent_id));
	
	return json<{ items: TerritoryBrief[] }>(await fetch(`${API}/territories?${q}`));
}

export async function streamChat(
	messages: { role: 'user' | 'assistant'; content: string }[],
	profile: Profile,
	onChunk: (text: string) => void,
	onCitations: (sources: { title: string; url: string | null }[]) => void
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
		if (event === 'citations') onCitations(data.sources ?? []);
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
