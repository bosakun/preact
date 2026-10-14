"""Compatibility name for the common, Runtime-owned dispatch binding fence."""

from time import time as wall_time

from preact.cognition.selection import SelectionWorld


class QueueMultiSelectionWorld(SelectionWorld):
    def _now(self):
        return wall_time()
