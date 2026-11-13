/**
 * Smooth climate surface for the /climate map, built in the browser from the NASA POWER grid nodes
 * (GET /climate/2025/grid): a Catmull-Rom bicubic interpolation between the nodes (it passes through
 * every node value), coloured with the scales of lib/palette.ts and clipped to the СКФО regions.
 * The image is laid out in Web Mercator rows, so MapLibre's image source places it exactly.
 * Isolines are traced separately (marching squares) and drawn by MapLibre as vector lines.
 */

import type { ClimateCell, ClimateTarget, RegionCollection } from '@/lib/api';
import { COMFORT_DOMAIN, COMFORT_STOPS, colorLut, TEMPERATURE_DOMAIN, TEMPERATURE_STOPS } from '@/lib/palette';

export type ClimateMode = 'temperature' | 'comfort';
export type LngLat = [number, number];

export interface IsolineCollection {
	type: 'FeatureCollection';
	features: { type: 'Feature'; properties: { value: number; major: boolean }; geometry: { type: 'MultiLineString'; coordinates: LngLat[][] } }[];
}

export interface ClimateSurface {
	/** Object URL of the PNG; revoke it when the image is no longer shown. */
	url: string;
	/** Corners for the MapLibre image source: top-left, top-right, bottom-right, bottom-left. */
	coordinates: [LngLat, LngLat, LngLat, LngLat];
	bounds: [number, number, number, number];
	isolines: IsolineCollection;
	inside: (lon: number, lat: number) => boolean;
	nearest: (lon: number, lat: number) => ClimateCell | null;
}

const IMAGE_WIDTH = 1200;
const LUT_SIZE = 512;
const ISOTHERM_STEP_C = 2;
const CONTOUR_SAMPLES = 320;
const PADDING_DEG = 0.12;

const mercY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
const latFromMercY = (y: number) => (360 / Math.PI) * Math.atan(Math.exp(y)) - 90;
const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);

/** Signed distance from [lo, hi], 0 inside (as signed_deviation in backend app/climate/service.py). */
const deviation = (v: number, lo: number, hi: number) => (v < lo ? v - lo : v > hi ? v - hi : 0);

/**
 * How far conditions are from the seasonal target, in "yellow margins": 0 inside the range (green),
 * up to 1 a small deviation (yellow), above 1 a large one (red) — the backend's color_for rule.
 */
export function comfortSeverity(t: number, h: number, target: ClimateTarget): number {
	const dt = Math.abs(deviation(t, target.temperature_min_c, target.temperature_max_c)) / target.yellow_temperature_margin_c;
	const dh = Math.abs(deviation(h, target.humidity_min_pct, target.humidity_max_pct)) / target.yellow_humidity_margin_pct;
	return Math.max(dt, dh);
}

interface Grid {
	lon0: number;
	lat0: number;
	dLon: number;
	dLat: number;
	nx: number;
	ny: number;
	temperature: Float64Array;
	humidity: Float64Array;
	cells: (ClimateCell | null)[];
	tRange: [number, number];
	hRange: [number, number];
}

function smallestStep(sorted: number[]): number {
	let best = Infinity;
	for (let i = 1; i < sorted.length; i++) {
		const d = sorted[i] - sorted[i - 1];
		if (d > 1e-9 && d < best) best = d;
	}
	return Number.isFinite(best) ? best : 1;
}

/** Missing nodes take the mean of their known neighbours, ring by ring, so interpolation has support everywhere. */
function fillGaps(values: Float64Array, nx: number, ny: number) {
	for (let pass = 0; pass < nx + ny; pass++) {
		let missing = 0;
		const next = values.slice();
		for (let y = 0; y < ny; y++) {
			for (let x = 0; x < nx; x++) {
				const k = y * nx + x;
				if (!Number.isNaN(values[k])) continue;
				let sum = 0;
				let n = 0;
				if (x > 0 && !Number.isNaN(values[k - 1])) { sum += values[k - 1]; n++; }
				if (x < nx - 1 && !Number.isNaN(values[k + 1])) { sum += values[k + 1]; n++; }
				if (y > 0 && !Number.isNaN(values[k - nx])) { sum += values[k - nx]; n++; }
				if (y < ny - 1 && !Number.isNaN(values[k + nx])) { sum += values[k + nx]; n++; }
				if (n) next[k] = sum / n;
				else missing++;
			}
		}
		values.set(next);
		if (!missing) return;
	}
}

function buildGrid(cells: ClimateCell[]): Grid {
	const lons = [...new Set(cells.map((c) => c.grid_lon))].sort((a, b) => a - b);
	const lats = [...new Set(cells.map((c) => c.grid_lat))].sort((a, b) => a - b);
	const dLon = smallestStep(lons);
	const dLat = smallestStep(lats);
	const lon0 = lons[0];
	const lat0 = lats[0];
	const nx = Math.round((lons[lons.length - 1] - lon0) / dLon) + 1;
	const ny = Math.round((lats[lats.length - 1] - lat0) / dLat) + 1;
	const temperature = new Float64Array(nx * ny).fill(NaN);
	const humidity = new Float64Array(nx * ny).fill(NaN);
	const byNode: (ClimateCell | null)[] = new Array(nx * ny).fill(null);
	let tMin = Infinity, tMax = -Infinity, hMin = Infinity, hMax = -Infinity;
	for (const c of cells) {
		const k = Math.round((c.grid_lat - lat0) / dLat) * nx + Math.round((c.grid_lon - lon0) / dLon);
		temperature[k] = c.temperature_c;
		humidity[k] = c.humidity_pct;
		byNode[k] = c;
		tMin = Math.min(tMin, c.temperature_c);
		tMax = Math.max(tMax, c.temperature_c);
		hMin = Math.min(hMin, c.humidity_pct);
		hMax = Math.max(hMax, c.humidity_pct);
	}
	fillGaps(temperature, nx, ny);
	fillGaps(humidity, nx, ny);
	return { lon0, lat0, dLon, dLat, nx, ny, temperature, humidity, cells: byNode, tRange: [tMin, tMax], hRange: [hMin, hMax] };
}

/** Catmull-Rom weights of the four nodes around fractional position t ∈ [0, 1). */
function cubicWeights(t: number, out: Float64Array, offset = 0) {
	const t2 = t * t;
	const t3 = t2 * t;
	out[offset] = (-t3 + 2 * t2 - t) / 2;
	out[offset + 1] = (3 * t3 - 5 * t2 + 2) / 2;
	out[offset + 2] = (-3 * t3 + 4 * t2 + t) / 2;
	out[offset + 3] = (t3 - t2) / 2;
}

/** Node index and fractional offset of a coordinate on one grid axis (clamped to the grid). */
function axisPosition(value: number, origin: number, step: number, count: number): [number, number] {
	const g = clamp((value - origin) / step, 0, count - 1);
	const i = Math.min(Math.floor(g), Math.max(0, count - 2));
	return [i, g - i];
}

/** Interpolated temperature and humidity at one point (16 nodes around it). */
function sample(grid: Grid, lon: number, lat: number): [number, number] {
	const [i, fx] = axisPosition(lon, grid.lon0, grid.dLon, grid.nx);
	const [j, fy] = axisPosition(lat, grid.lat0, grid.dLat, grid.ny);
	const wx = new Float64Array(4);
	const wy = new Float64Array(4);
	cubicWeights(fx, wx);
	cubicWeights(fy, wy);
	let t = 0;
	let h = 0;
	for (let b = 0; b < 4; b++) {
		const y = clamp(j - 1 + b, 0, grid.ny - 1);
		for (let a = 0; a < 4; a++) {
			const x = clamp(i - 1 + a, 0, grid.nx - 1);
			const w = wy[b] * wx[a];
			t += w * grid.temperature[y * grid.nx + x];
			h += w * grid.humidity[y * grid.nx + x];
		}
	}
	return [clamp(t, grid.tRange[0], grid.tRange[1]), clamp(h, grid.hRange[0], grid.hRange[1])];
}

function forEachPolygon(regions: RegionCollection, fn: (rings: number[][][]) => void) {
	for (const feature of regions.features) {
		const g = feature.geometry;
		if (!g) continue;
		if (g.type === 'Polygon') fn(g.coordinates);
		else for (const polygon of g.coordinates) fn(polygon);
	}
}

/** Contour segments of `field` (nx × ny samples) at `level`, by marching squares, in lon/lat. */
function contourSegments(
	field: Float64Array, nx: number, ny: number, lonAt: (x: number) => number, latAt: (y: number) => number, level: number,
): LngLat[][] {
	const segments: LngLat[][] = [];
	for (let y = 0; y < ny - 1; y++) {
		for (let x = 0; x < nx - 1; x++) {
			const v0 = field[y * nx + x]; // bottom-left
			const v1 = field[y * nx + x + 1]; // bottom-right
			const v2 = field[(y + 1) * nx + x + 1]; // top-right
			const v3 = field[(y + 1) * nx + x]; // top-left
			const c = (v0 > level ? 1 : 0) | (v1 > level ? 2 : 0) | (v2 > level ? 4 : 0) | (v3 > level ? 8 : 0);
			if (c === 0 || c === 15) continue;
			const at = (a: number, b: number) => (level - a) / (b - a);
			const bottom = (): LngLat => [lonAt(x + at(v0, v1)), latAt(y)];
			const right = (): LngLat => [lonAt(x + 1), latAt(y + at(v1, v2))];
			const top = (): LngLat => [lonAt(x + 1 - at(v2, v3)), latAt(y + 1)];
			const left = (): LngLat => [lonAt(x), latAt(y + 1 - at(v3, v0))];
			switch (c) {
				case 1: case 14: segments.push([left(), bottom()]); break;
				case 2: case 13: segments.push([bottom(), right()]); break;
				case 3: case 12: segments.push([left(), right()]); break;
				case 4: case 11: segments.push([right(), top()]); break;
				case 6: case 9: segments.push([bottom(), top()]); break;
				case 7: case 8: segments.push([left(), top()]); break;
				case 5: segments.push([left(), bottom()], [right(), top()]); break;
				case 10: segments.push([bottom(), right()], [top(), left()]); break;
			}
		}
	}
	return segments;
}

/** Builds the coloured, clipped surface image and the isolines of one season and mode. */
export async function buildClimateSurface(
	cells: ClimateCell[], regions: RegionCollection, mode: ClimateMode, target: ClimateTarget,
): Promise<ClimateSurface> {
	const grid = buildGrid(cells);

	let west = Infinity, east = -Infinity, south = Infinity, north = -Infinity;
	forEachPolygon(regions, (rings) => {
		for (const [lon, lat] of rings[0]) {
			west = Math.min(west, lon);
			east = Math.max(east, lon);
			south = Math.min(south, lat);
			north = Math.max(north, lat);
		}
	});
	if (!Number.isFinite(west)) {
		// No boundaries: show the whole grid.
		west = grid.lon0;
		east = grid.lon0 + grid.dLon * (grid.nx - 1);
		south = grid.lat0;
		north = grid.lat0 + grid.dLat * (grid.ny - 1);
	}
	west -= PADDING_DEG;
	east += PADDING_DEG;
	south -= PADDING_DEG;
	north += PADDING_DEG;

	const yNorth = mercY(north);
	const ySouth = mercY(south);
	const width = IMAGE_WIDTH;
	const height = Math.max(1, Math.round((width * (yNorth - ySouth)) / (((east - west) * Math.PI) / 180)));
	const toPx = (lon: number, lat: number): [number, number] => [
		((lon - west) / (east - west)) * width,
		((yNorth - mercY(lat)) / (yNorth - ySouth)) * height,
	];

	// Colours, row by row: interpolate along latitude for each grid column, then along longitude.
	const [dMin, dMax] = mode === 'temperature' ? TEMPERATURE_DOMAIN : COMFORT_DOMAIN;
	const lut = colorLut(mode === 'temperature' ? TEMPERATURE_STOPS : COMFORT_STOPS, [dMin, dMax], LUT_SIZE);
	const image = new ImageData(width, height);
	const pixels = image.data;
	const colIndex = new Int32Array(width);
	const colWeights = new Float64Array(width * 4);
	for (let px = 0; px < width; px++) {
		const [i, fx] = axisPosition(west + ((px + 0.5) / width) * (east - west), grid.lon0, grid.dLon, grid.nx);
		colIndex[px] = i;
		cubicWeights(fx, colWeights, px * 4);
	}
	const rowWeights = new Float64Array(4);
	const rowT = new Float64Array(grid.nx);
	const rowH = new Float64Array(grid.nx);
	for (let py = 0; py < height; py++) {
		const lat = latFromMercY(yNorth - ((py + 0.5) / height) * (yNorth - ySouth));
		const [j, fy] = axisPosition(lat, grid.lat0, grid.dLat, grid.ny);
		cubicWeights(fy, rowWeights);
		for (let x = 0; x < grid.nx; x++) {
			let t = 0;
			let h = 0;
			for (let b = 0; b < 4; b++) {
				const k = clamp(j - 1 + b, 0, grid.ny - 1) * grid.nx + x;
				t += rowWeights[b] * grid.temperature[k];
				h += rowWeights[b] * grid.humidity[k];
			}
			rowT[x] = t;
			rowH[x] = h;
		}
		for (let px = 0; px < width; px++) {
			const i = colIndex[px];
			const o = px * 4;
			let t = 0;
			let h = 0;
			for (let a = 0; a < 4; a++) {
				const x = clamp(i - 1 + a, 0, grid.nx - 1);
				t += colWeights[o + a] * rowT[x];
				h += colWeights[o + a] * rowH[x];
			}
			t = clamp(t, grid.tRange[0], grid.tRange[1]);
			h = clamp(h, grid.hRange[0], grid.hRange[1]);
			const value = mode === 'temperature' ? t : comfortSeverity(t, h, target);
			const li = Math.round(clamp((value - dMin) / (dMax - dMin), 0, 1) * (LUT_SIZE - 1)) * 3;
			const p = (py * width + px) * 4;
			pixels[p] = lut[li];
			pixels[p + 1] = lut[li + 1];
			pixels[p + 2] = lut[li + 2];
			pixels[p + 3] = 255;
		}
	}

	// The regions as a mask with soft edges: it clips the colours and answers "is this point inside".
	const mask = document.createElement('canvas');
	mask.width = width;
	mask.height = height;
	const mctx = mask.getContext('2d');
	if (!mctx) throw new Error('Canvas 2D is not available');
	mctx.fillStyle = '#fff';
	mctx.filter = 'blur(0.8px)';
	forEachPolygon(regions, (rings) => {
		mctx.beginPath();
		for (const ring of rings) {
			ring.forEach(([lon, lat], idx) => {
				const [x, y] = toPx(lon, lat);
				if (idx === 0) mctx.moveTo(x, y);
				else mctx.lineTo(x, y);
			});
			mctx.closePath();
		}
		mctx.fill('evenodd');
	});
	const maskAlpha = mctx.getImageData(0, 0, width, height).data;

	const canvas = document.createElement('canvas');
	canvas.width = width;
	canvas.height = height;
	const ctx = canvas.getContext('2d');
	if (!ctx) throw new Error('Canvas 2D is not available');
	ctx.putImageData(image, 0, 0);
	ctx.globalCompositeOperation = 'destination-in';
	ctx.drawImage(mask, 0, 0);

	const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'));
	const url = blob ? URL.createObjectURL(blob) : canvas.toDataURL('image/png');

	const inside = (lon: number, lat: number) => {
		const [x, y] = toPx(lon, lat);
		const xi = Math.floor(x);
		const yi = Math.floor(y);
		if (xi < 0 || yi < 0 || xi >= width || yi >= height) return false;
		return maskAlpha[(yi * width + xi) * 4 + 3] > 127;
	};

	const nearest = (lon: number, lat: number): ClimateCell | null => {
		const x = Math.round((lon - grid.lon0) / grid.dLon);
		const y = Math.round((lat - grid.lat0) / grid.dLat);
		if (x < 0 || y < 0 || x >= grid.nx || y >= grid.ny) return null;
		return grid.cells[y * grid.nx + x];
	};

	// Isolines on a regular lon/lat sample grid, kept inside the regions.
	const sx = CONTOUR_SAMPLES;
	const sy = Math.max(2, Math.round((CONTOUR_SAMPLES * (north - south)) / (east - west)));
	const lonAt = (x: number) => west + (x / (sx - 1)) * (east - west);
	const latAt = (y: number) => south + (y / (sy - 1)) * (north - south);
	const field = new Float64Array(sx * sy);
	for (let y = 0; y < sy; y++) {
		for (let x = 0; x < sx; x++) {
			const [t, h] = sample(grid, lonAt(x), latAt(y));
			field[y * sx + x] = mode === 'temperature' ? t : comfortSeverity(t, h, target);
		}
	}
	const levels: { value: number; major: boolean }[] = [];
	if (mode === 'temperature') {
		const from = Math.ceil(grid.tRange[0] / ISOTHERM_STEP_C) * ISOTHERM_STEP_C;
		for (let v = from; v <= grid.tRange[1]; v += ISOTHERM_STEP_C) levels.push({ value: v, major: v % 10 === 0 });
	} else {
		levels.push({ value: 0.001, major: false }, { value: 1, major: true });
	}
	const isolines: IsolineCollection = { type: 'FeatureCollection', features: [] };
	for (const level of levels) {
		const segments = contourSegments(field, sx, sy, lonAt, latAt, level.value).filter(([a, b]) =>
			inside((a[0] + b[0]) / 2, (a[1] + b[1]) / 2),
		);
		if (segments.length) {
			isolines.features.push({ type: 'Feature', properties: level, geometry: { type: 'MultiLineString', coordinates: segments } });
		}
	}

	return {
		url,
		coordinates: [[west, north], [east, north], [east, south], [west, south]],
		bounds: [west, south, east, north],
		isolines,
		inside,
		nearest,
	};
}
