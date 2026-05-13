import test from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';
import { pathToFileURL } from 'node:url';

register(
	`data:text/javascript,${encodeURIComponent(`
		export async function resolve(specifier, context, nextResolve) {
			if (
				(specifier.startsWith('./') || specifier.startsWith('../')) &&
				!/\\.[a-zA-Z0-9]+$/.test(specifier)
			) {
				try {
					return await nextResolve(specifier + '.ts', context);
				} catch {
					return nextResolve(specifier, context);
				}
			}

			return nextResolve(specifier, context);
		}
	`)}`,
	pathToFileURL(`${process.cwd()}/`)
);

const {
	buildActorGeofencePolygon,
	getActorGeofenceAlert,
} = await import('../src/lib/actorGeofenceRules.ts');

const actor = {
	actor_id: 7,
	blueprint: 'vehicle.carlamotors.firetruck',
	name: 'Firetruck',
	pos: [100, 200, 0],
	yaw: 0,
	geofence_radius: 35,
	message: 'Firefighter response vehicle active',
	autopilot: true,
};

test('buildActorGeofencePolygon returns a closed polygon', () => {
	const polygon = buildActorGeofencePolygon(actor, 37.0, -122.0, 8);

	assert.equal(polygon.length, 9);
	assert.deepEqual(polygon.at(0), polygon.at(-1));
});

test('getActorGeofenceAlert returns rounded distance when ego is inside radius', () => {
	const alert = getActorGeofenceAlert(actor, [110, 220, 0]);

	assert.deepEqual(alert, { actor, distance: 22 });
});

test('getActorGeofenceAlert returns null when ego is outside radius', () => {
	const alert = getActorGeofenceAlert(actor, [200, 300, 0]);

	assert.equal(alert, null);
});

test('invalid actor radius returns no polygon and no alert', () => {
	const invalidActor = { ...actor, geofence_radius: 0 };
	const polygon = buildActorGeofencePolygon(invalidActor, 37.0, -122.0, 8);
	const alert = getActorGeofenceAlert(invalidActor, [100, 200, 0]);

	assert.deepEqual(polygon, []);
	assert.equal(alert, null);
});

test('invalid actor position returns no polygon and no alert', () => {
	const invalidActor = { ...actor, pos: [Number.NaN, 200, 0] };
	const polygon = buildActorGeofencePolygon(invalidActor, 37.0, -122.0, 8);
	const alert = getActorGeofenceAlert(invalidActor, [100, 200, 0]);

	assert.deepEqual(polygon, []);
	assert.equal(alert, null);
});

test('invalid ego position returns no alert', () => {
	const alert = getActorGeofenceAlert(actor, [Number.POSITIVE_INFINITY, 200, 0]);

	assert.equal(alert, null);
});
