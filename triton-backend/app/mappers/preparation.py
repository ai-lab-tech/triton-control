"""Pure preparation entity to API mapping."""

from app.db.entities import PreparationAttemptEntity, PreparationTaskEntity, PreparationTaskEventEntity
from app.schemas.preparation import AttemptDTO, TaskDTO, TaskEventDTO


def task_to_dto(task: PreparationTaskEntity, blocked_reasons: list[str]) -> TaskDTO:
    return TaskDTO.model_validate({**task.model_dump(), "blocked_reasons": blocked_reasons})


def event_to_dto(event: PreparationTaskEventEntity) -> TaskEventDTO:
    return TaskEventDTO.model_validate(event.model_dump())


def attempt_to_dto(attempt: PreparationAttemptEntity) -> AttemptDTO:
    return AttemptDTO.model_validate(attempt.model_dump())
