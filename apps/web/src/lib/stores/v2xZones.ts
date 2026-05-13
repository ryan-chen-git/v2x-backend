/**
 * V2X Zone Store — manages drawn V2X zones and proximity detection.
 *
 * Zones are drawn on the pre-drive map editor and persisted to localStorage.
 * During driving, the store checks if the car's position falls inside any
 * zone and fires alerts via the existing V2xToast system.
 *
 * Coordinate conversion: CARLA UE4 world coords → GPS [lon, lat]
 * using the geo-reference origin from the /map-data API.
 */

import { writable, get, type Writable } from 'svelte/store';
import type { V2xZone } from '$lib/types';
import { carlaToGps } from '$lib/geo';
import { normalizeZones, zoneAlertMode } from '$lib/zoneRules';

const STORAGE_KEY = 'v2x-zones';

// ── Zone CRUD Store ──

function loadFromStorage(): V2xZone[] {
	if (typeof localStorage === 'undefined') return [];
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		return raw ? normalizeZones(JSON.parse(raw)) : [];
	} catch {
		return [];
	}
}

function saveToStorage(zones: V2xZone[]): void {
	if (typeof localStorage === 'undefined') return;
	try {
		localStorage.setItem(STORAGE_KEY, JSON.stringify(zones));
	} catch {
		// Storage full or unavailable
	}
}

const v2xZonesStore = writable<V2xZone[]>(loadFromStorage());

export const v2xZones: Writable<V2xZone[]> = {
	subscribe: v2xZonesStore.subscribe,
	set: (zones) => v2xZonesStore.set(normalizeZones(zones)),
	update: (fn) => v2xZonesStore.update((zones) => normalizeZones(fn(zones))),
};

// Auto-persist on change
v2xZones.subscribe(saveToStorage);

export function addZone(zone: V2xZone): void {
	v2xZones.update((zones) => [...zones, zone]);
}

export function updateZone(id: string, updates: Partial<V2xZone>): void {
	v2xZones.update((zones) =>
		zones.map((z) => (z.id === id ? { ...z, ...updates } : z))
	);
}

export function removeZone(id: string): void {
	v2xZones.update((zones) => zones.filter((z) => z.id !== id));
}

export function clearZones(): void {
	v2xZones.set([]);
}

export { carlaToGps } from '$lib/geo';

// ── Point-in-Polygon (ray casting) ──

export function pointInPolygon(
	point: [number, number],
	polygon: [number, number][]
): boolean {
	let inside = false;
	for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
		const xi = polygon[i][0],
			yi = polygon[i][1];
		const xj = polygon[j][0],
			yj = polygon[j][1];
		if (
			yi > point[1] !== yj > point[1] &&
			point[0] < ((xj - xi) * (point[1] - yi)) / (yj - yi) + xi
		) {
			inside = !inside;
		}
	}
	return inside;
}

// ── Proximity Detection ──

/** Zones the car is currently inside. Shown as persistent alerts. */
export const activeZoneAlerts = writable<{ zone: V2xZone; }[]>([]);

export const zoneEntryNotifications = writable<{ zone: V2xZone; _uid: number; }[]>([]);

let previousWarningZoneIds = new Set<string>();

/**
 * Check if a CARLA position is inside any V2X zone.
 * Updates activeZoneAlerts: adds zones on entry, removes on exit.
 */
export function checkZoneProximity(
	carlaX: number,
	carlaY: number,
	originLat: number,
	originLon: number
): void {
	const gpsPos = carlaToGps(carlaX, carlaY, originLat, originLon);
	const zones = get(v2xZones);

	const insideZones: { zone: V2xZone }[] = [];
	const currentWarningZoneIds = new Set<string>();

	for (const zone of zones) {
		if (zone.polygon.length < 3) continue;
		if (pointInPolygon(gpsPos, zone.polygon)) {
			if (zoneAlertMode(zone) === 'persistent') {
				insideZones.push({ zone });
			} else {
				currentWarningZoneIds.add(zone.id);
				if (!previousWarningZoneIds.has(zone.id)) {
					zoneEntryNotifications.update((alerts) => [
						...alerts,
						{ zone, _uid: Date.now() + Math.random() },
					]);
				}
			}
		}
	}

	previousWarningZoneIds = currentWarningZoneIds;
	activeZoneAlerts.set(insideZones);
}

/** Reset zone alerts (call when ending a session). */
export function resetZoneProximity(): void {
	activeZoneAlerts.set([]);
	zoneEntryNotifications.set([]);
	previousWarningZoneIds = new Set();
}
