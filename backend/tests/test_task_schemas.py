"""Hermetic, no-network unit tests for the Pydantic task schemas -- these
cover the "validation errors" requirement without needing a database."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.task import TaskCreate, TaskStatus, TaskUpdate


def test_create_task_requires_non_blank_title():
    with pytest.raises(ValidationError):
        TaskCreate(title="   ")


def test_create_task_requires_a_title():
    with pytest.raises(ValidationError):
        TaskCreate()  # type: ignore[call-arg]


def test_create_task_rejects_non_positive_estimate():
    with pytest.raises(ValidationError):
        TaskCreate(title="Write report", estimated_minutes=0)
    with pytest.raises(ValidationError):
        TaskCreate(title="Write report", estimated_minutes=-5)


def test_create_task_rejects_unknown_status():
    with pytest.raises(ValidationError):
        TaskCreate(title="Write report", status="not-a-real-status")


def test_create_task_defaults_status_to_pending():
    task = TaskCreate(title="Write report")
    assert task.status == TaskStatus.pending
    assert task.description is None


def test_create_task_strips_title_whitespace():
    task = TaskCreate(title="  Write report  ")
    assert task.title == "Write report"


def test_update_task_only_includes_fields_that_were_set():
    """PATCH semantics: an omitted field must not appear in the dict that
    the service layer turns into a SQL SET clause, but an explicit null
    must -- that's how a client clears due_at."""
    update = TaskUpdate(title="New title")
    dumped = update.model_dump(exclude_unset=True)
    assert dumped == {"title": "New title"}

    clear_due_date = TaskUpdate(due_at=None)
    dumped_clear = clear_due_date.model_dump(exclude_unset=True)
    assert dumped_clear == {"due_at": None}

    empty_update = TaskUpdate()
    assert empty_update.model_dump(exclude_unset=True) == {}


def test_update_task_rejects_blank_title():
    with pytest.raises(ValidationError):
        TaskUpdate(title="   ")


def test_update_task_rejects_non_positive_estimate():
    with pytest.raises(ValidationError):
        TaskUpdate(estimated_minutes=0)
