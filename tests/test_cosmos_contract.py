import numpy as np
import pytest

from preact.engines.cosmos import action_sequence


def test_cartesian_conditioning_preserves_endpoint_and_timing():
    sequence = action_sequence([0, 0, 0], [[0.1, 0, 0.2], [0.3, 0.1, 0.2]], 2)
    assert sequence.shape == (40, 7)
    assert np.allclose(sequence[:, :3].sum(axis=0) / 20, [0.3, 0.1, 0.2])
    assert np.allclose(sequence[:, 3:6], 0)
    assert np.allclose(sequence[:, 6], 1)
    assert np.allclose(action_sequence([0, 0, 0], [[0, 0, 0]], 1, release=True)[:, 6], 0)
    with pytest.raises(ValueError):
        action_sequence([0, 0, 0], [[float("nan"), 0, 0]], 1)


def test_uneven_waypoints_preserve_total_frames_and_closed_padding():
    from preact.engines.cosmos import pad_chunks

    sequence = action_sequence([0, 0, 0], [[0.1, 0, 0], [0.2, 0, 0], [0.3, 0, 0]], 2)
    assert sequence.shape == (40, 7)
    assert np.allclose(sequence[:, :3].sum(axis=0) / 20, [0.3, 0, 0])
    padded, extra = pad_chunks(sequence)
    assert padded.shape == (48, 7) and extra == 8
    assert np.allclose(padded[40:, :6], 0) and np.allclose(padded[:, 6], 1)


def test_loader_conditions_on_endpoint_not_previous_motion_start(monkeypatch):
    import sys
    from types import SimpleNamespace

    from preact.engines.cosmos import load_actions

    video = np.array([[[[1, 2, 3]]], [[[7, 8, 9]]]])
    monkeypatch.setitem(sys.modules, "mediapy", SimpleNamespace(read_video=lambda _: video))
    loaded = load_actions()({"actions": [[0, 0, 0, 0, 0, 0, 1]]}, "fixture", None)
    assert np.array_equal(loaded["initial_frame"], video[-1])
    assert not np.array_equal(loaded["initial_frame"], video[0])


@pytest.mark.parametrize(
    "revision,dirty", [("expected", False), ("other", False), ("expected", True)]
)
def test_checkout_validation_rejects_mismatch_and_uncommitted_model_changes(
    monkeypatch, revision, dirty
):
    from types import SimpleNamespace

    from preact.core.interfaces import EngineFailure
    from preact.engines.cosmos import verify_checkout

    monkeypatch.setattr("preact.engines.cosmos.subprocess.check_output", lambda *a, **kw: revision)
    monkeypatch.setattr(
        "preact.engines.cosmos.subprocess.run",
        lambda *a, **kw: SimpleNamespace(returncode=int(dirty)),
    )
    if revision == "expected" and not dirty:
        verify_checkout("fixture-repository", "expected")
    else:
        with pytest.raises(EngineFailure):
            verify_checkout("fixture-repository", "expected")
