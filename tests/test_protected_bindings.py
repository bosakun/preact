"""Defensive structural tests; rejected candidate fixtures are never executed."""

import pytest

from preact.domains.cloud import validate_generated_patch
from preact.domains.software import BASE, PREPARE, REPAIR, SAFE, SHORTCUT


@pytest.mark.parametrize("name", ["ValueError", "max", "min", "abs", "round"])
@pytest.mark.parametrize("binding", ["definition", "parameter", "assignment"])
def test_approved_operation_bindings_are_immutable(name, binding):
    if binding == "definition":
        source = f"def {name}(value):\n    return value\ndef checkout(total, discount):\n    return total - discount\n"
    elif binding == "parameter":
        source = f"def checkout(total, discount, {name}=0):\n    return total - discount\n"
    else:
        source = f"def checkout(total, discount):\n    {name} = 0\n    return total - discount\n"
    with pytest.raises(ValueError, match="cannot|cannot be"):
        validate_generated_patch(source)


@pytest.mark.parametrize(
    "body",
    [
        "    helper = 0\n    return total - discount\n",
        "    def helper(value):\n        return value\n    return total - discount\n",
    ],
)
def test_module_helper_bindings_cannot_be_replaced_or_shadowed(body):
    source = "def helper(value):\n    return value\ndef checkout(total, discount):\n" + body
    with pytest.raises(ValueError, match="rebound|module scope"):
        validate_generated_patch(source)


def test_parameters_cannot_shadow_module_helpers():
    source = "def helper(value):\n    return value\ndef checkout(total, discount, helper=0):\n    return total - discount\n"
    with pytest.raises(ValueError, match="shadow"):
        validate_generated_patch(source)


def test_duplicate_function_definitions_are_rejected():
    source = "def helper(value):\n    return value\ndef helper(value):\n    return 0\ndef checkout(total, discount):\n    return total - discount\n"
    with pytest.raises(ValueError, match="unique"):
        validate_generated_patch(source)


def test_reserved_function_identifiers_are_rejected_structurally():
    source = "def __reserved(value):\n    return value\ndef checkout(total, discount):\n    return total - discount\n"
    with pytest.raises(ValueError, match="Reserved"):
        validate_generated_patch(source)


@pytest.mark.parametrize("source", [BASE, PREPARE, REPAIR, SAFE, SHORTCUT])
def test_existing_candidate_repairs_retain_their_grammar(source):
    # Semantic safety still belongs to executable verification, including SHORTCUT.
    validate_generated_patch(source)


def test_similarly_named_data_variables_and_direct_helpers_remain_valid():
    validate_generated_patch(
        "def normalize(value):\n    return max(0, value)\ndef checkout(total, discount):\n    max_value = total - discount\n    return normalize(max_value)\n"
    )
