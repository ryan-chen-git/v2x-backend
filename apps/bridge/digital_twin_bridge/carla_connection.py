"""
Manages the connection to the CARLA simulator.

Provides a context-manager interface so the original world settings are
always restored on exit.
"""

import logging
from typing import Optional

import carla

from digital_twin_bridge.config import Config

logger = logging.getLogger(__name__)


class CarlaConnection:
    """Persistent connection to a CARLA simulator instance.

    Usage::

        with CarlaConnection(config) as conn:
            world = conn.world
            carla_map = conn.carla_map
            conn.tick()
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._client: Optional[carla.Client] = None
        self._world: Optional[carla.World] = None
        self._map: Optional[carla.Map] = None
        self._original_settings: Optional[carla.WorldSettings] = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Connect to CARLA, retrieve the world/map, and enable sync mode."""
        logger.info(
            "Connecting to CARLA at %s:%d ...",
            self._config.CARLA_HOST,
            self._config.CARLA_PORT,
        )
        self._client = carla.Client(
            self._config.CARLA_HOST, self._config.CARLA_PORT
        )
        self._client.set_timeout(30.0)

        self._world = self._client.get_world()
        self._map = self._world.get_map()

        # Save original settings so we can restore them later
        self._original_settings = self._world.get_settings()

        # If CARLA is stuck in sync mode from a previous crash, reset first
        if self._original_settings.synchronous_mode:
            logger.warning("CARLA was already in sync mode (previous crash?). Resetting...")
            reset = self._world.get_settings()
            reset.synchronous_mode = False
            self._world.apply_settings(reset)
            import time
            time.sleep(0.5)
            self._original_settings = self._world.get_settings()

        # Enable synchronous mode with a fixed delta.
        settings = self._world.get_settings()
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = 0.05  # 20 Hz simulation
        self._world.apply_settings(settings)
        self._client.set_timeout(10.0)

        # Bright noon at connect — without this, get_weather() returns
        # WeatherParameters() defaults (sun_altitude_angle=0, dark). Once a
        # scenario runs, its <EnvironmentAction> overrides this and the new
        # weather persists past scenario end (no bridge-side restore).
        self._world.set_weather(carla.WeatherParameters(
            cloudiness=0.0,
            precipitation=0.0,
            precipitation_deposits=0.0,
            wind_intensity=30.0,
            sun_azimuth_angle=180.0,
            sun_altitude_angle=75.0,
            fog_density=0.0,
            fog_distance=100000.0,
            wetness=0.0,
        ))

        logger.info(
            "Connected to CARLA. Map: %s | Sync mode enabled.",
            self._map.name,
        )

    def disconnect(self) -> None:
        """Restore original world settings and release references."""
        if self._world is not None and self._original_settings is not None:
            try:
                self._world.apply_settings(self._original_settings)
                logger.info("Restored original CARLA world settings.")
            except Exception:
                logger.warning(
                    "Failed to restore CARLA world settings.", exc_info=True
                )
        self._client = None
        self._world = None
        self._map = None
        self._original_settings = None

    def tick(self) -> int:
        """Advance the simulation by one step (synchronous mode).

        Returns:
            The frame id returned by :meth:`carla.World.tick`.
        """
        if self._world is None:
            raise RuntimeError("Not connected to CARLA.")
        return self._world.tick()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def world(self) -> carla.World:
        """The active CARLA world."""
        if self._world is None:
            raise RuntimeError("Not connected to CARLA.")
        return self._world

    @property
    def carla_map(self) -> carla.Map:
        """The active CARLA map."""
        if self._map is None:
            raise RuntimeError("Not connected to CARLA.")
        return self._map

    @property
    def client(self) -> carla.Client:
        """The underlying CARLA client."""
        if self._client is None:
            raise RuntimeError("Not connected to CARLA.")
        return self._client

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "CarlaConnection":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[override]
        self.disconnect()
        return None
