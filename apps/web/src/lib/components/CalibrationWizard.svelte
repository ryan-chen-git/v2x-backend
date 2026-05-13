<script lang="ts">
	import { rawAxes, calibration, gamepadConnected, gamepadName, recalibrateRestValues, applyDefaultRests } from '$lib/stores/gamepad';
	import { DEFAULT_CALIBRATION } from '$lib/constants';
	import type { GamepadCalibration } from '$lib/types';

	interface Props {
		onComplete: () => void;
	}
	let { onComplete }: Props = $props();

	let step = $state(0); // 0=steer, 1=gas, 2=brake
	let detecting = $state(false);
	let detectedAxis = $state(-1);
	let maxDelta = $state(0);
	let baselineAxes = $state<number[]>([]);
	let result = $state<Partial<GamepadCalibration>>({});

	const steps = [
		{ label: 'Steering', instruction: 'Turn your wheel fully left and right', key: 'steerAxis' as const, icon: 'steer' },
		{ label: 'Gas Pedal', instruction: 'Press your gas pedal fully, then release', key: 'gasAxis' as const, icon: 'gas' },
		{ label: 'Brake Pedal', instruction: 'Press your brake pedal fully, then release', key: 'brakeAxis' as const, icon: 'brake' },
	];

	function startDetection() {
		detecting = true;
		detectedAxis = -1;
		maxDelta = 0;
		baselineAxes = [...$rawAxes];
	}

	// Watch raw axes during detection
	$effect(() => {
		if (!detecting || $rawAxes.length === 0 || baselineAxes.length === 0) return;

		let bestAxis = -1;
		let bestDelta = 0;

		for (let i = 0; i < $rawAxes.length; i++) {
			const delta = Math.abs($rawAxes[i] - baselineAxes[i]);
			if (delta > bestDelta) {
				bestDelta = delta;
				bestAxis = i;
			}
		}

		if (bestDelta > 0.3 && bestDelta > maxDelta) {
			maxDelta = bestDelta;
			detectedAxis = bestAxis;
		}
	});

	function confirmAxis() {
		if (detectedAxis < 0) return;

		const key = steps[step].key;
		result[key] = detectedAxis;

		detecting = false;
		step++;

		if (step >= steps.length) {
			calibration.update((c) => ({
				...c,
				steerAxis: result.steerAxis ?? c.steerAxis,
				gasAxis: result.gasAxis ?? c.gasAxis,
				brakeAxis: result.brakeAxis ?? c.brakeAxis,
			}));
			recalibrateRestValues();
			onComplete();
		}
	}

	// Abandon the current detection and let the user re-trigger it.
	function redoDetection() {
		detecting = false;
		detectedAxis = -1;
		maxDelta = 0;
	}

	// Step backward so a mis-calibrated axis can be redone.
	function goBack() {
		if (detecting) {
			detecting = false;
			detectedAxis = -1;
			maxDelta = 0;
			return;
		}
		if (step > 0) {
			step--;
			delete result[steps[step].key];
			detectedAxis = -1;
			maxDelta = 0;
		}
	}

	// Apply hardcoded DEFAULT_CALIBRATION (axes + G923 rest values). Skipping
	// detection prevents a held pedal from being captured as rest.
	function skipCalibration() {
		calibration.update((c) => ({
			...c,
			steerAxis: DEFAULT_CALIBRATION.steerAxis,
			gasAxis: DEFAULT_CALIBRATION.gasAxis,
			brakeAxis: DEFAULT_CALIBRATION.brakeAxis,
		}));
		applyDefaultRests();
		onComplete();
	}
</script>

<div class="fixed inset-0 bg-black/90 backdrop-blur-sm flex items-center justify-center z-50">
	<!-- Subtle background glow -->
	<div class="absolute inset-0 flex items-center justify-center pointer-events-none">
		<div class="w-[500px] h-[500px] rounded-full bg-accent/5 blur-[100px]"></div>
	</div>

	<div class="relative bg-gray-900/80 backdrop-blur-xl rounded-2xl p-8 max-w-lg w-full mx-4 border border-gray-800/60 shadow-2xl shadow-black/50">
		<!-- Header -->
		<div class="mb-6">
			<h2 class="font-display text-xl font-bold text-white tracking-widest uppercase">Calibration</h2>
			<div class="mt-1.5 w-10 h-0.5 bg-accent rounded-full"></div>
		</div>

		<!-- Step progress -->
		<div class="flex items-center gap-2 mb-8">
			{#each steps as s, i}
				<div class="flex items-center gap-2 {i < steps.length - 1 ? 'flex-1' : ''}">
					<div class="relative flex items-center justify-center w-8 h-8 rounded-full border-2 transition-all duration-300
						{i < step
							? 'bg-accent border-accent'
							: i === step
								? 'border-accent bg-accent/10'
								: 'border-gray-700 bg-gray-800/50'}">
						{#if i < step}
							<svg class="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3">
								<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
							</svg>
						{:else}
							<span class="text-xs font-body {i === step ? 'text-accent' : 'text-gray-600'}">{i + 1}</span>
						{/if}
					</div>
					{#if i < steps.length - 1}
						<div class="flex-1 h-px transition-all duration-300 {i < step ? 'bg-accent' : 'bg-gray-800'}"></div>
					{/if}
				</div>
			{/each}
		</div>

		{#if !$gamepadConnected}
			<!-- No wheel detected -->
			<div class="text-center py-10">
				<div class="w-16 h-16 mx-auto mb-4 rounded-full border-2 border-gray-700 flex items-center justify-center">
					<svg class="w-8 h-8 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
						<circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="3" /><path d="M12 3v6M12 15v6M3 12h6M15 12h6" />
					</svg>
				</div>
				<p class="font-body text-sm text-gray-400 tracking-wider">NO WHEEL DETECTED</p>
				<p class="mt-1 font-body text-xs text-gray-600 tracking-wide">Connect your wheel and press a button</p>
			</div>
		{:else}
			<!-- Current step info -->
			<div class="mb-6">
				<p class="text-[10px] font-body text-gray-600 tracking-widest uppercase mb-1">{steps[step].label}</p>
				<p class="text-lg font-body text-white">{steps[step].instruction}</p>
			</div>

			{#if !detecting}
				<div class="flex gap-2">
					{#if step > 0}
						<button onclick={goBack}
							class="px-4 py-3.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl text-sm font-display font-bold tracking-widest uppercase text-gray-300 transition-all duration-200 cursor-pointer">
							Back
						</button>
					{/if}
					<button onclick={startDetection}
						class="flex-1 py-3.5 bg-accent hover:bg-red-500 rounded-xl text-sm font-display font-bold tracking-widest uppercase text-white transition-all duration-200 shadow-[0_0_20px_rgba(220,38,38,0.3)] hover:shadow-[0_0_30px_rgba(220,38,38,0.5)] cursor-pointer">
						Start Detection
					</button>
				</div>
			{:else}
				<div class="mb-4">
					<!-- Axis grid — visual bars instead of raw numbers -->
					<div class="grid grid-cols-4 gap-1.5 mb-5">
						{#each $rawAxes as axis, i}
							<div class="relative flex flex-col items-center p-2.5 rounded-xl transition-all duration-200
								{detectedAxis === i
									? 'bg-accent/10 border border-accent/50 shadow-[0_0_10px_rgba(220,38,38,0.15)]'
									: 'bg-gray-800/50 border border-gray-800'}">
								<span class="text-[9px] font-body text-gray-600 tracking-wider mb-1.5">AX{i}</span>
								<!-- Mini bar visualization -->
								<div class="w-full h-8 bg-gray-900 rounded-md relative overflow-hidden">
									<div class="absolute bottom-0 left-0 right-0 transition-all duration-75 rounded-md
										{detectedAxis === i ? 'bg-accent/70' : 'bg-gray-600/50'}"
										style="height: {Math.abs(axis) * 100}%"></div>
								</div>
								<span class="mt-1 text-[10px] font-mono {detectedAxis === i ? 'text-accent' : 'text-gray-500'}">
									{axis.toFixed(2)}
								</span>
							</div>
						{/each}
					</div>

					{#if detectedAxis >= 0}
						<div class="flex items-center gap-2 mb-3">
							<div class="w-2 h-2 bg-green-500 rounded-full shadow-[0_0_6px_rgba(34,197,94,0.5)]"></div>
							<span class="text-sm font-body text-green-400/80 tracking-wider">DETECTED: AXIS {detectedAxis}</span>
						</div>
						<div class="flex gap-2">
							{#if step > 0}
								<button onclick={goBack}
									class="px-4 py-3.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl text-sm font-display font-bold tracking-widest uppercase text-gray-300 transition-all duration-200 cursor-pointer">
									Back
								</button>
							{/if}
							<button onclick={redoDetection}
								class="px-4 py-3.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl text-sm font-display font-bold tracking-widest uppercase text-gray-300 transition-all duration-200 cursor-pointer">
								Redo
							</button>
							<button onclick={confirmAxis}
								class="flex-1 py-3.5 bg-green-600 hover:bg-green-500 rounded-xl text-sm font-display font-bold tracking-widest uppercase text-white transition-all duration-200 shadow-[0_0_15px_rgba(34,197,94,0.2)] hover:shadow-[0_0_25px_rgba(34,197,94,0.4)] cursor-pointer">
								Confirm Axis {detectedAxis}
							</button>
						</div>
					{:else}
						<div class="flex items-center justify-center gap-2 py-3">
							<div class="w-2 h-2 bg-yellow-500 rounded-full animate-pulse"></div>
							<span class="text-sm font-body text-yellow-500/80 tracking-wider animate-pulse">MOVE THE CONTROL...</span>
						</div>
						<div class="flex gap-2">
							{#if step > 0}
								<button onclick={goBack}
									class="flex-1 py-2.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl text-xs font-display font-bold tracking-widest uppercase text-gray-300 transition-all duration-200 cursor-pointer">
									Back
								</button>
							{/if}
							<button onclick={redoDetection}
								class="flex-1 py-2.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl text-xs font-display font-bold tracking-widest uppercase text-gray-300 transition-all duration-200 cursor-pointer">
								Cancel
							</button>
						</div>
					{/if}
				</div>
			{/if}
		{/if}

		<!-- Skip button -->
		<button onclick={skipCalibration}
			class="mt-5 w-full py-2.5 text-xs font-body text-gray-600 hover:text-gray-400 tracking-widest uppercase transition-colors cursor-pointer">
			Skip (use defaults)
		</button>
	</div>
</div>
