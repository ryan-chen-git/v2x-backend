import type { ActorGeofenceAlert, DynamicActor } from './types';
import { carlaToGps } from './geo';

export const DYNAMIC_GEOFENCE_COLOR = '#ef4444';

function getActorGeometry(
	actor: DynamicActor
): { x: number; y: number; radius: number } | null {
	const x = actor?.pos?.[0];
	const y = actor?.pos?.[1];
	const radius = actor?.geofence_radius;

	if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(radius)) {
		return null;
	}

	if (radius <= 0) {
		return null;
	}

	return { x, y, radius };
}

export function buildActorGeofencePolygon(
	actor: DynamicActor,
	originLat: number,
	originLon: number,
	segments = 48
): [number, number][] {
	const geometry = getActorGeometry(actor);
	const segmentCount = Math.floor(segments);
	if (
		geometry === null ||
		!Number.isFinite(originLat) ||
		!Number.isFinite(originLon) ||
		!Number.isFinite(segmentCount) ||
		segmentCount <= 0
	) {
		return [];
	}

	const polygon: [number, number][] = [];

	for (let i = 0; i <= segmentCount; i += 1) {
		const angle = (i / segmentCount) * Math.PI * 2;
		const x = geometry.x + Math.cos(angle) * geometry.radius;
		const y = geometry.y + Math.sin(angle) * geometry.radius;
		polygon.push(carlaToGps(x, y, originLat, originLon));
	}

	return polygon;
}

export function getActorGeofenceAlert(
	actor: DynamicActor,
	egoPos: [number, number, number]
): ActorGeofenceAlert | null {
	const geometry = getActorGeometry(actor);
	const egoX = egoPos?.[0];
	const egoY = egoPos?.[1];
	if (geometry === null || !Number.isFinite(egoX) || !Number.isFinite(egoY)) {
		return null;
	}

	const dx = geometry.x - egoX;
	const dy = geometry.y - egoY;
	const distance = Math.hypot(dx, dy);

	if (distance > geometry.radius) {
		return null;
	}

	return { actor, distance: Math.round(distance) };
}
