"""Pure public-vocabulary features and actual receipt outcome labels."""


class ProgramOutcomeAdapter:
    """Visible code vocabulary only; no oracle, verifier labels or private test inputs."""

    feature_names = ("input_variant", "candidate_variant")
    target_names = ("goal_complete", "test_pass_fraction")

    def __init__(self, world):
        self.codes = tuple(sorted({world.spec.source(world.spec.initial), *world.spec.candidates}))
        self.candidates = tuple(sorted(world.spec.candidates))
        self.specification = {
            "adapter": "program-observed-outcome/v1",
            "domain": "software",
            "task": world.task.model_dump(exclude={"seed"}),
            "codes": self.codes,
            "candidates": self.candidates,
        }

    def features(self, state, action):
        if state.domain != "software" or action.kind != "patch" or action.state_id != state.id:
            raise ValueError("Unsupported program input binding")
        before = state.payload["files"]["program.py"]
        after = action.payload["files"]["program.py"]
        if (
            after not in self.candidates
            or state.payload["task_title"] != self.specification["task"]["title"]
        ):
            raise ValueError("Unsupported program/task vocabulary")
        return float(self.codes.index(before)), float(self.codes.index(after))

    def targets(self, row):
        self.features(row.before, row.action)
        goal = row.after.state.payload["goal_complete"]
        fraction = row.after.metrics["test_pass_fraction"]
        if (
            type(goal) is not bool
            or not 0 <= fraction <= 1
            or row.after.metrics["goal_progress"] != float(goal)
            or row.after.state.payload["files"] != row.action.payload["files"]
        ):
            raise ValueError("Misaligned actual program outcome")
        return float(goal), float(fraction)

    def decode(self, state, action, cell):
        self.features(state, action)
        return dict(zip(self.target_names, cell.mean, strict=True)), {
            k: (lo, hi)
            for k, lo, hi in zip(self.target_names, cell.minimum, cell.maximum, strict=True)
        }
