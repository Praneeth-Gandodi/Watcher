"""Simulation service wiring: the runtime, its clock, and its settings.

The service owns the background tick loop and the single shared
``SimulationRuntime``. Everything the API and the WebSocket stream read comes
from that one instance, so a snapshot and an event can never describe two
different worlds.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass

from backend.simulation.runtime import RuntimeConfig, SimulationRuntime

DEFAULT_TICK_RATE_HZ = 10.0
TICK_INTERVAL_S = 1.0 / DEFAULT_TICK_RATE_HZ


def fleet_size_from_env() -> int:
    """Read the configured fleet size, clamped to a sane range."""

    raw = os.environ.get("WATCHER_FLEET_SIZE", "50")
    try:
        return max(1, min(2000, int(raw)))
    except ValueError:
        return 50


def seed_from_env() -> int:
    raw = os.environ.get("WATCHER_SEED", "2026")
    try:
        return int(raw)
    except ValueError:
        return 2026


def tick_rate_from_env() -> float:
    raw = os.environ.get("WATCHER_TICK_RATE_HZ", str(DEFAULT_TICK_RATE_HZ))
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TICK_RATE_HZ
    return max(1.0, min(60.0, value))


def build_config() -> RuntimeConfig:
    """Build the runtime configuration from the environment."""

    return RuntimeConfig.for_fleet(
        fleet_size_from_env(),
        seed=seed_from_env(),
        tick_rate_hz=tick_rate_from_env(),
    )


@dataclass(slots=True)
class SimulationService:
    """Owns the runtime instance and drives it from a background task."""

    runtime: SimulationRuntime
    tick_interval_s: float = TICK_INTERVAL_S
    _task: asyncio.Task | None = None
    _wake: asyncio.Event | None = None
    _stopped: bool = False

    @classmethod
    def from_env(cls) -> SimulationService:
        config = build_config()
        return cls(
            runtime=SimulationRuntime(config),
            tick_interval_s=1.0 / config.tick_rate_hz,
        )

    async def start(self) -> None:
        """Start ticking until cancelled."""

        if self._task is not None:
            return
        self._stopped = False
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="watcher-simulation-tick")

    async def stop(self) -> None:
        self._stopped = True
        if self._wake is not None:
            self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        """Advance the simulation on a fixed interval.

        The loop runs the synchronous tick on the event loop thread. The tick is
        measured in tens of milliseconds even at 500 robots, and keeping it
        inline means the snapshot a client reads is never taken while a tick is
        half applied.
        """

        assert self._wake is not None
        while not self._stopped:
            try:
                self.runtime.tick()
            except Exception:  # pragma: no cover - the tick must never kill the loop
                import logging

                logging.getLogger(__name__).exception("simulation tick failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.tick_interval_s)
            except TimeoutError:
                continue
            self._wake.clear()

    def request_tick(self) -> None:
        """Ask the loop to run a tick as soon as it can.

        Commands that change the world (a new task, a reset) do not need to wait
        for the next scheduled tick to become visible.
        """

        if self._wake is not None:
            self._wake.set()
