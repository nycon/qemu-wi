import { useEffect, useMemo, useState } from 'react';
import {
  ArrowDownTrayIcon,
  ArrowPathIcon,
  ArrowUpTrayIcon,
  CircleStackIcon,
  CloudArrowUpIcon,
  PlusIcon,
  ServerStackIcon,
  TrashIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { systemApi } from '@/services/api';
import { useAuthStore } from '@/store/authStore';

type Tab = 'vms' | 'database' | 'storage';
type BackendFilter = 'all' | 'local' | 'ftp' | 's3';

interface DbBackup {
  id: string;
  name: string;
  status: string;
  file_path: string | null;
  file_size_bytes: number | null;
  notes: string | null;
  error_message: string | null;
  created_at: string;
}

interface VmBackupRow {
  id: string;
  name: string;
  status: string;
  vm_id: string;
  vm_name: string | null;
  tenant_id: string;
  tenant_name: string | null;
  storage_backend: string;
  file_path: string | null;
  remote_path: string | null;
  has_local: boolean;
  has_remote: boolean;
  locations: string[];
  file_size_bytes: number | null;
  error_message: string | null;
  created_at: string;
}

function formatBytes(n: number | null | undefined) {
  if (!n) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function locationBadge(loc: string) {
  const styles: Record<string, string> = {
    local: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
    ftp: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
    s3: 'bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-300',
  };
  const label = loc === 'local' ? 'Local' : loc.toUpperCase();
  return (
    <span key={loc} className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${styles[loc] || 'bg-gray-100 text-gray-700'}`}>
      {label}
    </span>
  );
}

export function BackupsPage() {
  const isSuperadmin = useAuthStore((s) => s.user?.is_superadmin === true);
  const [tab, setTab] = useState<Tab>('vms');
  const [backups, setBackups] = useState<DbBackup[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [notes, setNotes] = useState('');
  const [importing, setImporting] = useState(false);
  const [deletingVmBackupId, setDeletingVmBackupId] = useState<string | null>(null);

  const [vmBackups, setVmBackups] = useState<VmBackupRow[]>([]);
  const [vmLoading, setVmLoading] = useState(true);
  const [backendFilter, setBackendFilter] = useState<BackendFilter>('all');
  const [vmSummary, setVmSummary] = useState<{
    total: number;
    by_backend: { internal: number; ftp: number; s3: number; other: number };
    with_local: number;
    with_remote: number;
    ready: number;
    failed: number;
  } | null>(null);

  const [storage, setStorage] = useState({
    backend: 'internal' as 'internal' | 'ftp' | 's3',
    keep_local_copy: true,
    ftp: {
      host: '',
      port: 21,
      username: '',
      password: '',
      path: '/vm-backups',
      use_tls: false,
      password_set: false,
    },
    s3: {
      endpoint_url: '',
      region: 'eu-central-1',
      bucket: '',
      access_key: '',
      secret_key: '',
      prefix: 'vm-backups/',
      secret_key_set: false,
    },
  });
  const [savingStorage, setSavingStorage] = useState(false);

  const loadBackups = async () => {
    setLoading(true);
    try {
      setBackups(await systemApi.listDbBackups());
    } catch {
      toast.error('DB backups could not be loaded');
    } finally {
      setLoading(false);
    }
  };

  const loadVmBackups = async (filter: BackendFilter = backendFilter) => {
    setVmLoading(true);
    try {
      const params = filter === 'all' ? undefined : { backend: filter === 'local' ? 'internal' : filter };
      const [list, summary] = await Promise.all([
        systemApi.listVmBackupsAdmin(params),
        systemApi.vmBackupsSummary(),
      ]);
      setVmBackups(list);
      setVmSummary(summary);
    } catch {
      toast.error('VM backups could not be loaded');
    } finally {
      setVmLoading(false);
    }
  };

  const loadStorage = async () => {
    try {
      const cfg = await systemApi.getBackupStorage();
      setStorage({
        backend: cfg.backend,
        keep_local_copy: cfg.keep_local_copy,
        ftp: {
          host: String(cfg.ftp.host || ''),
          port: Number(cfg.ftp.port || 21),
          username: String(cfg.ftp.username || ''),
          password: '',
          path: String(cfg.ftp.path || '/vm-backups'),
          use_tls: Boolean(cfg.ftp.use_tls),
          password_set: Boolean(cfg.ftp.password_set),
        },
        s3: {
          endpoint_url: String(cfg.s3.endpoint_url || ''),
          region: String(cfg.s3.region || 'eu-central-1'),
          bucket: String(cfg.s3.bucket || ''),
          access_key: String(cfg.s3.access_key || ''),
          secret_key: '',
          prefix: String(cfg.s3.prefix || 'vm-backups/'),
          secret_key_set: Boolean(cfg.s3.secret_key_set),
        },
      });
    } catch {
      toast.error('Backup storage config could not be loaded');
    }
  };

  useEffect(() => {
    loadVmBackups('all');
    loadBackups();
    loadStorage();
  }, []);

  const filteredCountLabel = useMemo(() => {
    if (!vmSummary) return '';
    return `${vmSummary.total} total · Local ${vmSummary.by_backend.internal} · FTP ${vmSummary.by_backend.ftp} · S3 ${vmSummary.by_backend.s3}`;
  }, [vmSummary]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    try {
      await systemApi.createDbBackup({ name: name.trim(), notes: notes.trim() || undefined });
      toast.success('Database backup created');
      setName('');
      setNotes('');
      loadBackups();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Create failed';
      toast.error(msg);
    } finally {
      setCreating(false);
    }
  };

  const handleDownload = async (b: DbBackup) => {
    try {
      const blob = await systemApi.downloadDbBackup(b.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${b.name.replace(/\s+/g, '-')}.dump`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success('Download started');
    } catch {
      toast.error('Download failed');
    }
  };

  const handleDelete = async (b: DbBackup) => {
    if (!confirm(`Delete DB backup "${b.name}"?`)) return;
    try {
      await systemApi.deleteDbBackup(b.id);
      toast.success('Backup deleted');
      loadBackups();
    } catch {
      toast.error('Delete failed');
    }
  };

  const handleDeleteVmBackup = async (b: VmBackupRow) => {
    if (!isSuperadmin) return;
    if (!confirm(`Delete VM backup "${b.name}" (${b.vm_name || b.vm_id})?\n\nLocal and remote copies (FTP/S3) will be removed if present.`)) {
      return;
    }
    setDeletingVmBackupId(b.id);
    try {
      await systemApi.deleteVmBackupAdmin(b.id);
      toast.success('VM backup deleted');
      await Promise.all([loadVmBackups(backendFilter), loadVmSummary()]);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(typeof msg === 'string' ? msg : 'Delete failed');
    } finally {
      setDeletingVmBackupId(null);
    }
  };

  const handleImportFile = async (file: File, mode: 'validate' | 'restore') => {
    if (mode === 'restore') {
      const ok = confirm(
        'WARNING: Restoring a database dump will overwrite the current database. Continue?',
      );
      if (!ok) return;
    }
    setImporting(true);
    try {
      const result = await systemApi.importDbBackup(file, {
        validate_only: mode === 'validate',
        confirm: mode === 'restore',
      });
      if (mode === 'validate') {
        toast.success(`Dump valid (${result.toc_lines ?? 0} TOC lines)`);
      } else {
        toast.success('Database restored from dump');
        loadBackups();
      }
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Import failed';
      toast.error(msg);
    } finally {
      setImporting(false);
    }
  };

  const handleSaveStorage = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingStorage(true);
    try {
      await systemApi.updateBackupStorage({
        backend: storage.backend,
        keep_local_copy: storage.keep_local_copy,
        ftp: {
          host: storage.ftp.host,
          port: storage.ftp.port,
          username: storage.ftp.username,
          password: storage.ftp.password || undefined,
          path: storage.ftp.path,
          use_tls: storage.ftp.use_tls,
        },
        s3: {
          endpoint_url: storage.s3.endpoint_url,
          region: storage.s3.region,
          bucket: storage.s3.bucket,
          access_key: storage.s3.access_key,
          secret_key: storage.s3.secret_key || undefined,
          prefix: storage.s3.prefix,
        },
      });
      toast.success('Backup storage saved');
      loadStorage();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Save failed';
      toast.error(msg);
    } finally {
      setSavingStorage(false);
    }
  };

  const handleTestStorage = async () => {
    try {
      const result = await systemApi.testBackupStorage({
        backend: storage.backend,
        keep_local_copy: storage.keep_local_copy,
        ftp: storage.ftp,
        s3: storage.s3,
      });
      toast.success(result.message || 'Connection OK');
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Test failed';
      toast.error(msg);
    }
  };

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Backups</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          VM backup inventory (Local / FTP / S3), database dumps and storage backends
        </p>
      </div>

      <div className="flex flex-wrap gap-2 mb-6">
        <button
          type="button"
          onClick={() => setTab('vms')}
          className={`px-4 py-2 rounded-lg text-sm font-medium ${tab === 'vms' ? 'bg-primary-600 text-white' : 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300'}`}
        >
          VM backups
        </button>
        <button
          type="button"
          onClick={() => setTab('database')}
          className={`px-4 py-2 rounded-lg text-sm font-medium ${tab === 'database' ? 'bg-primary-600 text-white' : 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300'}`}
        >
          Database backups
        </button>
        <button
          type="button"
          onClick={() => setTab('storage')}
          className={`px-4 py-2 rounded-lg text-sm font-medium ${tab === 'storage' ? 'bg-primary-600 text-white' : 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300'}`}
        >
          VM backup storage
        </button>
      </div>

      {tab === 'vms' && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="card p-4">
              <p className="text-xs text-gray-500">Total</p>
              <p className="text-xl font-semibold text-gray-900 dark:text-white">{vmSummary?.total ?? '—'}</p>
            </div>
            <div className="card p-4">
              <p className="text-xs text-gray-500">Local</p>
              <p className="text-xl font-semibold text-emerald-600">{vmSummary?.by_backend.internal ?? '—'}</p>
            </div>
            <div className="card p-4">
              <p className="text-xs text-gray-500">FTP</p>
              <p className="text-xl font-semibold text-amber-600">{vmSummary?.by_backend.ftp ?? '—'}</p>
            </div>
            <div className="card p-4">
              <p className="text-xs text-gray-500">S3</p>
              <p className="text-xl font-semibold text-sky-600">{vmSummary?.by_backend.s3 ?? '—'}</p>
            </div>
          </div>

          <div className="card overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <ServerStackIcon className="h-5 w-5 text-gray-400" />
                <div>
                  <h2 className="text-base font-semibold text-gray-900 dark:text-white">All VM backups</h2>
                  <p className="text-xs text-gray-500">{filteredCountLabel}</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {([
                  ['all', 'All'],
                  ['local', 'Local'],
                  ['ftp', 'FTP'],
                  ['s3', 'S3'],
                ] as const).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => {
                      setBackendFilter(value);
                      loadVmBackups(value);
                    }}
                    className={`px-2.5 py-1 rounded-md text-xs font-medium ${backendFilter === value ? 'bg-primary-600 text-white' : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300'}`}
                  >
                    {label}
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => loadVmBackups(backendFilter)}
                  className="p-1.5 text-gray-400 hover:text-gray-600"
                  title="Refresh"
                >
                  <ArrowPathIcon className={`h-4 w-4 ${vmLoading ? 'animate-spin' : ''}`} />
                </button>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-800/50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Backup</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">VM</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Tenant</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Location</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Size</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
                    {isSuperadmin && (
                      <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                  {vmLoading ? (
                    <tr><td colSpan={isSuperadmin ? 8 : 7} className="px-6 py-8 text-center text-gray-500">Loading…</td></tr>
                  ) : vmBackups.length === 0 ? (
                    <tr><td colSpan={isSuperadmin ? 8 : 7} className="px-6 py-8 text-center text-gray-500">No VM backups found</td></tr>
                  ) : vmBackups.map((b) => (
                    <tr key={b.id} className="hover:bg-gray-50 dark:hover:bg-gray-800/40">
                      <td className="px-6 py-3 text-sm text-gray-900 dark:text-white">
                        <div className="font-medium">{b.name}</div>
                        {(b.file_path || b.remote_path) && (
                          <div className="text-[11px] text-gray-400 font-mono truncate max-w-[260px]" title={b.remote_path || b.file_path || ''}>
                            {b.remote_path || b.file_path}
                          </div>
                        )}
                        {b.error_message && <div className="text-xs text-red-500 mt-0.5">{b.error_message}</div>}
                      </td>
                      <td className="px-6 py-3 text-sm text-gray-700 dark:text-gray-300">{b.vm_name || b.vm_id.slice(0, 8)}</td>
                      <td className="px-6 py-3 text-sm text-gray-500">{b.tenant_name || '—'}</td>
                      <td className="px-6 py-3">
                        <div className="flex flex-wrap gap-1">
                          {b.locations.map((loc) => locationBadge(loc))}
                        </div>
                      </td>
                      <td className="px-6 py-3 text-sm capitalize text-gray-500">{b.status}</td>
                      <td className="px-6 py-3 text-sm text-gray-500">{formatBytes(b.file_size_bytes)}</td>
                      <td className="px-6 py-3 text-sm text-gray-500 whitespace-nowrap">{new Date(b.created_at).toLocaleString()}</td>
                      {isSuperadmin && (
                        <td className="px-6 py-3 text-right">
                          <button
                            type="button"
                            onClick={() => handleDeleteVmBackup(b)}
                            disabled={deletingVmBackupId === b.id || b.status === 'creating'}
                            className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-900/20 disabled:opacity-50"
                            title={b.status === 'creating' ? 'Cannot delete while creating' : 'Delete backup'}
                          >
                            <TrashIcon className="h-4 w-4" />
                            {deletingVmBackupId === b.id ? 'Deleting…' : 'Delete'}
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {tab === 'database' && (
        <div className="space-y-6">
          <div className="card p-6">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4 flex items-center gap-2">
              <PlusIcon className="h-5 w-5" /> Create database backup
            </h2>
            <form onSubmit={handleCreate} className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name *</label>
                <input className="input-field" value={name} onChange={(e) => setName(e.target.value)} required placeholder="nightly-db" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Notes</label>
                <input className="input-field" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="optional" />
              </div>
              <div className="flex items-end">
                <button type="submit" disabled={creating} className="btn-primary w-full md:w-auto">
                  {creating ? 'Creating…' : 'Create dump'}
                </button>
              </div>
            </form>
          </div>

          <div className="card p-6">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4 flex items-center gap-2">
              <ArrowUpTrayIcon className="h-5 w-5" /> Import / restore
            </h2>
            <p className="text-xs text-gray-500 mb-3">
              Validate a dump first, then restore. Restore overwrites the live database.
            </p>
            <div className="flex flex-wrap gap-3">
              <label className="btn-secondary cursor-pointer">
                <input
                  type="file"
                  accept=".dump,.backup,.sql"
                  className="hidden"
                  disabled={importing}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) handleImportFile(f, 'validate');
                    e.target.value = '';
                  }}
                />
                {importing ? 'Working…' : 'Validate dump'}
              </label>
              <label className="btn-primary cursor-pointer">
                <input
                  type="file"
                  accept=".dump,.backup,.sql"
                  className="hidden"
                  disabled={importing}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) handleImportFile(f, 'restore');
                    e.target.value = '';
                  }}
                />
                Restore dump
              </label>
            </div>
          </div>

          <div className="card overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center gap-2">
              <CircleStackIcon className="h-5 w-5 text-gray-400" />
              <h2 className="text-base font-semibold text-gray-900 dark:text-white">Existing dumps</h2>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-800/50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Size</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
                    <th className="px-6 py-3" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                  {loading ? (
                    <tr><td colSpan={5} className="px-6 py-8 text-center text-gray-500">Loading…</td></tr>
                  ) : backups.length === 0 ? (
                    <tr><td colSpan={5} className="px-6 py-8 text-center text-gray-500">No database backups yet</td></tr>
                  ) : backups.map((b) => (
                    <tr key={b.id}>
                      <td className="px-6 py-3 text-sm text-gray-900 dark:text-white">
                        <div className="font-medium">{b.name}</div>
                        {b.error_message && <div className="text-xs text-red-500 mt-0.5">{b.error_message}</div>}
                      </td>
                      <td className="px-6 py-3 text-sm capitalize text-gray-500">{b.status}</td>
                      <td className="px-6 py-3 text-sm text-gray-500">{formatBytes(b.file_size_bytes)}</td>
                      <td className="px-6 py-3 text-sm text-gray-500">{new Date(b.created_at).toLocaleString()}</td>
                      <td className="px-6 py-3 text-right whitespace-nowrap">
                        {b.status === 'ready' && (
                          <button onClick={() => handleDownload(b)} className="p-1 text-gray-400 hover:text-primary-500" title="Export / download">
                            <ArrowDownTrayIcon className="h-4 w-4" />
                          </button>
                        )}
                        <button onClick={() => handleDelete(b)} className="p-1 text-gray-400 hover:text-red-500" title="Delete">
                          <TrashIcon className="h-4 w-4" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {tab === 'storage' && (
        <form onSubmit={handleSaveStorage} className="card p-6 space-y-5">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            <CloudArrowUpIcon className="h-5 w-5" />
            Where VM backups are stored
          </h2>
          <p className="text-sm text-gray-500">
            Backups are always created locally first. With FTP or S3 they are uploaded afterwards.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {([
              ['internal', 'Internal disk', 'Host path under /var/lib/qemu-interface/storage/backups'],
              ['ftp', 'FTP / FTPS', 'Upload finished backups to an FTP server'],
              ['s3', 'S3 / MinIO', 'Upload to an S3-compatible bucket'],
            ] as const).map(([value, label, help]) => (
              <button
                key={value}
                type="button"
                onClick={() => setStorage((s) => ({ ...s, backend: value }))}
                className={`p-4 rounded-xl border-2 text-left ${storage.backend === value ? 'border-primary-500 bg-primary-50 dark:bg-primary-900/20' : 'border-gray-200 dark:border-gray-700'}`}
              >
                <div className="font-semibold text-gray-900 dark:text-white">{label}</div>
                <p className="text-xs text-gray-500 mt-1">{help}</p>
              </button>
            ))}
          </div>

          <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
            <input
              type="checkbox"
              checked={storage.keep_local_copy}
              onChange={(e) => setStorage((s) => ({ ...s, keep_local_copy: e.target.checked }))}
              className="h-4 w-4 rounded border-gray-300 text-primary-600"
            />
            Keep local copy after remote upload
          </label>

          {storage.backend === 'ftp' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 border border-gray-200 dark:border-gray-700 rounded-xl p-4">
              <div>
                <label className="block text-sm mb-1">Host *</label>
                <input className="input-field" value={storage.ftp.host} onChange={(e) => setStorage((s) => ({ ...s, ftp: { ...s.ftp, host: e.target.value } }))} required={storage.backend === 'ftp'} />
              </div>
              <div>
                <label className="block text-sm mb-1">Port</label>
                <input type="number" className="input-field" value={storage.ftp.port} onChange={(e) => setStorage((s) => ({ ...s, ftp: { ...s.ftp, port: Number(e.target.value) } }))} />
              </div>
              <div>
                <label className="block text-sm mb-1">Username</label>
                <input className="input-field" value={storage.ftp.username} onChange={(e) => setStorage((s) => ({ ...s, ftp: { ...s.ftp, username: e.target.value } }))} />
              </div>
              <div>
                <label className="block text-sm mb-1">Password {storage.ftp.password_set ? '(saved)' : ''}</label>
                <input type="password" className="input-field" value={storage.ftp.password} placeholder={storage.ftp.password_set ? '••••••••' : ''} onChange={(e) => setStorage((s) => ({ ...s, ftp: { ...s.ftp, password: e.target.value } }))} />
              </div>
              <div>
                <label className="block text-sm mb-1">Remote path</label>
                <input className="input-field" value={storage.ftp.path} onChange={(e) => setStorage((s) => ({ ...s, ftp: { ...s.ftp, path: e.target.value } }))} />
              </div>
              <div className="flex items-end pb-2">
                <label className="inline-flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={storage.ftp.use_tls} onChange={(e) => setStorage((s) => ({ ...s, ftp: { ...s.ftp, use_tls: e.target.checked } }))} className="h-4 w-4 rounded" />
                  Use FTPS (TLS)
                </label>
              </div>
            </div>
          )}

          {storage.backend === 's3' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 border border-gray-200 dark:border-gray-700 rounded-xl p-4">
              <div>
                <label className="block text-sm mb-1">Bucket *</label>
                <input className="input-field" value={storage.s3.bucket} onChange={(e) => setStorage((s) => ({ ...s, s3: { ...s.s3, bucket: e.target.value } }))} required={storage.backend === 's3'} />
              </div>
              <div>
                <label className="block text-sm mb-1">Region</label>
                <input className="input-field" value={storage.s3.region} onChange={(e) => setStorage((s) => ({ ...s, s3: { ...s.s3, region: e.target.value } }))} />
              </div>
              <div>
                <label className="block text-sm mb-1">Access key</label>
                <input className="input-field" value={storage.s3.access_key} onChange={(e) => setStorage((s) => ({ ...s, s3: { ...s.s3, access_key: e.target.value } }))} />
              </div>
              <div>
                <label className="block text-sm mb-1">Secret key {storage.s3.secret_key_set ? '(saved)' : ''}</label>
                <input type="password" className="input-field" value={storage.s3.secret_key} placeholder={storage.s3.secret_key_set ? '••••••••' : ''} onChange={(e) => setStorage((s) => ({ ...s, s3: { ...s.s3, secret_key: e.target.value } }))} />
              </div>
              <div>
                <label className="block text-sm mb-1">Endpoint URL (MinIO / custom)</label>
                <input className="input-field" value={storage.s3.endpoint_url} onChange={(e) => setStorage((s) => ({ ...s, s3: { ...s.s3, endpoint_url: e.target.value } }))} placeholder="https://minio.example.com" />
              </div>
              <div>
                <label className="block text-sm mb-1">Key prefix</label>
                <input className="input-field" value={storage.s3.prefix} onChange={(e) => setStorage((s) => ({ ...s, s3: { ...s.s3, prefix: e.target.value } }))} />
              </div>
            </div>
          )}

          <div className="flex justify-end gap-3">
            <button type="button" onClick={handleTestStorage} className="btn-secondary">Test connection</button>
            <button type="submit" disabled={savingStorage} className="btn-primary">
              {savingStorage ? 'Saving…' : 'Save'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
