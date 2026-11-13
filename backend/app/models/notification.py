"""
Notification preference model.
Stores per-user email notification settings.
"""

import uuid

from sqlalchemy import Boolean, ForeignKeyConstraint, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class NotificationPreference(Base, UUIDMixin, TimestampMixin):
    """Per-user notification preferences."""

    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False
    )

    # Event toggles
    notify_vm_error: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_vm_created: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_backup_completed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_backup_failed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_quota_warning: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_login_new_ip: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notify_2fa_changed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
