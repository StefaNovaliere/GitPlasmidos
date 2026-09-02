"""SQLAlchemy 2.x tables.

Only the *base* sequence, the *base* features and the append-only operation log
are persisted. The current state is always derived by
:func:`app.domain.replay.replay`, never stored.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Construct(Base):
    __tablename__ = "constructs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Free text: the GenBank DEFINITION line on import, or a note explaining
    #: what a construct is for.
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_circular: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    base_sequence: Mapped[str] = mapped_column(Text, default="", nullable=False)
    base_features: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    #: Set when this construct was forked off another. A branch shares its
    #: parent's base sequence and the first ``fork_index`` of its operations,
    #: which is what makes the two logs mergeable.
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("constructs.id", ondelete="SET NULL"), nullable=True
    )
    fork_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    branches: Mapped[list[Construct]] = relationship(
        back_populates="parent", cascade="save-update", lazy="selectin"
    )
    parent: Mapped[Construct | None] = relationship(
        back_populates="branches", remote_side="Construct.id"
    )

    operations: Mapped[list[OperationRow]] = relationship(
        back_populates="construct",
        cascade="all, delete-orphan",
        order_by="OperationRow.index",
        lazy="selectin",
    )


class OperationRow(Base):
    """One entry of the append-only log.

    ``reverted`` implements soft undo: rows are kept so the history stays
    auditable. Reverted rows always form a suffix of the log, and are deleted
    outright when a new operation is applied on top of them (text-editor
    semantics, not git).
    """

    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("construct_id", "index", name="uq_operation_index"),
        Index("ix_operations_construct", "construct_id", "index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    construct_id: Mapped[str] = mapped_column(
        ForeignKey("constructs.id", ondelete="CASCADE"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reverted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    construct: Mapped[Construct] = relationship(back_populates="operations")
