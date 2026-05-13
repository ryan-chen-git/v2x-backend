import test from 'node:test';
import assert from 'node:assert/strict';

import {
	normalizeZone,
	shouldDrawZone,
	shouldSyncZone,
	zoneAlertMode,
} from '../src/lib/zoneRules.ts';

const baseZone = {
	id: 'zone-1',
	name: 'Zone 1',
	message: 'Heads up',
	signal_type: 'warning',
	polygon: [[0, 0], [1, 0], [1, 1], [0, 1]],
	color: '#ef4444',
};

test('legacy zones without zone_kind behave as geofences', () => {
	const zone = normalizeZone(baseZone);

	assert.equal(zone.zone_kind, 'geofence');
	assert.equal(shouldDrawZone(zone), true);
	assert.equal(shouldSyncZone(zone), true);
	assert.equal(zoneAlertMode(zone), 'persistent');
});

test('warning zones notify only and do not draw or sync boundaries', () => {
	const zone = normalizeZone({
		...baseZone,
		zone_kind: 'warning',
		color: undefined,
	});

	assert.equal(zone.zone_kind, 'warning');
	assert.equal(zone.color, '#f59e0b');
	assert.equal(shouldDrawZone(zone), false);
	assert.equal(shouldSyncZone(zone), false);
	assert.equal(zoneAlertMode(zone), 'transient');
});

test('geofence zones keep the red boundary behavior', () => {
	const zone = normalizeZone({
		...baseZone,
		zone_kind: 'geofence',
		color: undefined,
	});

	assert.equal(zone.zone_kind, 'geofence');
	assert.equal(zone.color, '#ef4444');
	assert.equal(shouldDrawZone(zone), true);
	assert.equal(shouldSyncZone(zone), true);
	assert.equal(zoneAlertMode(zone), 'persistent');
});
