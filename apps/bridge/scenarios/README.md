# OpenSCENARIO scenarios

Drop `.xosc` files (OpenSCENARIO **1.x**, the version CARLA's
[ScenarioRunner](https://github.com/carla-simulator/scenario_runner) supports)
in this directory. They show up in the **OpenSCENARIO** modal on the drive
page and can be started/stopped from there.

> **Note**: <https://www.asam.net/static_downloads/public/asam-openscenario/2.0.0/welcome.html>
> is OpenSCENARIO **2.0**, a brand new DSL with `.osc` syntax. CARLA does
> **not** support 2.0. Stick to 1.x XML files for this stack.

## How a scenario integrates with the bridge

```
                    BROWSER                                    DEV PC

  ┌─────────────────────────┐                  ┌────────────────────────────────┐
  │  drive/+page.svelte     │                  │ apps/bridge (Python WS server) │
  │  ┌───────────────────┐  │                  │ ┌────────────────────────────┐ │
  │  │ ScenarioPicker    │◄─┼── WS messages ──►│ │ drive_server.py            │ │
  │  └───────────────────┘  │                  │ │   owns ego (role_name=     │ │
  │  ┌───────────────────┐  │                  │ │   "ego_vehicle") + cameras │ │
  │  │ gamepad.ts        │──┼─ control msgs ──►│ └────────────────────────────┘ │
  │  │ keyboard.ts       │  │                  │ ┌────────────────────────────┐ │
  │  └───────────────────┘  │                  │ │ openscenario_runner.py     │ │
  └─────────────────────────┘                  │ │   spawns subprocess ──┐    │ │
                                               │ └───────────────────────┼────┘ │
                                               │                         │      │
                                               │                         ▼      │
                                               │      ┌──────────────────────┐  │
                                               │      │ scenario_runner.py   │  │
                                               │      │ (3rd-party, cloned)  │  │
                                               │      └──────────┬───────────┘  │
                                               └─────────────────┼──────────────┘
                                                                 │
                                                                 ▼
                                                       ┌─────────────────┐
                                                       │  CARLA :2000    │
                                                       │  (Docker)       │
                                                       └─────────────────┘
```

Three actors share one CARLA instance:

1. **`drive_server.py`** — owns the ego (`role_name="ego_vehicle"`) and the
   cameras. Wheel/keyboard input from `gamepad.ts` / `keyboard.ts` controls
   this car.
2. **`scenario_runner.py` subprocess** — spawns NPCs, watches triggers,
   evaluates pass/fail. Configured to attach to the existing ego rather than
   spawn its own.
3. **Browser** — picks a `.xosc` from `ScenarioPicker.svelte`, sees stdout
   stream back as `xosc_event` messages.

### Tick-mode handoff

The bridge runs CARLA in **synchronous mode at 20 Hz** and is the tick owner.
ScenarioRunner is launched with `--sync` so it owns the tick during a run.
Coordination, all in `openscenario_runner.py` and `drive_main.py`'s
`tick_loop`:

| Phase | Bridge | ScenarioRunner |
|---|---|---|
| Idle | sync, 20 Hz, ticking | not running |
| `start()` pre-launch | flips world to **async** so SR's first `apply_settings(sync=True)` doesn't deadlock | — |
| Subprocess running | `tick_loop` yields (sees `is_running`) | sync, ticks the world itself |
| SR exits | re-applies sync mode + 20 Hz `fixed_delta_seconds` | restored weather snapshot before announcing `xosc_finished` |

This handoff is the entire reason the bridge needs to know the runner exists;
without it ScenarioRunner's watchdog times out at 11s.

## One-time setup on the dev PC

Clone ScenarioRunner outside the bridge repo:

```bash
git clone https://github.com/carla-simulator/scenario_runner.git ~/scenario_runner
```

ScenarioRunner pins `numpy==1.24.4` which won't build on Python 3.12. Install
its requirements into a Python 3.10 venv, e.g. the existing `carla-venv-310`:

```bash
/home/path/V2XCarla/carla-venv-310/bin/pip install -r ~/scenario_runner/requirements.txt
```

Then point the bridge at the clone and the right interpreter:

```bash
export DTB_SCENARIO_RUNNER_PATH=$HOME/scenario_runner
export DTB_SCENARIO_RUNNER_PYTHON=/home/path/V2XCarla/carla-venv-310/bin/python
export DTB_SCENARIO_RUNNER_PYTHONPATH=/home/path/V2XCarla/Carla/PythonAPI/carla
```

The picker's banner reports whether the runner is configured.

### Required upstream patch

ScenarioRunner has a parser bug in this version (and at the time of writing,
upstream too): `AcquirePositionAction` constructs `ChangeActorWaypoints`
without `times`, but the constructor crashes on `len(None)` instead of
treating `times=None` as "no time constraint." Patch a single line in your
clone:

```diff
# srunner/scenariomanager/scenarioatomics/atomic_behaviors.py, ~line 773
-        if len(self._waypoints) != len(self._times):
+        if self._times is not None and len(self._waypoints) != len(self._times):
             raise ValueError("Both 'waypoints' and 'times' must have the same length")
```

Without this, any `.xosc` using `<AcquirePositionAction>` or
`<AssignRouteAction>` fails to load. With it, `times=None` correctly skips
the time-based speed override in `NpcVehicleControl`, so an explicit
`<SpeedAction>` controls speed.

A second patch paces SR's main loop at real-time. `ScenarioManager.run_scenario`
ticks the world as fast as the renderer can manage (~5x real-time on this
box), which makes the ego's throttle inputs feel hypersensitive when a
human shares the scenario. Add a `time.sleep` to cap the loop at 20 Hz
wall-time:

```diff
# srunner/scenariomanager/scenario_manager.py, in run_scenario()
+        import time as _time
+        _target_dt = 0.05
+        _last_loop_time = _time.time()
         while self._running:
             timestamp = None
             world = CarlaDataProvider.get_world()
             if world:
                 snapshot = world.get_snapshot()
                 if snapshot:
                     timestamp = snapshot.timestamp
             if timestamp:
                 self._tick_scenario(timestamp)
+
+            _now = _time.time()
+            _sleep_for = _target_dt - (_now - _last_loop_time)
+            if _sleep_for > 0:
+                _time.sleep(_sleep_for)
+            _last_loop_time = _time.time()
```

A third patch is a new actor controller, `basic_agent_control`, NPCs
can use instead of `npc_vehicle_control` to gain vehicle-obstacle and
traffic-light awareness. SR's bundled `npc_vehicle_control` is bare
LocalPlanner — it follows waypoints but plows straight through anything
in its path. `basic_agent_control` wraps CARLA's `BasicAgent` on top of
the same LocalPlanner, so the actor brakes for vehicles in its forward
route polygon. Used by `firetruck_from_south.xosc` and `firetruck_from_north.xosc`
(`<Property name="module" value="basic_agent_control"/>`).

Drop the bundled file into your SR clone:

```bash
cp apps/bridge/scenarios/patches/basic_agent_control.py \
   ~/scenario_runner/srunner/scenariomanager/actorcontrols/
```

No SR core changes required. Tunables inside (forward detection range,
emergency-stop brake force, traffic-light/stop-sign ignore flags) are
tuned for the firetruck demo; copy + rename if you want different
defaults for a different actor.

## Authoring a `.xosc` file

### Three mandatory rules

#### 1. Prefix `<FileHeader description>` with `CARLA:`

Without this, scenario_runner assumes right-handed (OpenSCENARIO 1.0 default)
and silently inverts y *and* yaw on every `<WorldPosition>`, so actors spawn
mirrored across the X axis — into terrain on RFS, with no error. Every
official example begins with `CARLA:` for this reason.

```xml
<FileHeader description="CARLA:Whatever describes your scenario"/>
```

#### 2. Mark each entity's type via `<Property>`

scenario_runner classifies entities by a `type` property. Without it the
entity goes to `other_actors` and the controller wiring takes the wrong path.

```xml
<!-- ego -->
<Properties><Property name="type" value="ego_vehicle"/></Properties>
<!-- NPC -->
<Properties><Property name="type" value="simulation"/></Properties>
```

When the ego is `ego_vehicle`, the bridge must launch SR with `--waitForEgo`
(already wired up in `openscenario_runner.py`) so SR attaches to the existing
ego by `role_name` instead of spawning + destroying its own.

#### 3. Spawn vehicles above the road waypoint Z, not at it

`map.get_waypoint(...).transform.location.z` is the road *surface*. Tall
vehicles (firetruck ≈ 3.5 m) clip the terrain when spawned at exactly that z
and `try_spawn_actor` returns `None` silently. SR's bulk spawn path doesn't
surface the failure either — you get a phantom actor whose controller exists
in the blackboard but no body in CARLA. Add ~1.5 m: `z = waypoint.z + 1.5`.

### Ego must be externally controlled — in TWO places

The ego needs `external_control` declared **both** at the entity level *and*
inside `Init`. The entity-level controller is what SR uses to validate the
.xosc; the Init `ControllerAction` is what's actually wired up at runtime.
Miss the Init one and SR falls back to `NpcVehicleControl` (LocalPlanner),
which silently overrides every `apply_control` call from the bridge — the
ego stops responding to keyboard/wheel input even though everything else
looks fine.

Entity-level (in `<Entities>`):

```xml
<ScenarioObject name="ego_vehicle">
  <Vehicle vehicleCategory="car" name="vehicle.tesla.model3"/>
  <ObjectController>
    <Controller name="ExternalController">
      <Properties>
        <Property name="module" value="external_control"/>
      </Properties>
    </Controller>
  </ObjectController>
</ScenarioObject>
```

Init-level (inside `<Storyboard><Init><Actions>`):

```xml
<Private entityRef="ego_vehicle">
  <PrivateAction>
    <ControllerAction>
      <AssignControllerAction>
        <Controller name="EgoExternalController">
          <Properties>
            <Property name="module" value="external_control"/>
          </Properties>
        </Controller>
      </AssignControllerAction>
      <OverrideControllerValueAction>
        <Throttle value="0" active="false"/>
        <Brake value="0" active="false"/>
        <Clutch value="0" active="false"/>
        <ParkingBrake value="0" active="false"/>
        <SteeringWheel value="0" active="false"/>
        <Gear number="0" active="false"/>
      </OverrideControllerValueAction>
    </ControllerAction>
  </PrivateAction>
</Private>
```

- `<ScenarioObject name="...">` **must** be `ego_vehicle` — the drive server
  stamps the ego blueprint with `role_name="ego_vehicle"` (see
  `drive_server.py: DriveSession.start`).
- The vehicle name attribute is a placeholder for validation; SR uses the
  existing actor regardless.
- See `srunner/scenarios/open_scenario.py:_create_init_behavior` for the
  fallback logic — `if controller_atomic is None: ChangeActorControl(...,
  control_py_module=None, ...)` and `ActorControl.__init__` defaults a
  vehicle to `NpcVehicleControl`.

### One strongly recommended rule

Add an `<EnvironmentAction>` with explicit weather. Without it,
`OpenScenarioConfiguration` defaults to `carla.WeatherParameters()` which
sets `sun_altitude_angle=0` (sun on the horizon → near-black world):

```xml
<GlobalAction>
  <EnvironmentAction>
    <Environment name="ClearNoon">
      <TimeOfDay animation="false" dateTime="2026-05-01T12:00:00"/>
      <Weather cloudState="free">
        <Sun intensity="1.0" azimuth="0.0" elevation="1.31"/>
        <Fog visualRange="100000.0"/>
        <Precipitation precipitationType="dry" intensity="0.0"/>
      </Weather>
      <RoadCondition frictionScaleFactor="1.0"/>
    </Environment>
  </EnvironmentAction>
</GlobalAction>
```

The bridge also snapshots and restores the prior weather around the run as a
safety net, but baking it into the `.xosc` keeps the scenario itself bright.

### Map and coords

`<RoadNetwork><LogicFile filepath="..."/></RoadNetwork>` must reference the
same map CARLA has loaded. CARLA exports `.xodr` files for stock maps under
`<Carla>/CarlaUE4/Content/Carla/Maps/OpenDrive/`. Custom maps (RFS) are
available by their loaded name (e.g. `Richmond_Field_Station_Richmond_CA`).

Positions in `<WorldPosition>` are OpenDRIVE/map-local space — **not** GPS or
the dashboard's `(lat, lon)`. To grab valid coordinates from the loaded map:

```bash
python3 -c "import carla; c=carla.Client('localhost',2000); c.set_timeout(10); \
            [print(sp) for sp in c.get_world().get_map().get_spawn_points()[:5]]"
```

## What you'll see at runtime

When you click **Start**:

1. Bridge snapshots weather, flips world to async, launches `scenario_runner.py
   --openscenario <file> --sync`.
2. SR connects to CARLA, finds the ego by role name, spawns NPCs / triggers.
3. Each line of SR's stdout streams back as an `xosc_event` WS message.
4. On exit: bridge restores weather; tick_loop re-arms sync mode; the verdict
   is the exit code (`0` → SUCCESS, non-zero → FAILURE).

Click **Stop** at any time to send SIGTERM to the subprocess.

## Files in this directory

- `sample.xosc` — minimal template demonstrating the externalControl ego, an
  `<EnvironmentAction>` for noon weather, and a single follow-vehicle NPC on
  `Richmond_Field_Station_Richmond_CA`.
- Anything else with a `.xosc` extension is auto-picked by the picker.
- `.json` files (legacy zone snapshots) live in `../scenes/`, not here.

## Mutual-exclusion rule

Only one scenario at a time across all connected browsers — the runner is a
server-level singleton constructed in `drive_main.py`. All connected sockets
see the same `xosc_event` stream.
