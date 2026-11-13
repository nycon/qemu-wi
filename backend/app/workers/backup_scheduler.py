"""
Celery periodic tasks for scheduled backups and retention cleanup.

- check_backup_schedules: Runs every 60s, finds due schedules, triggers backups
- cleanup_old_backups: Runs every hour, enforces retention_count per schedule
"""

import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, func

from app.core.agent_http import AGENT_SSL_VERIFY
from app.core.config import settings
from app.workers.celery_app import celery_app

AGENT_BASE_URL = settings.HOST_AGENT_URL
AGENT_HEADERS = {"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"}


def _get_sync_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    return Session(engine)


def _compute_next_run(frequency: str, time_utc: str, day_of_week=None, day_of_month=None):
    """Compute the next run datetime from now."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    hour, minute = int(time_utc.split(":")[0]), int(time_utc.split(":")[1])
    target_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if frequency == "daily":
        return target_today + timedelta(days=1) if target_today <= now else target_today

    elif frequency == "weekly":
        dow = day_of_week if day_of_week is not None else 0
        days_ahead = dow - now.weekday()
        if days_ahead < 0 or (days_ahead == 0 and target_today <= now):
            days_ahead += 7
        return target_today + timedelta(days=days_ahead)

    elif frequency == "monthly":
        dom = day_of_month if day_of_month is not None else 1
        try:
            target = now.replace(day=dom, hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            target = now.replace(day=28, hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            month = target.month + 1
            year = target.year
            if month > 12:
                month = 1
                year += 1
            try:
                target = target.replace(year=year, month=month, day=dom)
            except ValueError:
                target = target.replace(year=year, month=month, day=28)
        return target

    return target_today + timedelta(days=1)


@celery_app.task(name="scheduler.check_backup_schedules")
def check_backup_schedules():
    """Check for due backup schedules and trigger backups."""
    from app.models.backup import BackupSchedule, VMBackup, BackupStatus, BackupType
    from app.models.vm import VirtualMachine, VMStatus
    from app.models.task import Task, TaskStatus, TaskType

    session = _get_sync_session()
    now = datetime.now(timezone.utc)

    try:
        # Find active schedules that are due
        result = session.execute(
            select(BackupSchedule).where(
                BackupSchedule.is_active.is_(True),
                BackupSchedule.next_run_at <= now,
            )
        )
        schedules = list(result.scalars().all())

        if not schedules:
            return {"checked": 0, "triggered": 0}

        triggered = 0
        for schedule in schedules:
            try:
                vm = session.get(VirtualMachine, schedule.vm_id)
                if not vm or vm.is_deleted:
                    schedule.is_active = False
                    schedule.last_status = "vm_deleted"
                    session.commit()
                    continue

                # Offline or live backup (running VMs use temporary disk snapshot)
                if vm.status not in (VMStatus.STOPPED, VMStatus.RUNNING):
                    print(f"[Scheduler] Skipping backup for VM {vm.name} – status is {vm.status.value}")
                    schedule.last_run_at = now
                    schedule.last_status = f"skipped_vm_{vm.status.value}"
                    freq = schedule.frequency.value if hasattr(schedule.frequency, 'value') else schedule.frequency
                    schedule.next_run_at = _compute_next_run(
                        freq, schedule.time_utc, schedule.day_of_week, schedule.day_of_month
                    )
                    session.commit()
                    continue

                # Create backup record
                timestamp = now.strftime("%Y%m%d-%H%M")
                freq_label = schedule.frequency.value if hasattr(schedule.frequency, 'value') else schedule.frequency
                mode_note = "live" if vm.status == VMStatus.RUNNING else "offline"
                backup_name = f"auto-{freq_label}-{timestamp}"

                backup = VMBackup(
                    vm_id=vm.id,
                    tenant_id=schedule.tenant_id,
                    created_by=schedule.created_by,
                    name=backup_name,
                    description=f"Scheduled {freq_label} backup ({mode_note})",
                    backup_type=BackupType.FULL,
                    status=BackupStatus.CREATING,
                    vm_name=vm.name,
                    vm_vcpus=vm.vcpus,
                    vm_ram_mb=vm.ram_mb,
                    vm_disk_size_gb=vm.disk_size_gb,
                    schedule_id=schedule.id,
                )
                session.add(backup)
                session.flush()

                # Create task
                task = Task(
                    task_type=TaskType.BACKUP_CREATE,
                    status=TaskStatus.QUEUED,
                    user_id=schedule.created_by,
                    tenant_id=schedule.tenant_id,
                    resource_type="vm_backup",
                    resource_id=backup.id,
                )
                session.add(task)
                session.flush()

                # Launch Celery task
                from app.workers.vm_tasks import task_vm_backup
                task_vm_backup.delay(str(vm.id), str(backup.id), str(task.id))

                # Update schedule
                schedule.last_run_at = now
                schedule.last_status = "triggered"
                schedule.next_run_at = _compute_next_run(
                    freq_label, schedule.time_utc, schedule.day_of_week, schedule.day_of_month
                )
                session.commit()
                triggered += 1

                print(f"[Scheduler] Triggered backup '{backup_name}' for VM '{vm.name}'")

            except Exception as e:
                session.rollback()
                print(f"[Scheduler] Error processing schedule {schedule.id}: {e}")
                try:
                    schedule.last_status = "error"
                    freq = schedule.frequency.value if hasattr(schedule.frequency, 'value') else schedule.frequency
                    schedule.next_run_at = _compute_next_run(
                        freq, schedule.time_utc, schedule.day_of_week, schedule.day_of_month
                    )
                    session.commit()
                except Exception:
                    session.rollback()

        return {"checked": len(schedules), "triggered": triggered}

    except Exception as e:
        print(f"[Scheduler] Fatal error: {e}")
        session.rollback()
        return {"error": str(e)}
    finally:
        session.close()


@celery_app.task(name="scheduler.cleanup_old_backups")
def cleanup_old_backups():
    """Enforce retention policy: delete oldest backups exceeding retention_count."""
    from app.models.backup import BackupSchedule, VMBackup, BackupStatus

    session = _get_sync_session()
    deleted_count = 0

    try:
        # Find all active schedules
        result = session.execute(
            select(BackupSchedule).where(BackupSchedule.is_active.is_(True))
        )
        schedules = list(result.scalars().all())

        for schedule in schedules:
            try:
                # Get all ready backups for this schedule, ordered by creation date
                backup_result = session.execute(
                    select(VMBackup).where(
                        VMBackup.schedule_id == schedule.id,
                        VMBackup.status == BackupStatus.READY,
                    ).order_by(VMBackup.created_at.desc())
                )
                backups = list(backup_result.scalars().all())

                if len(backups) <= schedule.retention_count:
                    continue

                # Delete excess backups (oldest first)
                excess = backups[schedule.retention_count:]
                for old_backup in excess:
                    # Delete file via Host Agent
                    if old_backup.file_path:
                        try:
                            import httpx as hx
                            with hx.Client(verify=AGENT_SSL_VERIFY, timeout=30) as client:
                                client.delete(
                                    f"{AGENT_BASE_URL}/api/v1/backups/{old_backup.file_path.split('/')[-1]}",
                                    headers=AGENT_HEADERS,
                                )
                        except Exception:
                            pass  # Best effort

                    if old_backup.remote_path and (old_backup.storage_backend or "") in ("ftp", "s3"):
                        try:
                            from app.services.backup_storage import (
                                delete_remote_backup,
                                get_backup_storage_config_sync,
                            )
                            cfg = get_backup_storage_config_sync(session)
                            delete_remote_backup(cfg, old_backup.remote_path)
                        except Exception:
                            pass

                    session.delete(old_backup)
                    deleted_count += 1
                    print(f"[Retention] Deleted old backup '{old_backup.name}' (schedule {schedule.id})")

                session.commit()

            except Exception as e:
                session.rollback()
                print(f"[Retention] Error for schedule {schedule.id}: {e}")

        return {"deleted": deleted_count}

    except Exception as e:
        print(f"[Retention] Fatal error: {e}")
        return {"error": str(e)}
    finally:
        session.close()
