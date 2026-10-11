"""Owned evaluation fixture, NOT a change to normal Queue v2 admission semantics.

A public task-local submission permit is an additional mandatory obligation.
Structural Action validation still admits all three proposals; only independent
verification qualifies the permit. The executor also enforces it before effects.
"""

from preact.domains.information_queue import InformationQueueWorld, action_amount
from preact.engines.information_queue import InformationBoundVerifier


class AdmissionQueueFixture(InformationQueueWorld):
    def __init__(self, *args, submission_limit=1, **kwargs):
        self.submission_limit = submission_limit
        super().__init__(*args, **kwargs)
        self.task = self.task.model_copy(
            deep=True,
            update={
                "id": "owned-queue-admission-v1",
                "required_checks": [*self.task.required_checks, "submission_permit"],
            },
        )

    def reset(self):
        super().reset()
        self.payload["submission_limit"] = self.submission_limit

    async def execute(self, action, receipt):
        state = await self.observe()
        if action_amount(state, action) > self.submission_limit:
            raise ValueError("Owned fixture submission permit denied")
        result = await super().execute(action, receipt)
        result.checks["submission_permit"] = True
        return result


class AdmissionFixtureVerifier(InformationBoundVerifier):
    def __init__(self, task):
        super().__init__(task)
        self.capabilities = self.capabilities.model_copy(
            update={
                "engine_id": "owned-queue-admission-verifier",
                "applicability": "Owned fixture public submission permit and queue bound",
            }
        )

    async def predict(self, request):
        result = await super().predict(request)
        result.mandatory_checks["submission_permit"] = (
            action_amount(request.state, request.actions[0])
            <= request.state.payload["submission_limit"]
        )
        return result
