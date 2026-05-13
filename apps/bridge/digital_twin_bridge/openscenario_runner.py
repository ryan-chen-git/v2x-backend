"""
OpenSCENARIO Runner — manages a CARLA ScenarioRunner subprocess.

Loads `.xosc` files from `apps/bridge/scenarios/`, spawns the third-party
`scenario_runner.py`, and broadcasts each stdout line to subscribed
WebSocket sessions as `xosc_event` messages.

ScenarioRunner finds the bridge's already-spawned ego by
`role_name="ego_vehicle"` so the player keeps driving while SR spawns NPCs
and evaluates triggers. The `.xosc` must mark that entity as
`external_control` (see scenarios/README.md).
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from typing import Optional

logger = logging.getLogger(__name__)


BRIDGE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
XOSC_DIR = os.path.join(BRIDGE_ROOT, "scenarios")


def _ensure_xosc_dir() -> None:
    os.makedirs(XOSC_DIR, exist_ok=True)


def list_xosc() -> list[dict]:
    """List all `.xosc` files available in the scenarios directory."""
    _ensure_xosc_dir()
    out: list[dict] = []
    if not os.path.isdir(XOSC_DIR):
        return out
    for fname in sorted(os.listdir(XOSC_DIR)):
        if not fname.endswith(".xosc"):
            continue
        fpath = os.path.join(XOSC_DIR, fname)
        try:
            size = os.path.getsize(fpath)
        except OSError:
            size = 0
        out.append({
            "file": fname,
            "name": fname[:-5].replace("_", " "),
            "size_bytes": size,
        })
    return out


def _resolve_xosc_path(filename: str) -> str:
    """Resolve a `.xosc` filename to an absolute path inside `scenarios/`.

    Rejects paths that would escape the scenarios directory.
    """
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        raise ValueError(f"Invalid scenario filename: {filename!r}")
    if not filename.endswith(".xosc"):
        raise ValueError(f"Expected a .xosc file, got: {filename!r}")
    fpath = os.path.join(XOSC_DIR, filename)
    if not os.path.isfile(fpath):
        raise FileNotFoundError(f"Scenario file not found: {filename}")
    return fpath


class OpenScenarioRunner:
    """Singleton wrapper around a ScenarioRunner subprocess.

    One scenario runs at a time. Every WS connection subscribes to the same
    event stream so each connected browser sees the same trigger events.
    """

    def __init__(
        self,
        scenario_runner_path: str,
        carla_host: str = "localhost",
        carla_port: int = 2000,
        python_executable: Optional[str] = None,
        pythonpath_prefix: str = "",
        world=None,
    ) -> None:
        self._sr_path = scenario_runner_path
        self._carla_host = carla_host
        self._carla_port = carla_port
        # Override `python_executable` when scenario_runner's deps live in a
        # different venv from the bridge (e.g. a Py3.10 venv when the bridge
        # runs in Py3.12 and SR's pinned numpy won't build there).
        self._python = python_executable or sys.executable
        # Typically points at the CARLA PythonAPI `carla/` directory so SR
        # can `import agents`.
        self._pythonpath_prefix = pythonpath_prefix
        self._world = world

        self._proc: Optional[subprocess.Popen] = None
        self._current_file: Optional[str] = None
        self._started_at: Optional[float] = None
        self._exit_code: Optional[int] = None
        # Path to a per-launch rewritten .xosc (with the calling session's ego
        # role injected). Cleared on scenario exit. None when SR is using the
        # original file (default ego_role).
        self._temp_xosc_path: Optional[str] = None

        self._subscribers: list[asyncio.Queue] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    # ── Subscribe / unsubscribe ──

    def subscribe(self) -> asyncio.Queue:
        """Register a subscriber that receives every broadcast event.

        Must be called from inside an asyncio loop.
        """
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._loop = loop
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def _broadcast(self, event: dict) -> None:
        with self._lock:
            loop = self._loop
            subs = list(self._subscribers)
        if loop is None or not subs:
            return
        for q in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:
                # Loop is closed; subscriber will get cleaned up on its own.
                pass

    # ── Lifecycle ──

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> dict:
        return {
            "running": self.is_running,
            "file": self._current_file,
            "started_at": self._started_at,
            "exit_code": self._exit_code,
            "scenario_runner_configured": bool(self._sr_path) and os.path.isdir(self._sr_path),
        }

    def start(self, filename: str, ego_role: str = "ego_vehicle") -> dict:
        """Boot ScenarioRunner with the given .xosc file.

        ``ego_role`` is the role_name of the ego vehicle SR should attach to.
        Defaults to ``"ego_vehicle"`` (the original .xosc string) for backward
        compat. When a session passes its own per-session role
        (e.g. ``"ego_vehicle_7f8a"``), the .xosc is rewritten into a temp file
        with every reference to ``"ego_vehicle"`` updated to the new role —
        SR then attaches to that exact ego, ignoring other drivers' egos in
        a multi-session world.

        Raises if a scenario is already running, the file is missing, or
        the ScenarioRunner path isn't configured / installed.
        """
        if self.is_running:
            raise RuntimeError("A scenario is already running — stop it first")

        if not self._sr_path or not os.path.isdir(self._sr_path):
            raise RuntimeError(
                f"ScenarioRunner not configured. Set DTB_SCENARIO_RUNNER_PATH to the "
                f"cloned scenario_runner directory (current value: {self._sr_path!r})"
            )

        runner_script = os.path.join(self._sr_path, "scenario_runner.py")
        if not os.path.isfile(runner_script):
            raise RuntimeError(f"scenario_runner.py not found at {runner_script}")

        xosc_path = _resolve_xosc_path(filename)
        if ego_role and ego_role != "ego_vehicle":
            xosc_path = self._rewrite_xosc_for_session(xosc_path, ego_role)
            self._temp_xosc_path = xosc_path

        # --sync makes SR the sole ticker; the bridge's tick_loop yields while
        # is_running is True and re-arms sync mode after SR exits.
        # --waitForEgo: SR connects to the existing ego (matched by role_name)
        # instead of spawning + destroying its own. The .xosc must mark the ego
        # entity with <Property name="type" value="ego_vehicle"/>.
        # --reloadWorld omitted on purpose (store_true flag; passing a value
        # confuses argparse).
        # --sync: SR owns the world tick during the run; the bridge yields
        # its tick_loop. SR's main loop is locally patched in
        # srunner/scenariomanager/scenario_manager.py to pace ticks at 20 Hz
        # wall-time so user controls feel like real-time instead of 5x.
        # --waitForEgo: SR attaches to the bridge's existing ego (matched by
        # role_name) instead of spawning + destroying its own.
        # -u: unbuffered stdout so the browser's event log streams live
        # rather than dumping every line at exit.
        cmd = [
            self._python,
            "-u",
            runner_script,
            "--openscenario", xosc_path,
            "--host", self._carla_host,
            "--port", str(self._carla_port),
            "--sync",
            "--waitForEgo",
        ]

        env = os.environ.copy()
        if self._pythonpath_prefix:
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                f"{self._pythonpath_prefix}:{existing}" if existing else self._pythonpath_prefix
            )

        self._prepare_world_for_launch()

        logger.info("Launching ScenarioRunner: %s", " ".join(cmd))

        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=self._sr_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                env=env,
            )
        except FileNotFoundError as e:
            raise RuntimeError(f"Failed to launch ScenarioRunner: {e}") from e

        self._current_file = filename
        self._started_at = time.time()
        self._exit_code = None

        threading.Thread(
            target=self._read_stdout,
            name="xosc-stdout-reader",
            daemon=True,
        ).start()

        self._broadcast({
            "type": "xosc_started",
            "file": filename,
            "started_at": self._started_at,
        })

        return {
            "type": "xosc_started",
            "file": filename,
            "pid": self._proc.pid,
        }

    def _rewrite_xosc_for_session(self, src_path: str, ego_role: str) -> str:
        """Copy ``src_path`` to a temp .xosc, replacing the ego role references.

        Targets the two attributes that drive SR's actor lookup:
          * ``<ScenarioObject name="ego_vehicle">`` → ``name="<ego_role>"``
          * ``entityRef="ego_vehicle"`` (Private, EntityRef, etc.) → ``"<ego_role>"``

        Leaves ``<Property name="type" value="ego_vehicle"/>`` alone — that's
        SR's entity-type marker, not a role reference. Plain string replace
        rather than XML parse/write so comments, attribute order, and whitespace
        are preserved exactly (avoids tickling any SR parser quirk that depends
        on the file's original formatting).
        """
        with open(src_path) as f:
            content = f.read()
        content = content.replace('name="ego_vehicle"', f'name="{ego_role}"')
        content = content.replace('entityRef="ego_vehicle"', f'entityRef="{ego_role}"')
        fd, temp_path = tempfile.mkstemp(suffix=".xosc", prefix="bridge_ego_")
        os.close(fd)
        with open(temp_path, "w") as f:
            f.write(content)
        return temp_path

    def _prepare_world_for_launch(self) -> None:
        """Clean leftover scenario actors and flip world to async mode.

        Two reasons to scrub the world before SR launches:
          * SR signal-handler exits (Stop button) skip ScenarioManager.cleanup,
            so the firetruck (and any other scenario NPC) lives on in CARLA.
          * Even on natural exit, a partially-spawned actor pool can survive
            if the previous SR crashed. The next run's actor lookup gets
            confused.
        Wipe every vehicle except the ones the bridge owns (ego, trajectory
        playback, traffic-manager NPCs) so SR sees a clean slate.

        SR calls apply_settings(sync=True) on startup, which deadlocks
        against an already-sync world that nobody is ticking — we flip to
        async first so that call returns cleanly. Weather is NOT
        snapshotted: the .xosc <EnvironmentAction> is the source of truth
        and its weather persists past scenario end.
        """
        if self._world is None:
            return
        self._clear_scenario_actors()
        try:
            settings = self._world.get_settings()
            if settings.synchronous_mode:
                settings.synchronous_mode = False
                settings.fixed_delta_seconds = 0.0
                self._world.apply_settings(settings)
        except Exception as e:
            logger.warning("Failed to switch to async mode pre-launch: %s", e)

    def _clear_scenario_actors(self) -> None:
        """Destroy any vehicle that isn't owned by the bridge.

        Preserved roles:
          * ``ego_vehicle*`` — drive sessions (per-session role suffix)
          * ``trajectory`` — TrajectoryPlayer's playback car
          * ``autopilot`` — bridge-spawned traffic NPCs
        Everything else (OpenSCENARIO NPCs from prior runs, stale spawns
        from a crashed SR, manually placed cars) is destroyed.
        """
        if self._world is None:
            return
        destroyed = 0
        try:
            actors = list(self._world.get_actors().filter("vehicle.*"))
        except Exception as e:
            logger.warning("Failed to enumerate vehicles for cleanup: %s", e)
            return
        for actor in actors:
            try:
                role = actor.attributes.get("role_name", "") if actor.attributes else ""
            except Exception:
                role = ""
            if role.startswith("ego_vehicle") or role in ("trajectory", "autopilot"):
                continue
            try:
                actor.destroy()
                destroyed += 1
            except Exception as e:
                logger.debug("Failed to destroy scenario actor %d: %s", actor.id, e)
        if destroyed:
            logger.info("Cleared %d leftover scenario actor(s)", destroyed)

    def stop(self) -> dict:
        """Terminate the running ScenarioRunner subprocess (if any)."""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return {"type": "xosc_stopped", "was_running": False}

        logger.info("Stopping ScenarioRunner (pid=%d)", proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("ScenarioRunner did not exit on SIGTERM; killing")
            proc.kill()
            proc.wait(timeout=5)

        return {"type": "xosc_stopped", "was_running": True}

    # ── Internals ──

    def _read_stdout(self) -> None:
        """Reader thread: tail the subprocess and broadcast each line."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return

        try:
            for raw_line in proc.stdout:
                line = raw_line.rstrip()
                if not line:
                    continue
                self._broadcast({
                    "type": "xosc_event",
                    "line": line,
                    "ts": time.time(),
                })
        except Exception as e:
            logger.warning("ScenarioRunner stdout reader error: %s", e)
        finally:
            try:
                self._exit_code = proc.wait(timeout=1)
            except Exception:
                self._exit_code = proc.poll()
            duration = (time.time() - self._started_at) if self._started_at else 0.0
            logger.info(
                "ScenarioRunner finished: file=%s exit=%s duration=%.1fs",
                self._current_file, self._exit_code, duration,
            )

            if self._temp_xosc_path:
                try:
                    os.unlink(self._temp_xosc_path)
                except Exception as e:
                    logger.debug("Failed to delete temp xosc %s: %s", self._temp_xosc_path, e)
                self._temp_xosc_path = None

            # SR's signal-handler exit path (Stop button) skips
            # ScenarioManager.cleanup, leaving the firetruck and other
            # scenario NPCs in the world. Even natural exits can leak when
            # SR crashes mid-spawn. Wipe them now so the next launch's
            # entityRef lookups don't collide with stale role_names.
            self._clear_scenario_actors()

            self._broadcast({
                "type": "xosc_finished",
                "file": self._current_file,
                "exit_code": self._exit_code,
                "verdict": "SUCCESS" if self._exit_code == 0 else "FAILURE",
                "duration_sec": round(duration, 1),
            })
            self._proc = None
