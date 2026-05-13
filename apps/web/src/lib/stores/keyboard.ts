/**
 * Keyboard Input Store — WASD / arrow key driving controls.
 *
 * W / ↑  = throttle (forward)
 * S / ↓  = throttle (reverse)
 * A / ←  = steer left
 * D / →  = steer right
 * Space  = brake / handbrake
 *
 * This follows standard driving game convention:
 * W = go forward, S = go backward, Space = brake.
 */

import { writable, get } from 'svelte/store';
import type { NormalizedInput } from './gamepad';
import { telemetry } from './driveSocket';

// Speed-sensitive steering: at low speed the wheels can over-rotate the car
// in CARLA's vehicle model (almost no lateral grip force to oppose them), so
// we cap the maximum steering authority until the car has some forward
// momentum. Below LOW_SPEED_FLOOR_KMH the cap is LOW_SPEED_STEER_CAP, ramping
// linearly to HIGH_SPEED_STEER_CAP by FULL_STEER_SPEED_KMH.
//
// HIGH_SPEED_STEER_CAP matches CARLA's manual_control.py (line 616):
//   self._steer_cache = min(0.7, max(-0.7, self._steer_cache))
// — capping at 0.7 instead of 1.0 keeps lateral acceleration inside the
// tire's grip envelope at speed, which is what makes that example feel
// noticeably more planted than going to full lock.
const LOW_SPEED_STEER_CAP = 0.4;
const HIGH_SPEED_STEER_CAP = 0.7;
const LOW_SPEED_FLOOR_KMH = 0;
const FULL_STEER_SPEED_KMH = 25;

export const keyboardActive = writable<boolean>(false);

const keys: Record<string, boolean> = {};

let currentSteer = 0;
const STEER_SPEED = 3.0;
const STEER_RETURN_SPEED = 5.0;
// CARLA manual_control.py adds 0.1/frame at ~60Hz → ~6.0/sec.
const THROTTLE_RAMP = 6.0;

let currentForwardThrottle = 0;
let currentReverseThrottle = 0;
let currentBrake = 0;
// Sticky reverse gear: set true while S is held, only cleared by W.
// Releasing S alone keeps the gear in R so the car coasts backward; otherwise
// CARLA's auto-trans flips to D and engine-brakes us to a halt mid-roll.
let gearReverse = false;
let lastFrameTime = 0;
let animFrameId: number | null = null;

export const keyboardInput = writable<NormalizedInput>({
	steer: 0,
	throttle: 0,
	brake: 0,
	reverse: false
});

function onKeyDown(e: KeyboardEvent) {
	if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;

	const key = e.key.toLowerCase();
	if (['w', 'a', 's', 'd', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright', ' '].includes(key)) {
		e.preventDefault();
		keys[key] = true;
		keyboardActive.set(true);
	}
}

function onKeyUp(e: KeyboardEvent) {
	const key = e.key.toLowerCase();
	keys[key] = false;
}

function update() {
	const now = performance.now();
	const dt = lastFrameTime > 0 ? Math.min((now - lastFrameTime) / 1000, 0.05) : 0.016;
	lastFrameTime = now;

	// Steering
	const wantLeft = keys['a'] || keys['arrowleft'];
	const wantRight = keys['d'] || keys['arrowright'];
	let steerTarget = 0;
	if (wantLeft && !wantRight) steerTarget = -1;
	else if (wantRight && !wantLeft) steerTarget = 1;

	if (steerTarget !== 0) {
		const diff = steerTarget - currentSteer;
		currentSteer += Math.sign(diff) * Math.min(Math.abs(diff), STEER_SPEED * dt);
	} else {
		if (Math.abs(currentSteer) < STEER_RETURN_SPEED * dt) {
			currentSteer = 0;
		} else {
			currentSteer -= Math.sign(currentSteer) * STEER_RETURN_SPEED * dt;
		}
	}
	currentSteer = Math.max(-1, Math.min(1, currentSteer));

	// Speed-sensitive steering cap — pulled from live telemetry.
	const speedKmh = Math.abs(get(telemetry).speed ?? 0);
	const t = Math.max(
		0,
		Math.min(1, (speedKmh - LOW_SPEED_FLOOR_KMH) / (FULL_STEER_SPEED_KMH - LOW_SPEED_FLOOR_KMH))
	);
	const steerCap = LOW_SPEED_STEER_CAP + (HIGH_SPEED_STEER_CAP - LOW_SPEED_STEER_CAP) * t;
	const cookedSteer = Math.max(-steerCap, Math.min(steerCap, currentSteer));

	// Throttle — CARLA-style: ramp up while held, instant 0 on release.
	// W enters/keeps drive gear; S enters/keeps reverse gear. Releasing the
	// throttle key drops throttle to 0 but the gear sticks so the car coasts.
	const wantForward = keys['w'] || keys['arrowup'];
	const wantReverse = keys['s'] || keys['arrowdown'];

	if (wantForward && !wantReverse) {
		currentForwardThrottle = Math.min(1, currentForwardThrottle + THROTTLE_RAMP * dt);
		currentReverseThrottle = 0;
		gearReverse = false;
	} else if (wantReverse && !wantForward) {
		currentReverseThrottle = Math.min(1, currentReverseThrottle + THROTTLE_RAMP * dt);
		currentForwardThrottle = 0;
		gearReverse = true;
	} else {
		currentForwardThrottle = 0;
		currentReverseThrottle = 0;
	}

	// Brake (Space)
	const wantBrake = keys[' '];
	if (wantBrake) {
		currentBrake = Math.min(1, currentBrake + 4.0 * dt);
	} else {
		currentBrake = Math.max(0, currentBrake - 4.0 * 2 * dt);
	}

	const throttle = gearReverse ? currentReverseThrottle : currentForwardThrottle;

	keyboardInput.set({
		steer: cookedSteer,
		throttle,
		brake: currentBrake,
		reverse: gearReverse
	});

	animFrameId = requestAnimationFrame(update);
}

export function startKeyboardInput(): void {
	if (animFrameId !== null) return;
	lastFrameTime = 0;
	currentSteer = 0;
	currentForwardThrottle = 0;
	currentReverseThrottle = 0;
	currentBrake = 0;
	gearReverse = false;
	window.addEventListener('keydown', onKeyDown);
	window.addEventListener('keyup', onKeyUp);
	animFrameId = requestAnimationFrame(update);
}

export function stopKeyboardInput(): void {
	if (animFrameId !== null) {
		cancelAnimationFrame(animFrameId);
		animFrameId = null;
	}
	window.removeEventListener('keydown', onKeyDown);
	window.removeEventListener('keyup', onKeyUp);
	Object.keys(keys).forEach((k) => (keys[k] = false));
	currentSteer = 0;
	currentForwardThrottle = 0;
	currentReverseThrottle = 0;
	currentBrake = 0;
	gearReverse = false;
	keyboardInput.set({ steer: 0, throttle: 0, brake: 0, reverse: false });
	keyboardActive.set(false);
}
