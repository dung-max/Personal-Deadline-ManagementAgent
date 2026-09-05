"""Task and Reminder domain models.

Provides the Task and Reminder SQLAlchemy models and associated enums for the
task and reminder domains. Inherits id, created_at from genai-core Base.
Overrides updated_at to be NOT NULL.
"""

from __future__ import annotations

import enum
import uuid

from datetime import datetime, timezone
from sqlalchemy import UUID, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from genai_core.genai_shared.database import Base


class TaskPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TaskStatus(str, enum.Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class Task(Base):
    __tablename__ = "tasks"

    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    priority: Mapped[str] = mapped_column(
        String, nullable=False, default=TaskPriority.MEDIUM.value
    )
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=TaskStatus.TODO.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    reminders: Mapped[list["Reminder"]] = relationship(
        back_populates="task",
        passive_deletes="all",
    )

    __table_args__ = (
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_deadline", "deadline"),
    )


# --- Reminder ---


class ReminderStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    CANCELLED = "CANCELLED"


class Reminder(Base):
    __tablename__ = "reminders"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    remind_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=ReminderStatus.PENDING.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_reminders_task_id", "task_id"),
        Index("ix_reminders_remind_at", "remind_at"),
    )

    task: Mapped["Task"] = relationship(back_populates="reminders")
