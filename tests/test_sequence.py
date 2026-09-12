"""Tests for crash-safe mesh sequence number reservation."""

from __future__ import annotations

import pytest

from godox_mesh_bt.sequence import SequenceReserver


def test_reserver_persists_a_block_ahead_on_first_take() -> None:
    persisted: list[int] = []
    reserver = SequenceReserver(1000, block_size=100, persist=persisted.append)

    assert reserver.take() == 1000
    # The whole block is durably claimed before any of it is used on air.
    assert persisted == [1100]


def test_reserver_serves_the_block_without_further_persistence() -> None:
    persisted: list[int] = []
    reserver = SequenceReserver(0, block_size=4, persist=persisted.append)

    assert [reserver.take() for _ in range(4)] == [0, 1, 2, 3]
    assert persisted == [4]


def test_reserver_claims_a_new_block_when_exhausted() -> None:
    persisted: list[int] = []
    reserver = SequenceReserver(0, block_size=2, persist=persisted.append)

    assert [reserver.take() for _ in range(5)] == [0, 1, 2, 3, 4]
    assert persisted == [2, 4, 6]


def test_reserver_never_reissues_after_a_simulated_crash() -> None:
    """A restart resumes from the persisted high-water mark, not the used one."""
    persisted: list[int] = []
    reserver = SequenceReserver(0, block_size=50, persist=persisted.append)
    used = [reserver.take() for _ in range(3)]  # 0, 1, 2 — then the process dies

    recovered = SequenceReserver(persisted[-1], block_size=50, persist=persisted.append)

    assert recovered.take() > max(used)
    assert recovered.take() == 51


def test_reserver_reports_the_high_water_mark() -> None:
    reserver = SequenceReserver(10, block_size=5, persist=lambda value: None)

    assert reserver.high_water_mark == 10
    reserver.take()
    assert reserver.high_water_mark == 15


def test_reserver_rejects_a_non_positive_block() -> None:
    with pytest.raises(ValueError, match="block_size"):
        SequenceReserver(0, block_size=0, persist=lambda value: None)


def test_reserver_rejects_a_negative_start() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        SequenceReserver(-1, block_size=10, persist=lambda value: None)


def test_observe_persists_a_new_block_when_the_counter_passes_the_mark() -> None:
    """Push mode: the counter lives elsewhere, the reserver only decides when to write."""
    persisted: list[int] = []
    reserver = SequenceReserver(0, block_size=10, persist=persisted.append)

    for used in range(1, 10):
        reserver.observe(used)

    assert persisted == [10]
    assert reserver.high_water_mark == 10


def test_observe_reserves_again_as_the_counter_climbs() -> None:
    persisted: list[int] = []
    reserver = SequenceReserver(0, block_size=10, persist=persisted.append)

    for used in range(1, 26):
        reserver.observe(used)

    assert persisted == [10, 20, 30]


def test_observe_ignores_a_counter_still_inside_the_block() -> None:
    persisted: list[int] = []
    reserver = SequenceReserver(100, block_size=50, persist=persisted.append)
    reserver.observe(101)
    persisted.clear()

    reserver.observe(120)

    assert persisted == []


def test_observed_counter_is_always_covered_by_the_persisted_mark() -> None:
    """The invariant that keeps a restart from replaying a used number."""
    persisted: list[int] = [0]
    reserver = SequenceReserver(0, block_size=8, persist=persisted.append)

    for used in range(1, 200):
        reserver.observe(used)
        assert persisted[-1] > used
