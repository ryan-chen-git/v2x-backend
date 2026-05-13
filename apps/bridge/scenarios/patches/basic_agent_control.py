#!/usr/bin/env python

"""
Vehicle controller wrapping CARLA's BasicAgent.

Same interface as npc_vehicle_control (waypoint plan, target speed) but
BasicAgent's run_step() checks for vehicle obstacles and red lights every
tick and overrides the LocalPlanner control with an emergency stop when a
hazard is detected. Use this for NPCs that should yield to the ego instead
of plowing through.
"""

import math

import carla
from agents.navigation.basic_agent import BasicAgent
from agents.navigation.local_planner import RoadOption

from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
from srunner.scenariomanager.actorcontrols.basic_control import BasicControl
from srunner.scenariomanager.timer import GameTime


class BasicAgentControl(BasicControl):

    """
    Controller class for vehicles derived from BasicControl.

    Drop-in replacement for NpcVehicleControl that uses CARLA's BasicAgent
    so vehicle/traffic-light hazards trigger an emergency stop.

    Args:
        actor (carla.Actor): Vehicle actor that should be controlled.
    """

    _args = {'K_P': 1.0, 'K_D': 0.01, 'K_I': 0.0, 'dt': 0.05}

    def __init__(self, actor, args=None):
        super(BasicAgentControl, self).__init__(actor)
        # Track whether SR has pushed waypoints. BasicAgent.done() returns
        # True when its LocalPlanner queue is empty, but SR pushes waypoints
        # asynchronously (via AcquirePositionAction at sim_time > 0.5s), so
        # we'd otherwise report _reached_goal on tick 0 and SR would tear
        # the scenario down before it starts.
        self._has_plan = False

        # Flat 10 m detection threshold (no speed scaling): the firetruck
        # only emergency-stops once the ego is within 10 m. Combined with
        # the bridge's separate 20 m V2X "pull over" alert, this gives the
        # driver a chance to yield before the truck has to brake.
        # NOTE: don't put max_brake here — opt_dict is forwarded to
        # LocalPlanner too, and overriding its _max_brake from 0.3 to 1.0
        # makes routine startup braking look identical to an emergency
        # stop, which fights init_speed and triggers our is_emergency_stop
        # heuristic. Set BasicAgent._max_brake separately after construction.
        self._agent = BasicAgent(
            self._actor,
            target_speed=self._target_speed * 3.6,
            opt_dict={
                'lateral_control_dict': self._args,
                'base_vehicle_threshold': 10.0,
                'detection_speed_ratio': 0.0,
                'use_bbs_detection': True,
                # Firetruck is on an emergency response; don't treat red
                # lights or stop signs as hazards. Keep vehicle-obstacle
                # detection on so it still yields to the ego.
                'ignore_traffic_lights': True,
                'ignore_stop_signs': True,
            },
        )
        # Override BasicAgent's emergency-stop brake force only (LocalPlanner
        # still uses its own default 0.3 for normal control). Default 0.5 is
        # not enough to stop a firetruck before it crawls into the ego.
        self._agent._max_brake = 1.0  # pylint: disable=protected-access

        if self._waypoints:
            self._update_plan()

    def _update_plan(self):
        """
        Push the scenario waypoint list into the BasicAgent's LocalPlanner.
        """
        plan = []
        for transform in self._waypoints:
            waypoint = CarlaDataProvider.get_map().get_waypoint(
                transform.location, project_to_road=True, lane_type=carla.LaneType.Any)
            plan.append((waypoint, RoadOption.LANEFOLLOW))
        self._agent.set_global_plan(plan, stop_waypoint_creation=True, clean_queue=True)
        if plan:
            self._has_plan = True

    def reset(self):
        """
        Reset the controller
        """
        if self._actor and self._actor.is_alive:
            self._agent = None
            self._actor = None

    def run_step(self):
        """
        Execute one tick. BasicAgent.run_step() handles waypoint following
        plus collision/traffic-light hazard detection internally.
        """
        self._reached_goal = False

        if self._waypoints_updated:
            self._waypoints_updated = False
            self._update_plan()

        if self._target_speed < 0:
            raise NotImplementedError("Negative target speeds are not yet supported")

        self._agent.set_target_speed(self._target_speed * 3.6)
        if not self._actor.is_alive:
            return

        control = self._agent.run_step()

        # Only trust agent.done() once we've actually pushed a plan — before
        # that, the empty waypoint queue makes done() return True and would
        # cause SR to terminate the scenario on tick 0.
        if self._has_plan and self._agent.done():
            self._reached_goal = True

        self._actor.apply_control(control)

        # Init-speed boost: SR sets _init_speed=True when the .xosc declares
        # AbsoluteTargetSpeed, to snap the actor up to target velocity at
        # spawn instead of waiting for the PID to ramp. Skip the override
        # when the agent is asking for emergency stop — set_target_velocity
        # bypasses the brake, so otherwise it fights the hazard response.
        is_emergency_stop = control.brake >= self._agent._max_brake and control.throttle == 0  # pylint: disable=protected-access
        if self._init_speed and not is_emergency_stop:
            current_speed = math.sqrt(
                self._actor.get_velocity().x ** 2 + self._actor.get_velocity().y ** 2)
            if abs(self._target_speed - current_speed) > 3:
                yaw = self._actor.get_transform().rotation.yaw * (math.pi / 180)
                vx = math.cos(yaw) * self._target_speed
                vy = math.sin(yaw) * self._target_speed
                self._actor.set_target_velocity(carla.Vector3D(vx, vy, 0))
