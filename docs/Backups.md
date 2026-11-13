# Backups

QEMU Interface supports **live VM backups**, scheduled retention, optional **FTP/S3** offload, **database dumps**, and a Superadmin inventory of all VM backups by location.

## VM backups (create / restore)

| Operation | Running VM | Stopped VM |
|---|---|---|
| **Create backup** | Supported (live) | Supported (cold copy) |
| **Scheduled backup** | Supported (live) | Supported |
| **Restore** | Not allowed – stop the VM first | Supported |

### Live backup (running guests)

When the guest is running, the Host Agent:

1. Creates a temporary **external snapshot** (overlay) so the base disk is briefly quiescent for copy
2. Copies / converts the base disk to a compressed qcow2 backup file
3. Runs **blockcommit + pivot** to merge the overlay back and continue on the original disk

The guest stays **running** for the whole create path. Temporary `qi-bak*` overlays are cleaned up after a successful backup.

Cold backups (stopped VMs) still use a direct disk copy without the overlay path.

### Restore

Restore replaces the VM disk from a backup file. The VM **must be stopped**. A safety copy of the current disk is kept for rollback if restore fails.

### Per-VM UI

On the VM detail page:

- Create / list / delete backups
- Restore (when stopped)
- Configure schedules (daily / weekly / monthly + retention)
- Backup History auto-refreshes when an active schedule exists (so scheduled runs appear without a full page reload)

## Storage backends (Local / FTP / S3)

Superadmins configure where finished VM backups are stored under **Admin → Backups → VM backup storage**:

| Backend | Meaning |
|---|---|
| **internal (Local)** | Files under `BACKUP_PATH` on the host (default `/var/lib/qemu-interface/backups`) |
| **FTP** | Upload to an FTP/FTPS server after create |
| **S3** | Upload to S3-compatible object storage (AWS, MinIO, …) |

Optional **keep local copy**: when using FTP/S3, keep the file on the host as well (shown as both `local` + `ftp`/`s3` in the inventory).

Connection test is available in the same UI before saving.

## Global VM backup inventory

**Admin → Backups → VM backups** lists backups across all tenants with:

- Counts for Total / Local / FTP / S3
- Filters: All · Local · FTP · S3
- Location badges, VM name, tenant, status, size, created time
- **Superadmin Delete**: removes local/remote artifacts and the same `vm_backups` row (also disappears from the VM detail Backup History)

API: `DELETE /system/vm-backups/{backup_id}` (superadmin only).

## Database backups

Superadmins can dump and restore the panel PostgreSQL database under **Admin → Backups → Database backups**:

- Create named dumps (`pg_dump`)
- Download / export dump files
- Import a dump (`pg_restore` / compatible restore path)
- Delete old dumps

Use DB backups before upgrades or major config changes. Treat dump files as sensitive (contain user/tenant data).

## Schedules & retention

- Frequencies: daily, weekly (weekday), monthly (day of month)
- **UI time is local wall-clock** (browser timezone); the API still stores `time_utc`
- Audit log descriptions that say `at HH:MM UTC` are displayed in local time
- Retention: keep the N newest backups per schedule; older ones are deleted automatically
- Schedules run via Celery Beat (worker with `-B`)
- Running VMs are **not** skipped anymore – schedules create live backups

## API (summary)

### Tenant VM backups

```
GET    /tenants/{tid}/vms/{id}/backups
POST   /tenants/{tid}/vms/{id}/backups
POST   /tenants/{tid}/vms/{id}/backups/{bid}/restore
DELETE /tenants/{tid}/vms/{id}/backups/{bid}
```

### Schedules

```
GET    /tenants/{tid}/vms/{id}/backup-schedules
POST   /tenants/{tid}/vms/{id}/backup-schedules
PATCH  /tenants/{tid}/vms/{id}/backup-schedules/{sid}
DELETE /tenants/{tid}/vms/{id}/backup-schedules/{sid}
```

### Superadmin inventory & storage

```
GET  /system/vm-backups              → List all VM backups (?backend=internal|ftp|s3|local)
GET  /system/vm-backups/summary      → Counts by backend / status
GET  /system/backup-storage          → Get Local/FTP/S3 config (secrets masked)
PUT  /system/backup-storage          → Update backend config
POST /system/backup-storage/test     → Test connectivity

GET    /system/db-backups
POST   /system/db-backups
GET    /system/db-backups/{id}/download
POST   /system/db-backups/import
DELETE /system/db-backups/{id}
```

## Host paths

| Path | Purpose |
|---|---|
| `/var/lib/qemu-interface/backups` | Default local VM backup directory (`BACKUP_PATH`) |
| DB dump location | Managed by the backend service (see Admin UI / dump metadata) |

## Operational tips

1. Prefer **scheduled backups** plus at least one off-host target (FTP or S3) for disaster recovery
2. Test **restore** on a non-critical VM before relying on a schedule in production
3. Live backups need free space for the temporary overlay and the backup file
4. After changing FTP/S3 credentials, use **Test** in the Admin UI
5. Keep panel **database dumps** separate from VM disk backups – both are needed for full recovery
