# Integration test specifications

Agents add executable tests without modifying the protected composition root.

- `A` normal allocation: task → candidates → bids → negotiation → assignment
- `B` failed robot: failure → available task → reassignment
- `C` low battery: migration or charging decision → task completion
- `D` collision: route conflict → right-of-way/replan → safe movement
- `E` deadlock: wait-for cycle → detection → recovery
- `F` communication loss: timeout → recovery/reassignment
- `G` controller outage: agents continue safe local work
- `H` scalability: 500+ robots meet documented performance targets

Each scenario must assert emitted events and final task/robot state.
