"""Durable storage for the mesh sequence number.

The mesh keys live in the config entry because they never change. The sequence
number is different: it advances on every command, and a node silently drops any
PDU whose sequence number it has already recorded in its replay protection list.

It therefore needs somewhere durable that tolerates frequent writes, which is
what :class:`homeassistant.helpers.storage.Store` is for — it keeps the file in
``.storage`` and does the actual disk I/O in an executor, off the event loop.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
KEY_SEQUENCE_NUMBER = "sequence_number"

# Writes are coalesced. This is safe only because what gets stored is a
# reservation that runs ahead of the counter actually on air, so losing the
# last few seconds of writes can never replay a number that was already used.
SAVE_DELAY_SECONDS = 5.0


class GodoxSequenceStore:
    """Persist the reserved mesh sequence high-water mark for one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the store for a config entry."""
        self._store = Store[dict[str, int]](
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}",
            private=True,
            atomic_writes=True,
        )
        self._sequence_number = 0

    async def async_load(self, fallback: int) -> int:
        """Return the stored high-water mark, or *fallback* if there is none.

        The fallback is the sequence number from the config entry, used the
        first time an entry runs after being created or imported.
        """
        data = await self._store.async_load()
        stored = (data or {}).get(KEY_SEQUENCE_NUMBER)
        self._sequence_number = max(int(stored or 0), fallback)
        _LOGGER.debug(
            "loaded mesh sequence high-water mark %d (stored=%s fallback=%d)",
            self._sequence_number,
            stored,
            fallback,
        )
        return self._sequence_number

    @callback
    def async_set_sequence_number(self, sequence_number: int) -> None:
        """Schedule a coalesced write of a newly reserved high-water mark."""
        self._sequence_number = sequence_number
        snapshot = {KEY_SEQUENCE_NUMBER: sequence_number}
        self._store.async_delay_save(lambda: snapshot, SAVE_DELAY_SECONDS)

    async def async_flush(self) -> None:
        """Write the current high-water mark immediately."""
        await self._store.async_save({KEY_SEQUENCE_NUMBER: self._sequence_number})

    async def async_remove(self) -> None:
        """Delete the stored data when the config entry is removed."""
        await self._store.async_remove()
