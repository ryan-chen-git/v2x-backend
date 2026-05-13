<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { activeActorGeofenceAlerts } from '$lib/stores/actorGeofences';
	import { v2xAlerts } from '$lib/stores/driveSocket';
	import { activeZoneAlerts, zoneEntryNotifications } from '$lib/stores/v2xZones';
	import type { V2xZone } from '$lib/types';

	// Bridge re-broadcasts each in-range server alert every telemetry tick.
	// Remove one only after it stops being refreshed.
	const STALE_MS = 1500;
	const ZONE_NOTIFICATION_MS = 5000;
	let cleanup: ReturnType<typeof setInterval> | null = null;
	const timers = new Map<number, ReturnType<typeof setTimeout>>();

	onMount(() => {
		cleanup = setInterval(() => {
			const cutoff = Date.now() - STALE_MS;
			v2xAlerts.update(list =>
				list.filter(a => ((a as any)._lastSeen ?? Date.now()) > cutoff),
			);
		}, 250);
	});

	$effect(() => {
		for (const alert of $zoneEntryNotifications) {
			const uid = alert._uid;
			if (uid && !timers.has(uid)) {
				timers.set(uid, setTimeout(() => {
					zoneEntryNotifications.update(list => list.filter(a => a._uid !== uid));
					timers.delete(uid);
				}, ZONE_NOTIFICATION_MS));
			}
		}
	});

	onDestroy(() => {
		if (cleanup) clearInterval(cleanup);
		for (const timer of timers.values()) clearTimeout(timer);
		timers.clear();
	});

	function dismiss(alert: any) {
		v2xAlerts.update(list => list.filter(a => (a as any)._uid !== alert._uid));
	}

	function dismissZoneNotification(uid: number) {
		zoneEntryNotifications.update(list => list.filter(a => a._uid !== uid));
	}

	function typeColor(type: string): string {
		switch (type) {
			case 'warning': return 'bg-red-600/90 border-red-400';
			case 'alert': return 'bg-orange-600/90 border-orange-400';
			case 'info': return 'bg-blue-600/90 border-blue-400';
			default: return 'bg-gray-600/90 border-gray-400';
		}
	}

	function typeIcon(type: string): string {
		switch (type) {
			case 'warning': return '\u26A0';
			case 'alert': return '\uD83D\uDEA8';
			case 'info': return '\u2139';
			default: return '\uD83D\uDCE1';
		}
	}

	function typeLabel(type: string): string {
		switch (type) {
			case 'warning': return 'V2X WARNING';
			case 'alert': return 'V2X ALERT';
			case 'info': return 'V2X INFO';
			default: return 'V2X SIGNAL';
		}
	}

	function zoneColor(zone: V2xZone): string {
		return zone.zone_kind === 'geofence'
			? 'bg-red-600/90 border-red-400'
			: 'bg-amber-600/90 border-amber-300';
	}

	function zoneIcon(zone: V2xZone): string {
		return zone.zone_kind === 'geofence' ? '🚫' : '⚠';
	}

	function zoneLabel(zone: V2xZone): string {
		return zone.zone_kind === 'geofence' ? 'GEOFENCE' : 'WARNING ZONE';
	}
</script>

{#if $v2xAlerts.length > 0 || $activeZoneAlerts.length > 0 || $zoneEntryNotifications.length > 0 || $activeActorGeofenceAlerts.length > 0}
	<div class="absolute top-14 right-2 z-40 flex flex-col gap-3 pointer-events-auto max-w-[480px]">
		<!-- Warning-zone notifications (fire once on entry, auto-dismiss) -->
		{#each $zoneEntryNotifications as entry (entry._uid)}
			<div class="rounded-lg border-l-[6px] px-5 py-3 shadow-lg backdrop-blur-sm animate-slide-in {zoneColor(entry.zone)}">
				<div class="flex items-start gap-3">
					<span class="text-[54px] leading-none">{zoneIcon(entry.zone)}</span>
					<div class="flex-1 min-w-0">
						<p class="text-[30px] font-bold text-white/70 uppercase tracking-wide">{zoneLabel(entry.zone)}</p>
						<p class="text-[42px] font-medium text-white leading-tight">{entry.zone.message || entry.zone.name}</p>
					</div>
					<button onclick={() => dismissZoneNotification(entry._uid)}
						class="text-white/50 hover:text-white text-[36px] leading-none p-1">
						✕
					</button>
				</div>
			</div>
		{/each}

		<!-- Server-side V2X alerts (refreshed by telemetry while active) -->
		{#each $v2xAlerts as alert ((alert as any)._uid)}
			<div class="rounded-lg border-l-[6px] px-5 py-3 shadow-lg backdrop-blur-sm animate-slide-in {typeColor(alert.signal_type)}">
				<div class="flex items-start gap-3">
					<span class="text-[54px] leading-none">{typeIcon(alert.signal_type)}</span>
					<div class="flex-1 min-w-0">
						<p class="text-[30px] font-bold text-white/70 uppercase tracking-wide">{typeLabel(alert.signal_type)}</p>
						<p class="text-[42px] font-medium text-white leading-tight">{alert.message}</p>
						<p class="text-[30px] text-white/50 mt-1">{alert.distance}m away</p>
					</div>
					<button onclick={() => dismiss(alert)}
						class="text-white/50 hover:text-white text-[36px] leading-none p-1">
						✕
					</button>
				</div>
			</div>
		{/each}

		<!-- Persistent moving actor geofence alerts (show while inside, disappear on exit) -->
		{#each $activeActorGeofenceAlerts as entry (entry.actor.actor_id)}
			<div class="rounded-lg border-l-[6px] px-5 py-3 shadow-lg backdrop-blur-sm animate-slide-in bg-red-600/90 border-red-400">
				<div class="flex items-start gap-3">
					<span class="text-[54px] leading-none">🚫</span>
					<div class="flex-1 min-w-0">
						<p class="text-[30px] font-bold text-white/70 uppercase tracking-wide">MOVING GEOFENCE</p>
						<p class="text-[42px] font-medium text-white leading-tight">{entry.actor.message || entry.actor.name}</p>
						<p class="text-[30px] text-white/50 mt-1">{entry.distance}m away</p>
					</div>
				</div>
			</div>
		{/each}

		<!-- Persistent geofence alerts (show while inside, disappear on exit) -->
		{#each $activeZoneAlerts as entry (entry.zone.id)}
			<div class="rounded-lg border-l-[6px] px-5 py-3 shadow-lg backdrop-blur-sm animate-slide-in {zoneColor(entry.zone)}">
				<div class="flex items-start gap-3">
					<span class="text-[54px] leading-none">{zoneIcon(entry.zone)}</span>
					<div class="flex-1 min-w-0">
						<p class="text-[30px] font-bold text-white/70 uppercase tracking-wide">{zoneLabel(entry.zone)}</p>
						<p class="text-[42px] font-medium text-white leading-tight">{entry.zone.message || entry.zone.name}</p>
					</div>
				</div>
			</div>
		{/each}
	</div>
{/if}

<style>
	@keyframes slide-in {
		from { opacity: 0; transform: translateX(100px); }
		to { opacity: 1; transform: translateX(0); }
	}
	:global(.animate-slide-in) {
		animation: slide-in 0.3s ease-out;
	}
</style>
