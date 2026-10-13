"""Local prediction computation metadata, never external API or execution receipts."""


def usage_record(prediction, operation="deliberation_prediction"):
    return {
        "operation_kind": operation,
        "engine_id": prediction.engine_id,
        "engine_version": prediction.engine_version,
        "prediction_id": prediction.id,
        "cost_usd": prediction.cost_usd,
        "cost_known": True,
        "external_api_calls": 0,
    }
