"""Every translation placeholder must actually be supplied.

Home Assistant formats these strings with `formatjs`, which fails loudly at
runtime with `MISSING_VALUE` when a `{placeholder}` has no value. That is a
user-visible error in the setup dialog, and nothing in the test suite caught it:
`flow_title` is `"{name}"`, and the device picker is rendered *before* any
device has been chosen, so `title_placeholders` had not been set yet.

Two different mechanisms are involved and they are easy to confuse:

* `flow_title` is filled from `context["title_placeholders"]`, set on the flow.
* A step's own `title`/`description` is filled from `description_placeholders`,
  passed to `async_show_form`.

Supplying one does not supply the other.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from custom_components.godox_mesh.const import DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "godox_mesh"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _strings() -> dict:
    return json.loads((COMPONENT / "strings.json").read_text())


def test_strings_and_english_translations_agree() -> None:
    """A placeholder added to one file and not the other fails only at runtime."""
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())

    assert _strings() == english, "strings.json and translations/en.json diverged"


def test_every_step_placeholder_is_supplied_by_the_flow() -> None:
    """Each `{placeholder}` in a step's text must be passed for that step.

    A step title that repeats the device name is a standing hazard: `flow_title`
    already shows it in the dialog header, and duplicating it into every step
    title gave three more chances to get the plumbing wrong. Those titles no
    longer take a placeholder at all, which is why this now has less to check.
    """
    source = (COMPONENT / "config_flow.py").read_text()
    strings = _strings()
    missing: list[str] = []

    for section in ("config", "options"):
        for step_id, step in (strings.get(section, {}).get("step") or {}).items():
            wanted: set[str] = set()
            for key in ("title", "description"):
                wanted |= set(PLACEHOLDER.findall(step.get(key) or ""))
            if not wanted:
                continue
            # The step must exist, and each placeholder must be supplied in the
            # same call that shows it -- not merely appear somewhere in the file.
            if f'step_id="{step_id}"' not in source:
                missing.append(f"{section}/{step_id}: no such step in config_flow.py")
                continue
            # Look at the whole enclosing function, not just the call: some
            # steps build their placeholders into a local dict first.
            start = source.index(f'step_id="{step_id}"')
            body_start = source.rfind("\n    async def ", 0, start)
            body_start = source.rfind("\n    def ", 0, start) if body_start < 0 else body_start
            body_end = source.find("\n    async def ", start)
            body_end = len(source) if body_end < 0 else body_end
            body = source[max(0, body_start) : body_end]
            for name in sorted(wanted):
                if f'"{name}"' not in body:
                    missing.append(
                        f"{section}/{step_id}: {name!r} is never supplied by the "
                        "function that shows this step"
                    )

    assert not missing, "unsupplied translation placeholders:\n  " + "\n  ".join(missing)


def test_flow_title_placeholders_are_set_before_the_first_form(
    hass: HomeAssistant,
) -> None:
    """`flow_title` is formatted as soon as the flow renders anything.

    This is the case that actually broke: the user-initiated flow shows a device
    picker before a device is chosen, so nothing had set `title_placeholders`.
    """
    wanted = set(PLACEHOLDER.findall(_strings()["config"].get("flow_title") or ""))
    if not wanted:
        pytest.skip("flow_title has no placeholders")

    flows = hass.config_entries.flow
    result = flows.async_progress()
    del result  # only to touch the manager before starting a flow

    hass.loop.set_debug(False)
    return  # the async assertion lives in the coroutine test below


@pytest.mark.usefixtures("fake_ble")
async def test_user_flow_renders_without_missing_placeholders(
    hass: HomeAssistant,
) -> None:
    """Start the user flow and confirm the title placeholders are populated."""
    wanted = set(PLACEHOLDER.findall(_strings()["config"].get("flow_title") or ""))

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    for flow in hass.config_entries.flow.async_progress():
        if flow["handler"] != DOMAIN:
            continue
        supplied = set(flow.get("context", {}).get("title_placeholders") or {})
        assert wanted <= supplied, (
            f"flow_title needs {sorted(wanted)} but the flow supplies "
            f"{sorted(supplied)} at step {result.get('step_id')!r}"
        )
