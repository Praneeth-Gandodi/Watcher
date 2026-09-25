import pytest
from pydantic import ValidationError

from backend.contracts.commands import parse_command
from backend.contracts.fixtures import valid_create_task_command


def test_create_task_command_round_trip() -> None:
    command = valid_create_task_command()
    assert parse_command(command.model_dump(mode="json")) == command


def test_simulation_speed_is_bounded() -> None:
    command = valid_create_task_command().model_dump(mode="json")
    command.update(
        command_type="SET_SIMULATION_SPEED",
        multiplier=25.0,
    )
    with pytest.raises(ValidationError):
        parse_command(command)
