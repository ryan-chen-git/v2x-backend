<script lang="ts">
	import { onMount } from 'svelte';
	import {
		xoscScenarioList,
		xoscRunnerStatus,
		xoscEventLog,
		xoscLastResult,
		requestXoscScenarios,
		startXoscScenario,
		stopXoscScenario,
	} from '$lib/stores/driveSocket';

	interface Props {
		onclose: () => void;
	}

	let { onclose }: Props = $props();

	let selectedFile = $state('');
	let logEl = $state<HTMLDivElement | null>(null);

	let scenarios = $derived($xoscScenarioList);
	let status = $derived($xoscRunnerStatus);
	let events = $derived($xoscEventLog);
	let result = $derived($xoscLastResult);

	let configured = $derived(status.scenario_runner_configured);
	let running = $derived(status.running);

	onMount(() => {
		requestXoscScenarios();
	});

	$effect(() => {
		// Auto-scroll the log to the bottom as new events arrive.
		if (events.length > 0 && logEl) {
			logEl.scrollTop = logEl.scrollHeight;
		}
	});

	function handleStart() {
		if (!selectedFile || running) return;
		startXoscScenario(selectedFile);
	}

	function handleStop() {
		if (!running) return;
		stopXoscScenario();
	}

	function handleRefresh() {
		requestXoscScenarios();
	}

	function fmtSize(bytes: number): string {
		if (bytes < 1024) return `${bytes} B`;
		if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
		return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
	}

	function fmtTime(ts: number): string {
		const d = new Date(ts * 1000);
		return d.toLocaleTimeString();
	}
</script>

<div
	class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
	role="dialog"
	aria-modal="true"
	tabindex="-1"
	onkeydown={(e) => { if (e.key === 'Escape') onclose(); }}
>
	<div class="relative w-[760px] max-w-[95vw] max-h-[90vh] flex flex-col rounded-2xl border border-gray-800/60 bg-gray-950/95 shadow-2xl shadow-black/60 overflow-hidden">
		<!-- Header -->
		<div class="flex items-center justify-between px-6 py-4 border-b border-gray-800/60">
			<div>
				<h2 class="font-display text-lg font-bold text-white tracking-widest uppercase">OpenSCENARIO</h2>
				<p class="mt-0.5 text-[11px] font-body text-gray-500 tracking-wider uppercase">
					Run an .xosc file alongside your drive session
				</p>
			</div>
			<button
				onclick={onclose}
				class="w-8 h-8 rounded-lg flex items-center justify-center text-gray-500 hover:text-white hover:bg-gray-800/60 transition-colors cursor-pointer"
				aria-label="Close"
			>
				<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>

		<!-- Body -->
		<div class="flex-1 overflow-y-auto px-6 py-5 space-y-5">
			{#if !configured}
				<div class="rounded-xl border border-yellow-500/40 bg-yellow-500/10 p-4 text-sm text-yellow-200/90 font-body">
					<p class="font-semibold tracking-wide uppercase text-xs text-yellow-300 mb-1">ScenarioRunner not configured</p>
					<p class="text-xs leading-relaxed">
						Set <code class="px-1 py-0.5 bg-yellow-500/20 rounded text-yellow-100">DTB_SCENARIO_RUNNER_PATH</code>
						to the cloned
						<a href="https://github.com/carla-simulator/scenario_runner" target="_blank" rel="noopener" class="underline">scenario_runner</a>
						directory and restart the bridge.
					</p>
				</div>
			{/if}

			<!-- Status row -->
			<div class="flex items-center justify-between gap-3">
				<div class="flex items-center gap-2 text-xs font-body tracking-wider uppercase">
					<span class="w-2 h-2 rounded-full {running ? 'bg-green-400 animate-pulse' : 'bg-gray-600'}"></span>
					<span class="text-gray-400">
						{running ? `Running: ${status.file ?? ''}` : 'Idle'}
					</span>
				</div>
				<button
					onclick={handleRefresh}
					class="text-[10px] font-body tracking-widest uppercase text-gray-500 hover:text-white px-2 py-1 rounded-md hover:bg-gray-800/60 transition-colors cursor-pointer"
				>
					Refresh
				</button>
			</div>

			<!-- Picker -->
			<div>
				<label for="xosc-select" class="block text-[10px] font-body text-gray-600 tracking-widest uppercase mb-1.5">
					Scenario file
				</label>
				<div class="flex gap-2">
					<div class="relative flex-1">
						<select
							id="xosc-select"
							bind:value={selectedFile}
							disabled={running}
							class="w-full px-4 py-2.5 bg-gray-800/50 border border-gray-800 rounded-xl text-sm font-body text-white focus:outline-none focus:border-accent/50 appearance-none cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200"
						>
							<option value="">— Select a .xosc file —</option>
							{#each scenarios as s}
								<option value={s.file}>{s.name} ({fmtSize(s.size_bytes)})</option>
							{/each}
						</select>
						<svg
							class="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500 pointer-events-none"
							fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
						>
							<path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
						</svg>
					</div>

					{#if running}
						<button
							onclick={handleStop}
							class="px-5 py-2.5 bg-red-600 hover:bg-red-500 rounded-xl text-sm font-display font-bold tracking-widest uppercase text-white transition-all duration-200 cursor-pointer"
						>
							Stop
						</button>
					{:else}
						<button
							onclick={handleStart}
							disabled={!selectedFile || !configured}
							class="px-5 py-2.5 bg-accent hover:bg-red-500 disabled:bg-gray-800 disabled:text-gray-600 disabled:cursor-not-allowed rounded-xl text-sm font-display font-bold tracking-widest uppercase text-white transition-all duration-200 cursor-pointer"
						>
							Start
						</button>
					{/if}
				</div>
				{#if scenarios.length === 0}
					<p class="mt-2 text-[11px] font-body text-gray-600">
						No .xosc files found. Drop them into <code class="text-gray-500">apps/bridge/scenarios/</code>.
					</p>
				{/if}
			</div>

			<!-- Last result -->
			{#if result}
				<div
					class="rounded-xl border p-3 text-xs font-body
					{result.verdict === 'SUCCESS'
						? 'border-green-500/40 bg-green-500/10 text-green-200'
						: 'border-red-500/40 bg-red-500/10 text-red-200'}"
				>
					<span class="font-semibold tracking-widest uppercase">{result.verdict}</span>
					<span class="text-gray-400"> — {result.file ?? ''} ({result.duration_sec}s, exit={result.exit_code ?? '?'})</span>
				</div>
			{/if}

			<!-- Event log -->
			<div>
				<div class="flex items-center justify-between mb-1.5">
					<span class="block text-[10px] font-body text-gray-600 tracking-widest uppercase">
						Event log
					</span>
					<span class="text-[10px] font-body text-gray-600">{events.length} line{events.length === 1 ? '' : 's'}</span>
				</div>
				<div
					bind:this={logEl}
					class="h-[280px] overflow-y-auto rounded-xl border border-gray-800/60 bg-black/60 p-3 font-mono text-[11px] text-gray-300 leading-relaxed"
				>
					{#if events.length === 0}
						<p class="text-gray-600 italic">Waiting for output…</p>
					{:else}
						{#each events as ev, i (i)}
							<div class="flex gap-2">
								<span class="text-gray-600 shrink-0">{fmtTime(ev.ts)}</span>
								<span class="break-all">{ev.line}</span>
							</div>
						{/each}
					{/if}
				</div>
			</div>
		</div>

		<!-- Footer -->
		<div class="px-6 py-3 border-t border-gray-800/60 text-[11px] font-body text-gray-600 leading-relaxed">
			ScenarioRunner connects to your CARLA on port 2000 as a passive client.
			The drive server keeps owning the ego vehicle (<code class="text-gray-500">role_name=ego_vehicle</code>);
			your wheel/keyboard input still drives it. NPCs and triggers come from the .xosc.
		</div>
	</div>
</div>
