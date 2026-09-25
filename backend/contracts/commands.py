"""Canonical user and fault-injection commands."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import Field, TypeAdapter, model_validator

from .models import ContractModel, FailureInfo, Task


class CommandType(StrEnum):
    CREATE_TASK = "CREATE_TASK"
    INJECT_ROBOT_FAILURE = "INJECT_ROBOT_FAILURE"
    INJECT_COMMUNICATION_LOSS = "INJECT_COMMUNICATION_LOSS"
    RESTORE_ROBOT = "RESTORE_ROBOT"
    PAUSE_SIMULATION = "PAUSE_SIMULATION"
    RESUME_SIMULATION = "RESUME_SIMULATION"
    RESET_SIMULATION = "RESET_SIMULATION"
    SET_SIMULATION_SPEED = "SET_SIMULATION_SPEED"


class ControlCommand(ContractModel):
    command_id: UUID
    schema_version: Literal[1] = 1
    command_type: CommandType
    issued_at_s: float = Field(ge=0)


class CreateTaskCommand(ControlCommand):
    command_type: Literal[CommandType.CREATE_TASK] = CommandType.CREATE_TASK
    task: Task


class InjectRobotFailureCommand(ControlCommand):
    command_type: Literal[CommandType.INJECT_ROBOT_FAILURE] = CommandType.INJECT_ROBOT_FAILURE
    robot_id: str
    failure: FailureInfo


class InjectCommunicationLossCommand(ControlCommand):
    command_type: Literal[CommandType.INJECT_COMMUNICATION_LOSS] = CommandType.INJECT_COMMUNICATION_LOSS
    robot_id: str
    timeout_s: float = Field(gt=0)


class RestoreRobotCommand(ControlCommand):
    command_type: Literal[CommandType.RESTORE_ROBOT] = CommandType.RESTORE_ROBOT
    robot_id: str


class PauseSimulationCommand(ControlCommand):
    command_type: Literal[CommandType.PAUSE_SIMULATION] = CommandType.PAUSE_SIMULATION


class ResumeSimulationCommand(ControlCommand):
    command_type: Literal[CommandType.RESUME_SIMULATION] = CommandType.RESUME_SIMULATION


class ResetSimulationCommand(ControlCommand):
    command_type: Literal[CommandType.RESET_SIMULATION] = CommandType.RESET_SIMULATION
    seed: int


class SetSimulationSpeedCommand(ControlCommand):
    command_type: Literal[CommandType.SET_SIMULATION_SPEED] = CommandType.SET_SIMULATION_SPEED
    multiplier: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def validate_multiplier(self) -> SetSimulationSpeedCommand:
        if self.multiplier > 10:
            # Ten times real time is sufficient for accelerated demos and
            # avoids accidental overload of the simulation loop.
            raise ValueError("simulation speed multiplier must not exceed 10")
        return self


ControlCommandUnion: TypeAlias = Annotated[
    CreateTaskCommand
    | InjectRobotFailureCommand
    | InjectCommunicationLossCommand
    | RestoreRobotCommand
    | PauseSimulationCommand
    | ResumeSimulationCommand
    | ResetSimulationCommand
    | SetSimulationSpeedCommand,
    Field(discriminator="command_type"),
]

_COMMAND_ADAPTER = TypeAdapter(ControlCommandUnion)


def parse_command(data: object) -> ControlCommandUnion:
    """Validate an untrusted serialized command against the canonical union."""

    return _COMMAND_ADAPTER.validate_python(data)
