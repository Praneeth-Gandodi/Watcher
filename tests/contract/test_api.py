"""Contract tests for the canonical HTTP and WebSocket API surface."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.service import SimulationService
from backend.contracts.commands import parse_command
from backend.contracts.events import parse_event
from backend.contracts.models import SimulationSnapshot, SystemMetrics
from backend.simulation.runtime import RuntimeConfig, SimulationRuntime

COMMAND_UUID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def service() -> SimulationService:
    """A service whose clock is effectively stopped.

    Contract tests assert exact shapes and counts, so the world must not drift
    underneath them. Tests that need progression drive the runtime directly.
    """

    runtime = SimulationRuntime(RuntimeConfig.for_fleet(8, initial_task_count=4))
    return SimulationService(runtime=runtime, tick_interval_s=3600.0)


@pytest.fixture
def client(service: SimulationService) -> Iterator[TestClient]:
    with TestClient(create_app(service)) as test_client:
        yield test_client


class TestHealth:
    def test_health_contract(self, client: TestClient) -> None:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "service": "watcher-backend",
            "version": "0.1.0",
        }


class TestSnapshot:
    def test_snapshot_is_a_valid_canonical_projection(self, client: TestClient) -> None:
        response = client.get("/api/v1/snapshot")
        assert response.status_code == 200
        snapshot = SimulationSnapshot.model_validate(response.json())
        assert len(snapshot.robots) == 8
        assert snapshot.last_event_sequence >= snapshot.revision

    def test_snapshot_reports_controller_availability(self, client: TestClient) -> None:
        assert "controller_available" in client.get("/api/v1/snapshot").json()

    def test_robots_endpoint_returns_robots(self, client: TestClient) -> None:
        body = client.get("/api/v1/robots").json()
        assert len(body["robots"]) == 8
        assert "last_event_sequence" in body

    def test_tasks_endpoint_returns_the_seeded_queue(self, client: TestClient) -> None:
        body = client.get("/api/v1/tasks").json()
        # The service ticks once at startup, so the queue holds at least the
        # seeded work; the contract under test is the shape, not the count.
        assert len(body["tasks"]) >= 4
        for task in body["tasks"]:
            assert task["status"]
            assert task["task_id"]

    def test_conflicts_endpoint_returns_a_list(self, client: TestClient) -> None:
        assert client.get("/api/v1/conflicts").json()["conflicts"] == []

    def test_metrics_match_the_canonical_model(self, client: TestClient) -> None:
        metrics = SystemMetrics.model_validate(client.get("/api/v1/metrics").json())
        assert metrics.controller_available is True

    def test_metrics_include_the_free_form_telemetry_map(self, client: TestClient) -> None:
        extra = client.get("/api/v1/metrics").json()["extra_metrics"]
        assert "fleet_size" in extra
        assert all(isinstance(value, (int, float)) for value in extra.values())


class TestEvents:
    def test_events_start_from_the_beginning(self, client: TestClient) -> None:
        body = client.get("/api/v1/events?after_sequence=0").json()
        assert body["events"]

    def test_every_event_parses_as_a_canonical_envelope(self, client: TestClient) -> None:
        for raw in client.get("/api/v1/events?after_sequence=0").json()["events"]:
            parse_event(raw)

    def test_cursor_filters_the_history(self, client: TestClient) -> None:
        everything = client.get("/api/v1/events?after_sequence=0").json()["events"]
        cursor = everything[len(everything) // 2]["sequence"]
        later = client.get(f"/api/v1/events?after_sequence={cursor}").json()["events"]
        assert all(event["sequence"] > cursor for event in later)

    def test_rejects_a_negative_cursor(self, client: TestClient) -> None:
        assert client.get("/api/v1/events?after_sequence=-1").status_code == 422

    def test_rejects_an_oversized_limit(self, client: TestClient) -> None:
        assert client.get("/api/v1/events?limit=5000").status_code == 422


class TestCommands:
    def test_accepts_a_pause_command(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/commands",
            json={
                "command_id": COMMAND_UUID,
                "schema_version": 1,
                "command_type": "PAUSE_SIMULATION",
                "issued_at_s": 0.0,
            },
        )
        assert response.status_code == 200
        assert response.json()["accepted"] is True

    def test_accepts_a_create_task_command(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/commands",
            json={
                "command_id": COMMAND_UUID,
                "schema_version": 1,
                "command_type": "CREATE_TASK",
                "issued_at_s": 0.0,
                "task": {
                    "task_id": "task-contract-01",
                    "target": {"x": 10.0, "y": 10.0},
                    "priority": 3,
                    "required_capabilities": ["transport"],
                    "estimated_duration_s": 30.0,
                    "status": "pending",
                    "assigned_robot_id": None,
                    "created_at_s": 0.0,
                },
            },
        )
        assert response.status_code == 200
        task_ids = [task["task_id"] for task in client.get("/api/v1/tasks").json()["tasks"]]
        assert "task-contract-01" in task_ids

    def test_rejects_an_unknown_command_type(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/commands",
            json={
                "command_id": COMMAND_UUID,
                "command_type": "LAUNCH_ROCKET",
                "issued_at_s": 0.0,
            },
        )
        assert response.status_code == 422

    def test_rejects_an_invalid_uuid(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/commands",
            json={
                "command_id": "not-a-uuid",
                "command_type": "PAUSE_SIMULATION",
                "issued_at_s": 0.0,
            },
        )
        assert response.status_code == 422

    def test_rejects_an_unknown_field(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/commands",
            json={
                "command_id": COMMAND_UUID,
                "command_type": "PAUSE_SIMULATION",
                "issued_at_s": 0.0,
                "surprise": True,
            },
        )
        assert response.status_code == 422

    def test_rejects_an_out_of_range_priority(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/commands",
            json={
                "command_id": COMMAND_UUID,
                "command_type": "CREATE_TASK",
                "issued_at_s": 0.0,
                "task": {
                    "task_id": "task-contract-02",
                    "target": {"x": 10.0, "y": 10.0},
                    "priority": 9,
                    "required_capabilities": [],
                    "estimated_duration_s": 30.0,
                    "status": "pending",
                    "assigned_robot_id": None,
                    "created_at_s": 0.0,
                },
            },
        )
        assert response.status_code == 422

    def test_rejects_a_speed_beyond_the_documented_ceiling(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/commands",
            json={
                "command_id": COMMAND_UUID,
                "command_type": "SET_SIMULATION_SPEED",
                "issued_at_s": 0.0,
                "multiplier": 50.0,
            },
        )
        assert response.status_code == 422

    def test_a_rejected_command_changes_nothing(self, client: TestClient) -> None:
        before = client.get("/api/v1/snapshot").json()["last_event_sequence"]
        client.post(
            "/api/v1/commands",
            json={"command_id": "nope", "command_type": "PAUSE_SIMULATION", "issued_at_s": 0.0},
        )
        after = client.get("/api/v1/snapshot").json()["last_event_sequence"]
        assert after == before

    def test_every_command_the_contract_defines_is_accepted(self, client: TestClient) -> None:
        robot_id = client.get("/api/v1/robots").json()["robots"][0]["robot_id"]
        payloads = [
            {
                "command_id": COMMAND_UUID,
                "command_type": "PAUSE_SIMULATION",
                "issued_at_s": 0.0,
            },
            {
                "command_id": COMMAND_UUID,
                "command_type": "RESUME_SIMULATION",
                "issued_at_s": 0.0,
            },
            {
                "command_id": COMMAND_UUID,
                "command_type": "SET_SIMULATION_SPEED",
                "issued_at_s": 0.0,
                "multiplier": 2.0,
            },
            {
                "command_id": COMMAND_UUID,
                "command_type": "RESET_SIMULATION",
                "issued_at_s": 0.0,
                "seed": 7,
            },
            {
                "command_id": COMMAND_UUID,
                "command_type": "INJECT_ROBOT_FAILURE",
                "issued_at_s": 0.0,
                "robot_id": robot_id,
                "failure": {
                    "kind": "actuator",
                    "code": "contract-test",
                    "detected_at_s": 0.0,
                    "detail": None,
                },
            },
            {
                "command_id": COMMAND_UUID,
                "command_type": "INJECT_COMMUNICATION_LOSS",
                "issued_at_s": 0.0,
                "robot_id": robot_id,
                "timeout_s": 3.0,
            },
            {
                "command_id": COMMAND_UUID,
                "command_type": "RESTORE_ROBOT",
                "issued_at_s": 0.0,
                "robot_id": robot_id,
            },
        ]
        for payload in payloads:
            parse_command(payload)
            assert client.post("/api/v1/commands", json=payload).status_code == 200


class TestEventStream:
    def test_stream_opens_with_a_snapshot_frame(self, client: TestClient) -> None:
        with client.websocket_connect("/api/v1/stream?after_sequence=0") as socket:
            frame = socket.receive_json()
        assert frame["kind"] == "snapshot"
        SimulationSnapshot.model_validate(frame["data"])

    def test_stream_emits_canonical_events(self, client: TestClient, service: SimulationService) -> None:
        with client.websocket_connect("/api/v1/stream?after_sequence=0") as socket:
            assert socket.receive_json()["kind"] == "snapshot"
            # Enough ticks for the world to produce new canonical events.
            for _ in range(12):
                service.runtime.tick()
            kinds = [socket.receive_json()["kind"] for _ in range(4)]
        assert "event" in kinds

    def test_stream_event_frames_validate(self, client: TestClient, service: SimulationService) -> None:
        with client.websocket_connect("/api/v1/stream?after_sequence=0") as socket:
            assert socket.receive_json()["kind"] == "snapshot"
            for _ in range(12):
                service.runtime.tick()
            for _ in range(6):
                frame = socket.receive_json()
                if frame["kind"] == "event":
                    parse_event(frame["data"])
                    return
        pytest.fail("no event frame arrived")

    def test_stream_sends_a_cursor_heartbeat_when_idle(
        self, client: TestClient, service: SimulationService
    ) -> None:
        with client.websocket_connect("/api/v1/stream?after_sequence=0") as socket:
            socket.receive_json()
            kinds = []
            for _ in range(4):
                frame = socket.receive_json()
                kinds.append(frame["kind"])
                if frame["kind"] == "cursor":
                    assert "last_event_sequence" in frame["data"]
                    return
        pytest.fail(f"no cursor frame arrived, saw {kinds}")

    def test_stream_honours_the_cursor(self, client: TestClient) -> None:
        cursor = client.get("/api/v1/snapshot").json()["last_event_sequence"]
        with client.websocket_connect(f"/api/v1/stream?after_sequence={cursor}") as socket:
            frame = socket.receive_json()
        assert frame["kind"] == "snapshot"

    def test_stream_tolerates_a_malformed_cursor(self, client: TestClient) -> None:
        with client.websocket_connect("/api/v1/stream?after_sequence=abc") as socket:
            assert socket.receive_json()["kind"] == "snapshot"
