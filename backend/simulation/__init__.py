"""Agent 2 simulation runtime: authoritative world, grid, and safety state.

Module boundaries inside this package:

* :mod:`backend.simulation.grid` -- coordinate conversion, occupancy, footprints.
* :mod:`backend.simulation.world` -- deterministic world and fleet construction.
* :mod:`backend.simulation.runtime` -- simulated time, movement, safety, faults,
  event production, and the ``SimulationSnapshot``/``SystemMetrics`` projections.

Nothing is re-exported here on purpose: importing this package must never pull
in the runtime, so :mod:`backend.safety` can depend on the grid boundary without
creating an import cycle.
"""
