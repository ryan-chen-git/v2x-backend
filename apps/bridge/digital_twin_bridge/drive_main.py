"""
Unified entry point for the Digital Twin Bridge.

Combines the drive server (WebSocket vehicle control + MJPEG streaming)
with V2X observation (object spawning, state publishing, map export).

Run with:  python -m digital_twin_bridge.drive_main

Architecture:

  ┌─────────────────────────────────────────────┐
  │  Unified Server (asyncio)                   │
  │                                             │
  │  CARLA tick loop ─── 20 Hz physics          │
  │  WebSocket server ── per-client drive sess  │
  │  V2X snapshot ────── props spawned at boot  │
  │  State publisher ─── state.json → S3        │
  │  Map exporter ────── road network → S3      │
  │  Actor audit ─────── orphan cleanup / 60s   │
  └─────────────────────────────────────────────┘
"""

import asyncio
import logging
import os
import sys
import time

import requests
import websockets

from digital_twin_bridge.config import Config
from digital_twin_bridge.carla_connection import CarlaConnection
from digital_twin_bridge.drive_server import serve_drive, _active_sessions
from digital_twin_bridge.trajectory_player import TrajectoryPlayer
from digital_twin_bridge.openscenario_runner import OpenScenarioRunner

logger = logging.getLogger(__name__)


# ── V2X snapshot ────────────────────────────────────────────────────


def fetch_v2x_snapshot(config: Config) -> list[dict]:
    """Fetch current V2X detections from the read API (one-shot).

    The V2X data is treated as static — fetched once at startup and
    spawned as CARLA props. No continuous polling.
    """
    try:
        resp = requests.get(
            config.V2X_API_URL,
            params={"limit": config.V2X_LIMIT},
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        logger.info("Fetched %d V2X detections", len(items))
        return items
    except Exception as e:
        logger.warning("Failed to fetch V2X detections: %s", e)
        return []


# ── State publisher ─────────────────────────────────────────────────


async def state_publisher(config, registry, health, uplink, interval=5.0):
    """Periodically publish state.json to S3 so the dashboard stays live."""
    loop = asyncio.get_running_loop()

    while True:
        try:
            status = health.get_status()
            state_objects = []
            for obj in registry.get_all():
                state_objects.append({
                    "object_id": obj.object_id,
                    "object_type": obj.object_type,
                    "lat": obj.lat,
                    "lon": obj.lon,
                    "confidence": obj.confidence,
                    "street_name": obj.street_name,
                    "timestamp_utc": obj.timestamp_utc,
                    "snapshot_url": getattr(obj, "snapshot_url", None),
                    "snapshot_timestamp": getattr(obj, "snapshot_timestamp", None),
                    "last_updated": int(time.time() * 1000),
                })
            bridge_status = {
                "status": "connected",
                "carla_fps": status.get("effective_fps", 0),
                "objects_tracked": registry.count,
                "cameras_active": 0,
                "last_heartbeat": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
            }
            await loop.run_in_executor(
                None, uplink.publish_state, state_objects, bridge_status
            )
        except Exception as e:
            logger.debug("State publish failed: %s", e)

        await asyncio.sleep(interval)


# ── API fetcher (for per-session scene reconstruction) ──────────────


def make_api_fetcher(config: Config):
    """Create an API fetcher for SceneReconstructor (per-session use)."""
    base_url = config.V2X_API_URL.rsplit("/detections/", 1)[0]

    def fetch(start: str, end: str, limit: int = 500) -> dict:
        url = f"{base_url}/detections/range"
        params = {"start": start, "end": end, "limit": limit}
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    return fetch


# ── Main ────────────────────────────────────────────────────────────


async def main():
    config = Config.from_env()
    config.setup_logging()

    if "--dry-run" in sys.argv:
        logger.info("Unified server dry-run OK")
        return

    # ── Connect to CARLA (CarlaConnection handles sync mode + restore) ──
    conn = CarlaConnection(config)
    conn.connect()

    world = conn.world
    carla_map = conn.carla_map

    # Clean up leftover actors from previous sessions. `static.prop.*` includes
    # V2X-detected cones/warnings from the previous boot plus any user-placed
    # props left by spawn_object; without wiping them, each restart stacks a
    # fresh copy on top of the old.
    for actor in world.get_actors().filter("vehicle.*"):
        logger.info("Cleaning up leftover vehicle: %s (id=%d)", actor.type_id, actor.id)
        actor.destroy()
    for actor in world.get_actors().filter("sensor.*"):
        logger.info("Cleaning up leftover sensor: %s (id=%d)", actor.type_id, actor.id)
        actor.destroy()
    leftover_props = world.get_actors().filter("static.prop.*")
    if leftover_props:
        logger.info("Cleaning up %d leftover static prop(s) from previous boot", len(leftover_props))
        for actor in leftover_props:
            try:
                actor.destroy()
            except Exception as e:
                logger.debug("Prop destroy failed (id=%d): %s", actor.id, e)

    # ── V2X props: boot spawn disabled ──
    # PropSpawner is intentionally not invoked here. Production V2X
    # detections were landing traffic cones on the road in the firetruck
    # scenarios' paths. To re-enable, restore the fetch_v2x_snapshot() +
    # PropSpawner(world, carla_map).sync(registry) block from git history;
    # the spawner module (prop_spawner.py) is still available unchanged.
    shared_prop_pool: dict[str, int] = {}
    registry = None

    # ── Map data: export road network to S3 ──
    uplink = None
    try:
        from digital_twin_bridge.map_data import MapDataExporter
        from digital_twin_bridge.uplink import Uplink

        uplink = Uplink(config)
        map_exporter = MapDataExporter(conn)
        snapshot_dir = config.LOCAL_SNAPSHOT_DIR
        os.makedirs(snapshot_dir, exist_ok=True)
        map_data = map_exporter.export_to_json(
            os.path.join(snapshot_dir, "map_data.json")
        )
        uplink.upload_map_data(map_data)
        logger.info("Map data exported and uploaded to S3")
    except Exception:
        logger.warning("Map data export failed (non-fatal)", exc_info=True)

    # ── Health monitor ──
    from digital_twin_bridge.health import HealthMonitor

    health = HealthMonitor()

    # ── Trajectory player (singleton: one playback at a time, shared world) ──
    trajectory_player = TrajectoryPlayer(world, carla_map)

    # ── OpenSCENARIO runner (singleton: one .xosc at a time across all sessions) ──
    openscenario_runner = OpenScenarioRunner(
        scenario_runner_path=config.SCENARIO_RUNNER_PATH,
        carla_host=config.CARLA_HOST,
        carla_port=config.CARLA_PORT,
        python_executable=config.SCENARIO_RUNNER_PYTHON or None,
        pythonpath_prefix=config.SCENARIO_RUNNER_PYTHONPATH,
        world=world,
    )

    # ── Drive server setup ──
    api_fetcher = make_api_fetcher(config)

    async def handler(websocket):
        await serve_drive(
            websocket, world, carla_map, api_fetcher,
            shared_prop_pool, trajectory_player, openscenario_runner,
            eva_warning_distance_m=config.EVA_WARNING_DISTANCE_M,
        )

    async def tick_loop():
        """Advance CARLA physics at 20 Hz.

        In sync mode the world does not tick on its own; we drive it from
        here. While ScenarioRunner is running it owns the tick (launched
        with --sync, locally patched to pace at 20 Hz wall-time so user
        controls feel normal). The bridge yields its tick during that
        window. SR turns sync mode off on exit, so we re-arm it before
        resuming. After each bridge tick we step the trajectory player so
        its controller stays in lockstep with sim time.
        """
        loop = asyncio.get_running_loop()
        target_dt = 0.05
        was_running = False
        while True:
            if openscenario_runner.is_running:
                was_running = True
                await asyncio.sleep(target_dt)
                continue
            if was_running:
                try:
                    settings = world.get_settings()
                    if not settings.synchronous_mode:
                        settings.synchronous_mode = True
                        settings.fixed_delta_seconds = target_dt
                        world.apply_settings(settings)
                        logger.info("Re-applied sync mode after scenario finished")
                except Exception as e:
                    logger.warning("Failed to restore sync mode after scenario: %s", e)
                was_running = False

            start = loop.time()
            try:
                await loop.run_in_executor(None, world.tick)
            except Exception as e:
                logger.warning("world.tick() failed: %s", e)
                await asyncio.sleep(target_dt)
                continue
            try:
                trajectory_player.tick()
            except Exception as e:
                logger.warning("trajectory_player.tick() failed: %s", e)
            elapsed = loop.time() - start
            await asyncio.sleep(max(0.0, target_dt - elapsed))

    async def periodic_actor_audit():
        """Every 60s, check for orphaned actors when no sessions are active."""
        while True:
            await asyncio.sleep(60)
            if _active_sessions:
                continue
            try:
                from digital_twin_bridge.drive_server import _traffic_actor_ids
                vehicles = [v for v in world.get_actors().filter("vehicle.*")
                            if v.id not in _traffic_actor_ids
                            and v.attributes.get("role_name") != "trajectory"]
                sensors = world.get_actors().filter("sensor.*")
                # Boot-time V2X props are intentional; only sweep if we find a
                # runaway count (>2x the tracked snapshot), which indicates
                # stacked spawns from prior sessions.
                props = list(world.get_actors().filter("static.prop.*"))
                baseline = len(registry.get_all()) if registry else 0
                props_to_clean = props if baseline and len(props) > baseline * 2 else []
                orphaned = len(vehicles) + len(sensors) + len(props_to_clean)
                if orphaned > 0:
                    logger.warning(
                        "Actor audit: %d orphaned actors (vehicles=%d, sensors=%d, props=%d). Cleaning up.",
                        orphaned, len(vehicles), len(sensors), len(props_to_clean),
                    )
                    for a in sensors:
                        try:
                            a.stop()
                        except Exception:
                            pass
                        try:
                            a.destroy()
                        except Exception:
                            pass
                    for a in vehicles:
                        try:
                            a.destroy()
                        except Exception:
                            pass
                    for a in props_to_clean:
                        try:
                            a.destroy()
                        except Exception:
                            pass
            except Exception as e:
                logger.debug("Actor audit error: %s", e)

    port = config.WS_PORT

    logger.info("=" * 60)
    logger.info("  Digital Twin -- Unified Server")
    logger.info("=" * 60)
    logger.info("  CARLA       : %s:%d (sync mode, 20 Hz)", config.CARLA_HOST, config.CARLA_PORT)
    logger.info("  Drive WS    : ws://0.0.0.0:%d", port)
    logger.info("  V2X objects : %d tracked", len(shared_prop_pool))
    logger.info("  State pub   : %s", "active" if uplink else "disabled (no AWS)")
    logger.info("=" * 60)

    tick_task = None
    audit_task = None
    publish_task = None

    try:
        tick_task = asyncio.create_task(tick_loop())
        audit_task = asyncio.create_task(periodic_actor_audit())

        # Publish state.json to S3 so the web dashboard stays live
        if uplink is not None and registry is not None:
            publish_task = asyncio.create_task(
                state_publisher(config, registry, health, uplink)
            )

        async with websockets.serve(
            handler,
            "0.0.0.0",
            port,
            ping_interval=5,
            ping_timeout=15,
            close_timeout=5,
        ):
            logger.info("Unified server ready. Waiting for connections...")
            await asyncio.Future()
    finally:
        for task in [tick_task, audit_task, publish_task]:
            if task is not None:
                try:
                    task.cancel()
                except Exception:
                    pass

        # Stop any active trajectory playback (server-owned, not session-owned).
        try:
            trajectory_player.stop()
        except Exception as e:
            logger.debug("Trajectory stop on shutdown failed: %s", e)

        # Stop any running OpenSCENARIO subprocess.
        try:
            openscenario_runner.stop()
        except Exception as e:
            logger.debug("OpenSCENARIO stop on shutdown failed: %s", e)

        # Destroy shared V2X props (owned by the server, not any session).
        if shared_prop_pool:
            destroyed = 0
            for actor_id in list(shared_prop_pool.values()):
                try:
                    actor = world.get_actor(actor_id)
                    if actor is not None:
                        actor.destroy()
                        destroyed += 1
                except Exception as e:
                    logger.debug("Shared prop destroy failed (id=%d): %s", actor_id, e)
            shared_prop_pool.clear()
            logger.info("Destroyed %d shared V2X props at shutdown", destroyed)

        conn.disconnect()
        logger.info("Unified server stopped")


if __name__ == "__main__":
    asyncio.run(main())
