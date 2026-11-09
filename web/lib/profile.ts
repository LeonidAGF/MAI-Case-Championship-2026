import { Profile, DEFAULT_PROFILE } from '@/types/profile';

const STORAGE_KEY = 'atlas:profile';
const URL_PARAM = 'p';

function toBase64Url(str: string): string {
	const b64 =
		typeof window === 'undefined'
		? Buffer.from(str, 'utf-8').toString('base64')
		: btoa(unescape(encodeURIComponent(str)));
	return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64Url(s: string): string {
	const b64 = s.replace(/-/g, '+').replace(/_/g, '/');
	const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
	return typeof window === 'undefined'
		? Buffer.from(padded, 'base64').toString('utf-8')
		: decodeURIComponent(escape(atob(padded)));
}

export function encodeProfile(p: Profile): string {
  	return toBase64Url(JSON.stringify(p));
}

export function decodeProfile(encoded: string): Profile | null {
	try {
		const parsed = JSON.parse(fromBase64Url(encoded));
		if (parsed?.v !== 1) return null;
		if (!parsed.filters || !parsed.weights) return null;
		return parsed as Profile;
	} catch {
		return null;
	}
}

export function readProfileFromUrl(): Profile | null {
	if (typeof window === 'undefined') return null;
	const raw = new URL(window.location.href).searchParams.get(URL_PARAM);
	return raw ? decodeProfile(raw) : null;
}

export function writeProfileToUrl(p: Profile): void {
	const url = new URL(window.location.href);
	url.searchParams.set(URL_PARAM, encodeProfile(p));
	window.history.replaceState({}, '', url.toString());
}

export function readProfileFromStorage(): Profile | null {
	if (typeof window === 'undefined') return null;
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		return raw ? decodeProfile(raw) : null;
	} catch {
		return null;
	}
}

export function writeProfileToStorage(p: Profile): void {
	try {
		localStorage.setItem(STORAGE_KEY, encodeProfile(p));
	} catch {}
}

export function resolveInitialProfile(): Profile {
	return (
		readProfileFromUrl() ??
		readProfileFromStorage() ??
		DEFAULT_PROFILE
	);
}

export function persistProfile(p: Profile): void {
	writeProfileToStorage(p);
	writeProfileToUrl(p);
}
