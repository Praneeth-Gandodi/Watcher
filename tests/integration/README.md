# Integration test specifications

Agents add executable tests without modifying the protected composition root.

| Scenario | Specification | Executable test |
|---|---|---|
| `A` | normal allocation: task -> candidates -> bids -> negotiation -> assignment | `agent1/test_normal_allocation.py` |
| `B` | failed robot: failure -> available task -> reassignment | `agent1/test_failed_robot_reassignment.py` |
| `C` | low battery: migration or charging decision -> task completion | `agent2/test_deterministic_scenario.py` (battery-low plus the whole pipeline) |
| `D` | collision: route conflict -> right-of-way/replan -> safe movement | `agent2/test_deterministic_scenario.py`, `unit/safety/test_right_of_way.py` |
| `E` | deadlock: wait-for cycle -> detection -> recovery | `unit/safety/test_deadlock.py`, `unit/simulation/test_runtime.py` |
| `F` | communication loss: timeout -> recovery/reassignment | `agent2/test_deterministic_scenario.py` |
| `G` | controller outage: agents continue safe local work | `unit/simulation/test_runtime.py` |
| `H` | scalability: 500+ robots meet documented performance targets | `scalability/test_large_fleet.py` |

Each scenario must assert emitted events and final task/robot state.

## What each scenario additionally proves

- **A and B** are Coder 1's original executable tests, unmodified.
- **C** runs the whole pipeline in one deterministic 5-robot world: allocation,
  route planning through a single-cell gap, a genuine space-time conflict
  between two robots at different speeds, resolution by a safe timing delay with
  no residual collision, continued simulation, battery drain, a
  `ROBOT_FAILED` that Agent 1 turns into a `TASK_REASSIGNED`, and a valid final
  snapshot. It also asserts two identical runs produce identical state and
  identical events.
- **D** additionally asserts that a shared route at different times is *not* a
  conflict, that a permanently occupied cell is reported as unresolvable rather
  than silently ignored, and that every resolution in the core scenario used a
  timing delay rather than an experimental hold.
- **E** additionally asserts that recovery never mutates the caller's wait graph
  and that a 1500-robot cycle is detected without recursion.
- **F** asserts that communication loss leaves the robot physically present and
  only removes it from coordination, which is distinct from failure.
- **G** asserts that robots keep executing local work while
  `controller_available` is false.
- **H** prints measured initialisation, planning, conflict-check, and memory
  costs for 50 and 500 robots, and states the unoptimised limits explicitly.

`tests/integration/agent2/test_ten_robot_stress.py` covers the ten-robot demo
fleet: no exception, no negative or out-of-grid position, no robot on an
obstacle, no unresolved space-time collision, valid metrics, and world
invariants asserted on every tick.

## Not yet executable

- A scenario where safe holding position or alternate-route replanning clears a
  head-on conflict. Both strategies are experimental and marked as such; the MVP
  reports the conflict and leaves it open rather than pretending to resolve it.
