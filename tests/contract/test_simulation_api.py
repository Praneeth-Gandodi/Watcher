"""Contract tests for the simulation HTTP surface.

These assert the wire contract the dashboard depends on: the paths, the
response shapes, and the fact that the endpoints are thin adapters over the
composition root. ``/health`` is covered by ``test_api.py`` and is unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from backend.app import composition
from backend.app.composition import build_coordinator
from backend.app.main import app
from backend.contracts.events import EventType
from backend.contracts.models import Position2D, Task, TaskStatus
from tests.integration.agent2.conftest import five_robot_blueprint

PREFIX = "/api/v1"


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client bound to an isolated coordinator, never the process global."""

    coordinator = build_coordinator(five_robot_blueprint())
    app.dependency_overrides[composition.get_coordinator] = lambda: coordinator
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        composition.reset_coordinator()


def new_task(task_id: str = "task-001", cell: tuple[int, int] = (8, 3)) -> Task:
    return Task(
        task_id=task_id,
        target=Position2D(x=cell[0] + 0.5, y=cell[1] + 0.5),
        priority=3,
        required_capabilities=(),
        estimated_duration_s=20.0,
        status=TaskStatus.PENDING,
        assigned_robot_id=None,
        created_at_s=0.0,
    )


def test_health_is_unchanged(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "watcher-backend",
        "version": "0.1.0",
    }


def test_snapshot_exposes_the_authoritative_backend_state(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "world",
        "robots",
        "routes",
        "tasks",
        "simulation_time_s",
        "revision",
        "last_event_sequence",
        "controller_available",
    }
    assert len(body["robots"]) == 5
    assert body["world"]["columns"] == 10
    assert body["last_event_sequence"] >= body["revision"]
    assert body["controller_available"] is True


def test_metrics_are_returned_in_the_canonical_shape(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/metrics")

    assert response.status_code == 200
    body = response.json()
    for field in (
        "active_robots",
        "failed_robots",
        "communication_lost_robots",
        "pending_tasks",
        "completed_tasks",
        "open_conflicts",
        "detected_deadlocks",
        "task_reassignments",
        "average_battery_percent",
        "average_allocation_latency_ms",
        "event_throughput_per_s",
        "controller_available",
    ):
        assert field in body
    assert 0.0 <= body["average_battery_percent"] <= 100.0


def test_robots_tasks_routes_and_world_are_readable(client: TestClient) -> None:
    client.post(f"{PREFIX}/tasks", json={"task": new_task().model_dump(mode="json")})

    robots = client.get(f"{PREFIX}/robots").json()
    tasks = client.get(f"{PREFIX}/tasks").json()
    routes = client.get(f"{PREFIX}/routes").json()
    world = client.get(f"{PREFIX}/world").json()

    assert [robot["robot_id"] for robot in robots] == sorted(
        robot["robot_id"] for robot in robots
    )
    assert len(robots) == 5
    assert len(tasks) == 1
    assert len(routes) == 1
    assert world["cell_size_m"] == 1.0


def test_creating_a_task_returns_the_full_event_cascade(client: TestClient) -> None:
    response = client.post(
        f"{PREFIX}/tasks", json={"task": new_task().model_dump(mode="json")}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["accepted"] is True
    assert body["command_type"] == "CREATE_TASK"
    assert body["produced_event_types"] == [
        "TASK_CREATED",
        "NEGOTIATION_STARTED",
        *["BID_SUBMITTED"] * 5,
        "TASK_ASSIGNED",
        "ROUTE_REQUESTED",
        "ROUTE_PLANNED",
    ]
    assert body["last_event_sequence"] >= body["revision"]


def test_events_are_returned_after_a_cursor(client: TestClient) -> None:
    client.post(f"{PREFIX}/tasks", json={"task": new_task().model_dump(mode="json")})

    everything = client.get(f"{PREFIX}/events").json()
    tail = client.get(f"{PREFIX}/events", params={"after_sequence": 3}).json()

    assert everything[0]["sequence"] == 1
    assert [event["sequence"] for event in everything] == list(
        range(1, len(everything) + 1)
    )
    assert tail[0]["sequence"] == 4
    assert {event["producer"] for event in everything} == {
        "agent-1-negotiation",
        "agent-2-safety",
    }
    assert everything[0]["event_type"] == EventType.TASK_CREATED.value
    assert "task" in everything[0]["payload"]


def test_a_negative_event_cursor_is_rejected(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/events", params={"after_sequence": -1}).status_code == 422


def test_fault_injection_publishes_events_and_reassigns(client: TestClient) -> None:
    client.post(f"{PREFIX}/tasks", json={"task": new_task().model_dump(mode="json")})
    client.post(f"{PREFIX}/simulation/advance", params={"ticks": 5})
    holder = client.get(f"{PREFIX}/tasks").json()[0]["assigned_robot_id"]

    response = client.post(
        f"{PREFIX}/faults/failure",
        json={
            "robot_id": holder,
            "failure": {
                "kind": "actuator",
                "code": "drive-failure",
                "detected_at_s": 0.0,
            },
        },
    )

    assert response.status_code == 201
    produced = response.json()["produced_event_types"]
    assert produced[0] == "ROBOT_FAILED"
    assert "TASK_REASSIGNED" in produced
    assert "ROUTE_PLANNED" in produced
    robots = {robot["robot_id"]: robot for robot in client.get(f"{PREFIX}/robots").json()}
    assert robots[holder]["status"] == "failed"
    assert robots[holder]["failure"]["code"] == "drive-failure"


def test_communication_loss_keeps_the_robot_physically_present(
    client: TestClient,
) -> None:
    client.post(f"{PREFIX}/tasks", json={"task": new_task().model_dump(mode="json")})
    holder = client.get(f"{PREFIX}/tasks").json()[0]["assigned_robot_id"]

    response = client.post(
        f"{PREFIX}/faults/communication-loss",
        json={"robot_id": holder, "timeout_s": 3.0},
    )

    assert response.status_code == 201
    assert response.json()["produced_event_types"][0] == "COMMUNICATION_LOST"
    robots = {robot["robot_id"]: robot for robot in client.get(f"{PREFIX}/robots").json()}
    assert robots[holder]["communication_state"] == "lost"
    assert robots[holder]["failure"] is None
    assert robots[holder]["status"] != "failed"


def test_restore_brings_a_robot_back(client: TestClient) -> None:
    client.post(
        f"{PREFIX}/faults/failure",
        json={
            "robot_id": "robot-003",
            "failure": {"kind": "sensor", "code": "lidar-fault", "detected_at_s": 0.0},
        },
    )

    response = client.post(
        f"{PREFIX}/faults/restore", json={"robot_id": "robot-003"}
    )

    assert response.status_code == 201
    robots = {robot["robot_id"]: robot for robot in client.get(f"{PREFIX}/robots").json()}
    assert robots["robot-003"]["status"] == "idle"
    assert robots["robot-003"]["failure"] is None
    assert robots["robot-003"]["communication_state"] == "online"


def test_pause_resume_and_speed_control_simulated_time(client: TestClient) -> None:
    assert client.post(f"{PREFIX}/simulation/advance", params={"ticks": 10}).json()[
        "simulation_time_s"
    ] == pytest.approx(1.0)

    assert client.post(f"{PREFIX}/simulation/pause").status_code == 200
    paused = client.post(f"{PREFIX}/simulation/advance", params={"ticks": 50}).json()
    assert paused["simulation_time_s"] == pytest.approx(1.0)
    assert paused["produced_event_types"] == []

    assert client.post(f"{PREFIX}/simulation/resume").status_code == 200
    assert client.post(
        f"{PREFIX}/simulation/speed", json={"multiplier": 4.0}
    ).status_code == 200
    accelerated = client.post(f"{PREFIX}/simulation/advance", params={"ticks": 10}).json()
    assert accelerated["simulation_time_s"] == pytest.approx(1.0 + 4.0)


def test_an_out_of_range_speed_is_rejected(client: TestClient) -> None:
    assert (
        client.post(f"{PREFIX}/simulation/speed", json={"multiplier": 0.0}).status_code
        == 422
    )
    assert (
        client.post(f"{PREFIX}/simulation/speed", json={"multiplier": 99.0}).status_code
        == 422
    )
    assert client.post(
        f"{PREFIX}/simulation/speed", json={"multiplier": 2.0}
    ).status_code == 200


def test_reset_clears_state_but_keeps_the_fleet(client: TestClient) -> None:
    client.post(f"{PREFIX}/tasks", json={"task": new_task().model_dump(mode="json")})
    client.post(f"{PREFIX}/simulation/advance", params={"ticks": 20})

    response = client.post(f"{PREFIX}/simulation/reset", json={"seed": 7})

    assert response.status_code == 200
    assert client.get(f"{PREFIX}/tasks").json() == []
    assert len(client.get(f"{PREFIX}/robots").json()) == 5
    assert client.get(f"{PREFIX}/snapshot").json()["simulation_time_s"] == 0.0


def test_the_generic_command_endpoint_accepts_canonical_commands(
    client: TestClient,
) -> None:
    response = client.post(
        f"{PREFIX}/commands",
        json={
            "command_id": str(uuid4()),
            "issued_at_s": 0.0,
            "command_type": "CREATE_TASK",
            "task": new_task().model_dump(mode="json"),
        },
    )

    assert response.status_code == 200
    assert response.json()["command_type"] == "CREATE_TASK"
    assert "TASK_ASSIGNED" in response.json()["produced_event_types"]


def test_the_generic_command_endpoint_rejects_an_unknown_command(
    client: TestClient,
) -> None:
    response = client.post(
        f"{PREFIX}/commands",
        json={
            "command_id": str(uuid4()),
            "issued_at_s": 0.0,
            "command_type": "LAUNCH_MISSILES",
        },
    )

    assert response.status_code == 422


def test_the_endpoints_make_no_coordination_decisions(client: TestClient) -> None:
    """The HTTP layer is an adapter: it never builds events or picks a winner."""

    import ast
    import inspect

    from backend.app import api, composition as composition_module

    for module in (api, composition_module):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert not node.func.id.endswith("Payload"), (
                    f"{module.__name__} must not construct event payloads"
                )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert not node.func.attr.endswith("Payload"), (
                    f"{module.__name__} must not construct event payloads"
                )
        assert "EventEnvelope(" not in source, (
            f"{module.__name__} must not build event envelopes itself"
        )


def test_the_app_does_not_import_another_agents_internals(client: TestClient) -> None:
    """The composition root may depend on both agents; nothing else may."""

    import ast
    import inspect

    from backend.app import api, composition as composition_module
    from backend.simulation import runtime as runtime_module

    def imported_modules(module) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    agent_one = ("backend.negotiation", "backend.allocation")
    assert not any(
        name.startswith(agent_one) for name in imported_modules(runtime_module)
    )
    # The composition root is the one place that knows both agents exist.
    assert any(
        name.startswith(agent_one) for name in imported_modules(composition_module)
    )
    # The HTTP adapter itself only talks to the composition root.
    assert not any(
        name.startswith(agent_one) for name in imported_modules(api)
    )
