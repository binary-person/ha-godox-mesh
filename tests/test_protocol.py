from __future__ import annotations

import pytest

from godox_mesh_bt.protocol import (
    validate_brightness,
    validate_cct,
)


@pytest.mark.parametrize("value", [0, 1, 50, 100])
def test_validate_brightness_accepts_percent_range(value: int) -> None:
    assert validate_brightness(value) == value


@pytest.mark.parametrize("value", [-1, 101])
def test_validate_brightness_rejects_out_of_range_values(value: int) -> None:
    with pytest.raises(ValueError, match="brightness must be between 0 and 100"):
        validate_brightness(value)


@pytest.mark.parametrize("value", [2800, 3200, 4300, 5600, 6500])
def test_validate_cct_accepts_ul60bi_kelvin_range(value: int) -> None:
    assert validate_cct(value) == value


@pytest.mark.parametrize("value", [50, 25501])
def test_validate_cct_rejects_values_the_wire_cannot_carry(value: int) -> None:
    """The default bound is the protocol's, not any one light's."""
    with pytest.raises(ValueError, match="CCT must be between"):
        validate_cct(value)


@pytest.mark.parametrize("value", [1800, 2500, 2700, 8500, 10000])
def test_validate_cct_accepts_the_whole_godox_range(value: int) -> None:
    """Godox mesh models span 1800-10000 K; hardcoding 2800-6500 broke half."""
    assert validate_cct(value) == value


@pytest.mark.parametrize("value", [2799, 6501])
def test_validate_cct_honours_a_models_own_range(value: int) -> None:
    """A caller that knows the model can still bound it tightly."""
    with pytest.raises(ValueError, match="CCT must be between 2800K and 6500K"):
        validate_cct(value, min_kelvin=2800, max_kelvin=6500)



