"""Database schema.

Every table that holds user data has a NOT NULL `user_id` that cascades on delete, so
per-user isolation (Day 20) can be enforced by filtering on one column, and deleting a user
removes everything they own.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base

# JSONB on PostgreSQL (compact, indexable); plain JSON elsewhere (SQLite in tests).
JsonType = JSON().with_variant(JSONB(), "postgresql")


def _user_fk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)  # never the password
    created_at: Mapped[datetime] = _created_at()


# "A@x.com" and "a@x.com" are the same person, so uniqueness ignores case.
Index("uq_users_email_lower", func.lower(User.email), unique=True)


class Model(Base):
    """A model the user attached. Today only API endpoints; uploads arrive in Phase 5."""

    __tablename__ = "models"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_models_id_user"),  # target for owner-checked FKs
        CheckConstraint("kind IN ('endpoint', 'upload')", name="ck_models_kind"),
        # An endpoint model must say where and what to call.
        CheckConstraint(
            "kind <> 'endpoint' OR (url IS NOT NULL AND model_name IS NOT NULL)",
            name="ck_models_endpoint_fields",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="endpoint")
    url: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(200))
    # Stored encrypted, never plaintext; the encryption itself is applied by the code that
    # writes this column when the routers move onto the database.
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()


class TestSet(Base):
    __tablename__ = "test_sets"
    __test__ = False  # not a pytest class

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # The CSV text itself (capped at 5 MB by the API). Moves to object storage in Phase 5.
    csv_text: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str | None] = mapped_column(String(255))
    classes: Mapped[dict | None] = mapped_column(JsonType)  # {label: count}
    leakage: Mapped[dict | None] = mapped_column(JsonType)
    evaluation: Mapped[dict | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = _created_at()


class TrainingLog(Base):
    __tablename__ = "training_logs"
    __table_args__ = (CheckConstraint("source IN ('csv', 'mlflow')", name="ck_training_logs_source"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    x_name: Mapped[str] = mapped_column(String(50), nullable=False)
    x: Mapped[list] = mapped_column(JsonType, nullable=False)
    series: Mapped[dict] = mapped_column(JsonType, nullable=False)  # {name: [values or null]}
    train_loss: Mapped[str | None] = mapped_column(String(255))
    val_loss: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _created_at()


class Report(Base):
    """A saved snapshot, so scores stay comparable across runs.

    `payload` holds the readiness result plus what produced it (prompt template, sample size,
    seed, thresholds), per the "reproducibility" rule in the README.
    """

    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="ck_reports_score"),
        Index("ix_reports_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    # Deleting a model or data set keeps the report; the snapshot in `payload` is self-contained.
    model_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("models.id", ondelete="SET NULL"))
    test_set_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("test_sets.id", ondelete="SET NULL"))
    training_log_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("training_logs.id", ondelete="SET NULL")
    )
    score: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_chat_sessions_id_user"),
        # (model_id, user_id) must match a model owned by the same user: a session can't chat
        # with someone else's model.
        ForeignKeyConstraint(
            ["model_id", "user_id"], ["models.id", "models.user_id"],
            ondelete="CASCADE", name="fk_chat_sessions_model_owner",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created_at()

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.position",
        passive_deletes=True,
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('system', 'user', 'assistant')", name="ck_chat_messages_role"),
        Index("uq_chat_messages_session_position", "session_id", "position", unique=True),
        # (session_id, user_id) must match a session owned by the same user, so a message can
        # never belong to a different user than its session.
        ForeignKeyConstraint(
            ["session_id", "user_id"], ["chat_sessions.id", "chat_sessions.user_id"],
            ondelete="CASCADE", name="fk_chat_messages_session_owner",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # user_id is repeated (not just reachable via the session) so a query can be filtered by
    # owner without a join.
    user_id: Mapped[uuid.UUID] = _user_fk()
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)  # order within the session
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    session: Mapped[ChatSession] = relationship(back_populates="messages")
