"""Scene-instance partitions for actual Cartesian MuJoCo evaluation, never NVIDIA proof."""

from dataclasses import dataclass

from preact.core.models import identity


@dataclass(frozen=True)
class Scene:
    name: str
    family: str
    configuration: dict

    @property
    def fingerprint(self):
        return identity(
            {"name": self.name, "family": self.family, "configuration": self.configuration}
        )


def all_scenes():
    cases = []
    for family in ["clearance", "grasp-place", "perception-dynamics"]:
        for index in range(14):
            y = (index - 6.5) * 0.012
            half = 0.018 + 0.002 * (index % 7)
            configuration = {
                "object": [-0.32 - 0.007 * index, y, half + 0.005],
                "target": [0.28 + 0.006 * index, -y, half + 0.005],
                "obstacle": [0, 0, 0.06 + 0.012 * (index % 6)],
                "obstacle_half": [
                    0.045 + 0.004 * (index % 5),
                    0.055 + 0.011 * index,
                    0.06 + 0.012 * (index % 6),
                ],
                "pose_error": 0.01,
                "cube_half": half,
                "mass": 0.1,
                "friction": 1.0,
                "grasp_response": 0.01,
                "actuator_step": 0.002,
            }
            if family == "clearance":
                # Some scenes have a clear direct route; others require detours,
                # including a low ceiling that invalidates a generic lift rule.
                if index % 4 == 0:
                    configuration["obstacle"][1] = 0.32
                if index % 3 == 2:
                    configuration["extra_obstacles"] = [
                        {"center": [0, 0, 0.34], "half": [0.6, 0.6, 0.025]}
                    ]
            elif family == "grasp-place":
                configuration.update(
                    mass=0.06 + 0.013 * index,
                    friction=0.3 + 0.09 * index,
                    grasp_response=0.009 + 0.003 * (index % 6),
                    actuator_step=0.0015 + 0.0002 * (index % 5),
                )
                if index % 3 == 0:
                    configuration["obstacle"][1] = -0.3
            else:
                configuration.update(
                    pose_error=0.018 + 0.004 * index,
                    mass=0.07 + 0.012 * index,
                    friction=0.25 + 0.07 * index,
                    grasp_response=0.012 + 0.003 * (index % 5),
                )
                if index % 4 == 1:
                    configuration["extra_obstacles"] = [
                        {"center": [0.15, -0.32, 0.08], "half": [0.04, 0.07, 0.08]}
                    ]
            cases.append(Scene(f"{family}-{index:02d}", family, configuration))
    return cases


def split_scenes(split):
    start, end = {"development": (0, 2), "calibration": (2, 6), "held_out": (6, 14)}[split]
    return [case for case in all_scenes() if start <= int(case.name.rsplit("-", 1)[1]) < end]
