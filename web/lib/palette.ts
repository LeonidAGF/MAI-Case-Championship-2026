/**
 * Colour scales of the maps and their legends. Colours between the stops are blended in OKLab, so
 * the gradients stay even (no muddy or washed-out middle), and the same functions feed MapLibre
 * (interpolate-lab), the climate canvas (a lookup table) and CSS legends.
 */

export type ColorStop = readonly [value: number, color: string];
type Rgb = [number, number, number];

/** Accessibility index 0–100: crimson (low) → apricot → sun → mint → sky → brand indigo (high). */
export const INDEX_STOPS: readonly ColorStop[] = [
	[0, '#d6455d'],
	[25, '#ef8a4c'],
	[50, '#f4c64e'],
	[70, '#59c3a6'],
	[85, '#3d8fd8'],
	[100, '#5a48dd'],
];
export const INDEX_DOMAIN: readonly [number, number] = [0, 100];
export const NO_DATA_COLOR = '#c3c7d9';

/** Season mean temperature, °C. One scale for all seasons, so they can be compared. */
export const TEMPERATURE_DOMAIN: readonly [number, number] = [-15, 30];
export const TEMPERATURE_STOPS: readonly ColorStop[] = [
	[-15, '#2e2a7f'],
	[-9, '#3f5fcf'],
	[-3, '#5ca6ea'],
	[3, '#79d2df'],
	[9, '#8fdbb0'],
	[14, '#cfe58a'],
	[19, '#f8d35b'],
	[23, '#f6a03f'],
	[27, '#e8603c'],
	[30, '#b8294f'],
];

/**
 * Distance from the project target range in "yellow margins" (lib/climate.ts, comfortSeverity):
 * 0 inside the range, up to 1 a small deviation, above 1 a large one.
 */
export const COMFORT_DOMAIN: readonly [number, number] = [0, 2.5];
export const COMFORT_STOPS: readonly ColorStop[] = [
	[0, '#1fbf8f'],
	[0.15, '#8fd36a'],
	[0.55, '#f2d24b'],
	[1, '#f39a3d'],
	[1.6, '#e5534b'],
	[2.5, '#b02a4f'],
];

function hexToRgb(hex: string): Rgb {
	const n = parseInt(hex.slice(1), 16);
	return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const toLinear = (c: number) => {
	const v = c / 255;
	return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
};

const fromLinear = (v: number) => {
	const c = v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
	return Math.round(Math.min(1, Math.max(0, c)) * 255);
};

function rgbToOklab([r8, g8, b8]: Rgb): Rgb {
	const r = toLinear(r8);
	const g = toLinear(g8);
	const b = toLinear(b8);
	const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
	const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
	const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
	return [
		0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
		1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
		0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
	];
}

function oklabToRgb([L, a, b]: Rgb): Rgb {
	const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
	const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
	const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
	return [
		fromLinear(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
		fromLinear(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
		fromLinear(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
	];
}

/** Colour of `value` on the scale as [r, g, b], 0–255; values outside it take the end colours. */
export function rgbAt(stops: readonly ColorStop[], value: number): Rgb {
	if (!(value > stops[0][0])) return hexToRgb(stops[0][1]);
	const last = stops[stops.length - 1];
	if (value >= last[0]) return hexToRgb(last[1]);
	let i = 1;
	while (value > stops[i][0]) i++;
	const [v0, c0] = stops[i - 1];
	const [v1, c1] = stops[i];
	const t = (value - v0) / (v1 - v0);
	const a = rgbToOklab(hexToRgb(c0));
	const b = rgbToOklab(hexToRgb(c1));
	return oklabToRgb([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]);
}

export function colorAt(stops: readonly ColorStop[], value: number): string {
	const [r, g, b] = rgbAt(stops, value);
	return `rgb(${r} ${g} ${b})`;
}

/** `size` colours spread evenly over [min, max] as r, g, b triples: a lookup table for canvas pixels. */
export function colorLut(stops: readonly ColorStop[], [min, max]: readonly [number, number], size = 512): Uint8ClampedArray {
	const lut = new Uint8ClampedArray(size * 3);
	for (let i = 0; i < size; i++) {
		lut.set(rgbAt(stops, min + ((max - min) * i) / (size - 1)), i * 3);
	}
	return lut;
}

/** CSS gradient of the scale over [min, max], sampled densely so the browser's sRGB blend matches OKLab. */
export function cssGradient(stops: readonly ColorStop[], [min, max]: readonly [number, number], direction = 'to right', samples = 24): string {
	const parts: string[] = [];
	for (let i = 0; i <= samples; i++) {
		parts.push(`${colorAt(stops, min + ((max - min) * i) / samples)} ${((i / samples) * 100).toFixed(1)}%`);
	}
	return `linear-gradient(${direction}, ${parts.join(', ')})`;
}

/** Flat [value, colour, value, colour, …] list for a MapLibre interpolate expression. */
export function stopsForMap(stops: readonly ColorStop[]): (number | string)[] {
	return stops.flatMap(([value, color]) => [value, color]);
}
