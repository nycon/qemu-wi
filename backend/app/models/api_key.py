"""
API Key model for programmatic/external API access.
"""

import uuid

from sqlalchemy import Boolean, DateTime, ForeignKeyConstraint, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class APIKey(Base, UUIDMixin, TimestampMixin):
    """
    Long-lived API keys for external/programmatic access.

    Security:
    - Only the hash of the key is stored (never plaintext)
    - The plaintext key is shown exactly once at creation
    - Keys can be scoped to specific tenants or be global (superadmin)
    - Keys can be revoked (is_active=False) or expire (expires_at)
    """

    __tablename__ = "api_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Store only the hash – key_prefix stores first 8 chars for identification
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)

    # Optional: scope key to a specific tenant (None = all tenants the user has access to)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User")

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
    )
