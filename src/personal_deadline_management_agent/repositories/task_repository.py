"""Task repository.

Persistence layer for Task entities using SQLAlchemy Session.
Transaction ownership remains with UnitOfWork; this repository does NOT commit or rollback.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Task


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, task: Task) -> Task:
        self._session.add(task)
        self._session.flush()
        self._session.refresh(task)
        return task

    def get_by_id(self, task_id: UUID) -> Task | None:
        return self._session.get(Task, task_id)

    def list(self) -> list[Task]:
        stmt = select(Task)
        return list(self._session.scalars(stmt).all())

    def update(self, task: Task) -> Task:
        merged = self._session.merge(task)
        self._session.flush()
        self._session.refresh(merged)
        return merged

    def delete(self, task_id: UUID) -> bool:
        task = self.get_by_id(task_id)
        if task is None:
            return False
        self._session.delete(task)
        self._session.flush()
        return True
