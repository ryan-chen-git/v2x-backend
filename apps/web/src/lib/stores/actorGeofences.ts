import { writable } from 'svelte/store';
import type { ActorGeofenceAlert, DynamicActor } from '$lib/types';
import { getActorGeofenceAlert } from '$lib/actorGeofenceRules';

export const activeActorGeofenceAlerts = writable<ActorGeofenceAlert[]>([]);

export function checkActorGeofenceProximity(
	egoPos: [number, number, number],
	actors: DynamicActor[]
): void {
	activeActorGeofenceAlerts.set(
		actors
			.map((actor) => getActorGeofenceAlert(actor, egoPos))
			.filter((alert): alert is ActorGeofenceAlert => alert !== null)
	);
}

export function resetActorGeofenceProximity(): void {
	activeActorGeofenceAlerts.set([]);
}
