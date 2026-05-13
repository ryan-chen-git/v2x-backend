import type { V2xZone, V2xZoneKind } from './types';

export const DEFAULT_ZONE_COLORS: Record<V2xZoneKind, string> = {
	warning: '#f59e0b',
	geofence: '#ef4444',
};

type ZoneInput = Omit<V2xZone, 'zone_kind' | 'color'> & {
	zone_kind?: V2xZoneKind;
	color?: string;
};

export function normalizeZone(zone: ZoneInput | V2xZone): V2xZone {
	const zoneKind = zone.zone_kind ?? 'geofence';
	return {
		...zone,
		zone_kind: zoneKind,
		color: zone.color ?? DEFAULT_ZONE_COLORS[zoneKind],
	};
}

export function normalizeZones(zones: (ZoneInput | V2xZone)[]): V2xZone[] {
	return zones.map(normalizeZone);
}

export function shouldDrawZone(zone: Pick<V2xZone, 'zone_kind'>): boolean {
	return zone.zone_kind === 'geofence';
}

export function shouldSyncZone(zone: Pick<V2xZone, 'zone_kind'>): boolean {
	return zone.zone_kind === 'geofence';
}

export function zoneAlertMode(
	zone: Pick<V2xZone, 'zone_kind'>
): 'persistent' | 'transient' {
	return zone.zone_kind === 'geofence' ? 'persistent' : 'transient';
}
