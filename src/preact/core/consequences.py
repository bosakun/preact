"""Conservative reuse admission; consequence values remain claim/horizon-specific."""

from .models import identity


def reuse_key(state, node, task, policy, declarations, remaining_depth):
    # Free-form assumptions cannot prove semantic compatibility. The immediate
    # input/action binding also prevents equating different root interventions.
    if not node.evaluation or not node.evaluation.claim_assessments:
        return None
    if any(p.assumptions for p in node.predictions):
        return None
    return identity(
        {
            "state": state.model_dump(exclude={"timestamp"}),
            "input_state": node.state.id,
            "path_parent": node.parent_id,
            "action": node.action.model_dump() if node.action else None,
            "claims": [a.claim.model_dump() for a in node.evaluation.claim_assessments],
            "task": task.model_dump(),
            "policy": policy.model_dump(),
            "engines": [d.model_dump() for d in declarations],
            "remaining_depth": remaining_depth,
            "source_conditions": [
                p.conditioning.model_dump() if p.conditioning else None for p in node.predictions
            ],
        }
    )
