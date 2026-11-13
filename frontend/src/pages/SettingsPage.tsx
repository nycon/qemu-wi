import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  BugAntIcon,
  ServerStackIcon,
  CircleStackIcon,
  ExclamationTriangleIcon,
  CheckCircleIcon,
  XCircleIcon,
  ArrowPathIcon,
  ClockIcon,
  CpuChipIcon,
  CloudIcon,
  TrashIcon,
  GlobeAltIcon,
  LockClosedIcon,
  LockOpenIcon,
  ShieldCheckIcon,
  BellIcon,
  EnvelopeIcon,
  UserCircleIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { useAuthStore } from '@/store/authStore';
import { useTenantStore } from '@/store/tenantStore';
import { systemApi, notificationApi, tenantApi } from '@/services/api';
import { LiveLogViewer } from '@/components/debug/LiveLogViewer';
import type { Tenant } from '@/types';

interface DebugInfo {
  database?: { status: string; url?: string; error?: string };
  redis?: {
    status: string;
    broker_url?: string;
    error?: string;
    queues?: Record<string, number>;
  };
  celery_workers?: {
    status: string;
    message?: string;
    error?: string;
    workers?: Record<string, {
      status: string;
      active_tasks: number;
      pool: string;
      concurrency: string | number;
      registered_tasks: number;
    }>;
  };
  tasks_24h?: {
    stats?: Record<string, number>;
    recent_failures?: Array<{
      id: string;
      task_type: string;
      error_message: string | null;
      resource_id: string | null;
      created_at: string | null;
      completed_at: string | null;
    }>;
    stuck_tasks?: Array<{
      id: string;
      task_type: string;
      status: string;
      resource_id: string | null;
      created_at: string | null;
      age_minutes: number | null;
    }>;
  };
  vms?: { counts_by_status?: Record<string, number> };
  cloud_images?: { counts_by_status?: Record<string, number> };
  config?: Record<string, unknown>;
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    ok: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
    warning: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
    error: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  };
  const icons: Record<string, React.ElementType> = {
    ok: CheckCircleIcon,
    warning: ExclamationTriangleIcon,
    error: XCircleIcon,
  };
  const Icon = icons[status] || ExclamationTriangleIcon;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${colors[status] || colors.warning}`}>
      <Icon className="h-3.5 w-3.5" />
      {status === 'ok' ? 'Online' : status === 'warning' ? 'Warning' : 'Error'}
    </span>
  );
}

interface SSLStatus {
  domain: string;
  ssl_enabled: boolean;
  cert_exists: boolean;
  cert_expiry: string | null;
  cert_path: string;
}

interface NotificationPrefs {
  notify_vm_error: boolean;
  notify_vm_created: boolean;
  notify_backup_completed: boolean;
  notify_backup_failed: boolean;
  notify_quota_warning: boolean;
  notify_login_new_ip: boolean;
  notify_2fa_changed: boolean;
}

function TenantNotificationEmailCard() {
  const user = useAuthStore((s) => s.user);
  const { currentTenant, setCurrentTenant } = useTenantStore();
  const [email, setEmail] = useState(currentTenant?.notification_email || '');
  const [saving, setSaving] = useState(false);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const [loadingAll, setLoadingAll] = useState(false);

  useEffect(() => {
    setEmail(currentTenant?.notification_email || '');
  }, [currentTenant?.id, currentTenant?.notification_email]);

  useEffect(() => {
    if (!user?.is_superadmin) return;
    setLoadingAll(true);
    tenantApi
      .list()
      .then((list) => {
        setTenants(list);
        setDrafts(
          Object.fromEntries(list.map((t) => [t.id, t.notification_email || ''])),
        );
      })
      .catch(() => {})
      .finally(() => setLoadingAll(false));
  }, [user?.is_superadmin]);

  const saveCurrent = async () => {
    if (!currentTenant) return;
    setSaving(true);
    try {
      const updated = await tenantApi.updateNotificationEmail(
        currentTenant.id,
        email.trim() || null,
      );
      setCurrentTenant(updated);
      setEmail(updated.notification_email || '');
      toast.success('Tenant notification email saved');
    } catch (error: unknown) {
      const msg =
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Could not save email';
      toast.error(typeof msg === 'string' ? msg : 'Could not save email');
    } finally {
      setSaving(false);
    }
  };

  const saveTenant = async (tenantId: string) => {
    setSavingId(tenantId);
    try {
      const updated = await tenantApi.updateNotificationEmail(
        tenantId,
        (drafts[tenantId] || '').trim() || null,
      );
      setTenants((prev) => prev.map((t) => (t.id === tenantId ? updated : t)));
      setDrafts((prev) => ({ ...prev, [tenantId]: updated.notification_email || '' }));
      if (currentTenant?.id === tenantId) setCurrentTenant(updated);
      toast.success('Saved');
    } catch (error: unknown) {
      const msg =
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Could not save email';
      toast.error(typeof msg === 'string' ? msg : 'Could not save email');
    } finally {
      setSavingId(null);
    }
  };

  return (
    <div className="card p-6 mb-6">
      <div className="flex items-center gap-3 mb-5">
        <EnvelopeIcon className="h-6 w-6 text-gray-500 dark:text-gray-400" />
        <div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            Tenant notification email
          </h2>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Inbox for VM, backup and quota alerts for this organisation (independent of your user email).
          </p>
        </div>
      </div>

      {currentTenant ? (
        <div className="flex flex-col sm:flex-row gap-3 sm:items-end mb-2">
          <div className="flex-1">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Email for {currentTenant.name}
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="input-field"
              placeholder="ops@example.com"
            />
          </div>
          <button
            type="button"
            onClick={saveCurrent}
            disabled={saving}
            className="btn-primary shrink-0"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      ) : (
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">Select a tenant first.</p>
      )}

      {user?.is_superadmin && (
        <div className="mt-6 pt-5 border-t border-gray-200 dark:border-gray-700">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-3">
            All tenants
          </h3>
          {loadingAll ? (
            <div className="flex justify-center py-4">
              <ArrowPathIcon className="h-5 w-5 animate-spin text-gray-400" />
            </div>
          ) : tenants.length === 0 ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">No tenants.</p>
          ) : (
            <div className="space-y-3">
              {tenants.map((t) => (
                <div
                  key={t.id}
                  className="flex flex-col sm:flex-row gap-2 sm:items-center"
                >
                  <div className="sm:w-40 shrink-0">
                    <p className="text-sm font-medium text-gray-900 dark:text-white truncate">
                      {t.name}
                    </p>
                    <p className="text-xs text-gray-500 font-mono">{t.slug}</p>
                  </div>
                  <input
                    type="email"
                    value={drafts[t.id] ?? ''}
                    onChange={(e) =>
                      setDrafts((prev) => ({ ...prev, [t.id]: e.target.value }))
                    }
                    className="input-field flex-1"
                    placeholder="ops@example.com"
                  />
                  <button
                    type="button"
                    onClick={() => saveTenant(t.id)}
                    disabled={savingId === t.id}
                    className="btn-secondary shrink-0"
                  >
                    {savingId === t.id ? '…' : 'Save'}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function NotificationPreferencesCard() {
  const [prefs, setPrefs] = useState<NotificationPrefs | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    notificationApi.getPreferences().then(setPrefs).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const toggle = async (key: keyof NotificationPrefs) => {
    if (!prefs) return;
    setSaving(key);
    const newVal = !prefs[key];
    try {
      await notificationApi.updatePreferences({ [key]: newVal });
      setPrefs({ ...prefs, [key]: newVal });
    } catch {
      toast.error('Failed to save preference');
    } finally {
      setSaving(null);
    }
  };

  const notifications: { key: keyof NotificationPrefs; label: string; description: string; icon: string }[] = [
    { key: 'notify_vm_error', label: 'VM Errors', description: 'When a VM enters error state or provisioning fails', icon: '🔴' },
    { key: 'notify_vm_created', label: 'VM Created', description: 'When a new VM is successfully provisioned', icon: '✅' },
    { key: 'notify_backup_completed', label: 'Backup Completed', description: 'When a VM backup finishes successfully', icon: '💾' },
    { key: 'notify_backup_failed', label: 'Backup Failed', description: 'When a VM backup fails', icon: '⚠️' },
    { key: 'notify_quota_warning', label: 'Quota Warnings', description: 'When resource usage approaches quota limits', icon: '📊' },
    { key: 'notify_login_new_ip', label: 'Login Alerts', description: 'When your account is accessed from a new IP', icon: '🔐' },
    { key: 'notify_2fa_changed', label: '2FA Changes', description: 'When two-factor authentication is enabled or disabled', icon: '🛡️' },
  ];

  return (
    <div className="card p-6 mb-6">
      <div className="flex items-center gap-3 mb-5">
        <BellIcon className="h-6 w-6 text-gray-500 dark:text-gray-400" />
        <div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Email Notifications</h2>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Choose which events trigger email notifications
          </p>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-8">
          <ArrowPathIcon className="h-6 w-6 animate-spin text-gray-400" />
        </div>
      ) : prefs ? (
        <div className="divide-y divide-gray-200 dark:divide-gray-700">
          {notifications.map(({ key, label, description, icon }) => (
            <div key={key} className="flex items-center justify-between py-3">
              <div className="flex items-center gap-3">
                <span className="text-lg">{icon}</span>
                <div>
                  <p className="text-sm font-medium text-gray-900 dark:text-white">{label}</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">{description}</p>
                </div>
              </div>
              <button
                onClick={() => toggle(key)}
                disabled={saving === key}
                className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 ${
                  prefs[key] ? 'bg-indigo-600' : 'bg-gray-200 dark:bg-gray-600'
                } ${saving === key ? 'opacity-50' : ''}`}
              >
                <span
                  className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                    prefs[key] ? 'translate-x-5' : 'translate-x-0'
                  }`}
                />
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-gray-500 dark:text-gray-400 py-4 text-center">
          Could not load notification preferences.
        </p>
      )}
    </div>
  );
}

interface SMTPStatus {
  enabled: boolean;
  host: string;
  port: number;
  from_email: string;
  from_name: string;
  use_tls: boolean;
  allow_invalid_cert: boolean;
  configured: boolean;
}

function SMTPConfigCard() {
  const [smtp, setSMTP] = useState<SMTPStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [testEmail, setTestEmail] = useState('');
  const [form, setForm] = useState({
    smtp_host: '',
    smtp_port: 587,
    smtp_user: '',
    smtp_password: '',
    smtp_from_email: '',
    smtp_from_name: '',
    smtp_use_tls: true,
    smtp_allow_invalid_cert: false,
    smtp_enabled: false,
  });

  useEffect(() => {
    notificationApi.getSMTPStatus()
      .then((data) => {
        setSMTP(data);
        setForm({
          smtp_host: data.host,
          smtp_port: data.port,
          smtp_user: '',
          smtp_password: '',
          smtp_from_email: data.from_email,
          smtp_from_name: data.from_name,
          smtp_use_tls: data.use_tls,
          smtp_allow_invalid_cert: data.allow_invalid_cert,
          smtp_enabled: data.enabled,
        });
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {};
      if (form.smtp_host) payload.smtp_host = form.smtp_host;
      payload.smtp_port = form.smtp_port;
      if (form.smtp_user) payload.smtp_user = form.smtp_user;
      if (form.smtp_password) payload.smtp_password = form.smtp_password;
      if (form.smtp_from_email) payload.smtp_from_email = form.smtp_from_email;
      if (form.smtp_from_name) payload.smtp_from_name = form.smtp_from_name;
      payload.smtp_use_tls = form.smtp_use_tls;
      payload.smtp_allow_invalid_cert = form.smtp_allow_invalid_cert;
      payload.smtp_enabled = form.smtp_enabled;

      const updated = await notificationApi.updateSMTP(payload as Parameters<typeof notificationApi.updateSMTP>[0]);
      setSMTP(updated as unknown as SMTPStatus);
      toast.success('SMTP settings saved');
      setEditing(false);
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    if (!testEmail) {
      toast.error('Enter an email address');
      return;
    }
    setTesting(true);
    try {
      // In edit mode, ensure backend runtime settings match current form before testing.
      if (editing) {
        const payload: Record<string, unknown> = {};
        if (form.smtp_host) payload.smtp_host = form.smtp_host;
        payload.smtp_port = form.smtp_port;
        if (form.smtp_user) payload.smtp_user = form.smtp_user;
        if (form.smtp_password) payload.smtp_password = form.smtp_password;
        if (form.smtp_from_email) payload.smtp_from_email = form.smtp_from_email;
        if (form.smtp_from_name) payload.smtp_from_name = form.smtp_from_name;
        payload.smtp_use_tls = form.smtp_use_tls;
        payload.smtp_allow_invalid_cert = form.smtp_allow_invalid_cert;
        payload.smtp_enabled = form.smtp_enabled;
        const updated = await notificationApi.updateSMTP(payload as Parameters<typeof notificationApi.updateSMTP>[0]);
        setSMTP(updated as unknown as SMTPStatus);
      }

      const result = await notificationApi.testSMTP(testEmail);
      toast.success(result.message);
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Failed to send test email'
        : String(err);
      toast.error(detail);
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="card p-6 mb-6">
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
          <EnvelopeIcon className="h-6 w-6 text-gray-500 dark:text-gray-400" />
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">SMTP Configuration</h2>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Configure email server for sending notifications
            </p>
          </div>
        </div>
        {smtp && (
          <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${
            smtp.enabled && smtp.configured
              ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
              : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400'
          }`}>
            {smtp.enabled && smtp.configured ? 'Active' : smtp.enabled ? 'Not Configured' : 'Disabled'}
          </span>
        )}
      </div>

      {loading ? (
        <div className="flex justify-center py-8">
          <ArrowPathIcon className="h-6 w-6 animate-spin text-gray-400" />
        </div>
      ) : editing ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">SMTP Host</label>
              <input
                type="text"
                value={form.smtp_host}
                onChange={(e) => setForm({ ...form, smtp_host: e.target.value })}
                placeholder="smtp.example.com"
                className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm text-gray-900 dark:text-white"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Port</label>
              <input
                type="number"
                value={form.smtp_port}
                onChange={(e) => setForm({ ...form, smtp_port: parseInt(e.target.value) || 587 })}
                className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm text-gray-900 dark:text-white"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Username</label>
              <input
                type="text"
                value={form.smtp_user}
                onChange={(e) => setForm({ ...form, smtp_user: e.target.value })}
                placeholder="user@example.com"
                className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm text-gray-900 dark:text-white"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Password</label>
              <input
                type="password"
                value={form.smtp_password}
                onChange={(e) => setForm({ ...form, smtp_password: e.target.value })}
                placeholder="Leave empty to keep current"
                className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm text-gray-900 dark:text-white"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">From Email</label>
              <input
                type="email"
                value={form.smtp_from_email}
                onChange={(e) => setForm({ ...form, smtp_from_email: e.target.value })}
                placeholder="noreply@example.com"
                className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm text-gray-900 dark:text-white"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">From Name</label>
              <input
                type="text"
                value={form.smtp_from_name}
                onChange={(e) => setForm({ ...form, smtp_from_name: e.target.value })}
                placeholder="QEMU Interface"
                className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm text-gray-900 dark:text-white"
              />
            </div>
          </div>

          <div className="flex items-center gap-6 pt-2">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.smtp_use_tls}
                onChange={(e) => setForm({ ...form, smtp_use_tls: e.target.checked })}
                className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500"
              />
              <span className="text-sm text-gray-700 dark:text-gray-300">Use TLS (STARTTLS)</span>
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.smtp_enabled}
                onChange={(e) => setForm({ ...form, smtp_enabled: e.target.checked })}
                className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500"
              />
              <span className="text-sm text-gray-700 dark:text-gray-300">Enable Email Notifications</span>
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.smtp_allow_invalid_cert}
                onChange={(e) => setForm({ ...form, smtp_allow_invalid_cert: e.target.checked })}
                className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500"
              />
              <span className="text-sm text-gray-700 dark:text-gray-300">Allow invalid TLS certificate (insecure)</span>
            </label>
          </div>

          {/* Test email in edit mode */}
          <div className="flex items-center gap-3 pt-3 border-t border-gray-200 dark:border-gray-700">
            <input
              type="email"
              value={testEmail}
              onChange={(e) => setTestEmail(e.target.value)}
              placeholder="test@example.com"
              className="flex-1 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-1.5 text-sm text-gray-900 dark:text-white"
            />
            <button onClick={handleTest} disabled={testing || !form.smtp_enabled} className="btn btn-secondary text-sm whitespace-nowrap">
              {testing ? 'Sending...' : 'Send Test'}
            </button>
          </div>

          <div className="flex gap-3 pt-2">
            <button onClick={handleSave} disabled={saving} className="btn btn-primary text-sm">
              {saving ? 'Saving...' : 'Save Settings'}
            </button>
            <button onClick={() => setEditing(false)} className="btn btn-secondary text-sm">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div>
          {smtp && smtp.configured ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-y-2 text-sm">
                <span className="text-gray-500 dark:text-gray-400">Host</span>
                <span className="text-gray-900 dark:text-white font-mono">{smtp.host}:{smtp.port}</span>
                <span className="text-gray-500 dark:text-gray-400">From</span>
                <span className="text-gray-900 dark:text-white">{smtp.from_name} &lt;{smtp.from_email}&gt;</span>
                <span className="text-gray-500 dark:text-gray-400">TLS</span>
                <span className="text-gray-900 dark:text-white">{smtp.use_tls ? 'Yes' : 'No'}</span>
              </div>

              {/* Test email */}
              <div className="flex items-center gap-3 pt-3 border-t border-gray-200 dark:border-gray-700">
                <input
                  type="email"
                  value={testEmail}
                  onChange={(e) => setTestEmail(e.target.value)}
                  placeholder="test@example.com"
                  className="flex-1 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-1.5 text-sm text-gray-900 dark:text-white"
                />
                <button onClick={handleTest} disabled={testing || !smtp.enabled} className="btn btn-secondary text-sm whitespace-nowrap">
                  {testing ? 'Sending...' : 'Send Test'}
                </button>
                <button onClick={() => setEditing(true)} className="btn btn-primary text-sm">
                  Edit
                </button>
              </div>
            </div>
          ) : (
            <div className="text-center py-6">
              <EnvelopeIcon className="h-10 w-10 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
              <p className="text-sm text-gray-500 dark:text-gray-400 mb-3">
                SMTP is not configured. Set up an email server to enable notifications.
              </p>
              <button onClick={() => setEditing(true)} className="btn btn-primary text-sm">
                Configure SMTP
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function SettingsPage() {
  const user = useAuthStore((s) => s.user);
  const [debugMode, setDebugMode] = useState(() => localStorage.getItem('debug_mode') === 'true');
  const [debugInfo, setDebugInfo] = useState<DebugInfo | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [isCleaning, setIsCleaning] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);

  // SSL State
  const [sslStatus, setSSLStatus] = useState<SSLStatus | null>(null);
  const [sslLoading, setSSLLoading] = useState(false);
  const [sslSetupLoading, setSSLSetupLoading] = useState(false);
  const [sslDomain, setSSLDomain] = useState('');
  const [sslEmail, setSSLEmail] = useState('');
  const [sslSetupLog, setSSLSetupLog] = useState<string[]>([]);

  const loadDebugInfo = useCallback(async () => {
    if (!debugMode || !user?.is_superadmin) return;
    setIsLoading(true);
    try {
      const data = await systemApi.getDebugInfo();
      setDebugInfo(data as DebugInfo);
    } catch {
      toast.error('Debug info could not be loaded');
    } finally {
      setIsLoading(false);
    }
  }, [debugMode, user?.is_superadmin]);

  useEffect(() => {
    if (debugMode && user?.is_superadmin) {
      loadDebugInfo();
    }
  }, [debugMode, user?.is_superadmin, loadDebugInfo]);

  // Auto-refresh every 10s when enabled
  useEffect(() => {
    if (!autoRefresh || !debugMode) return;
    const interval = setInterval(loadDebugInfo, 10000);
    return () => clearInterval(interval);
  }, [autoRefresh, debugMode, loadDebugInfo]);

  const toggleDebugMode = () => {
    const newVal = !debugMode;
    setDebugMode(newVal);
    localStorage.setItem('debug_mode', String(newVal));
    if (!newVal) {
      setDebugInfo(null);
      setAutoRefresh(false);
    }
  };

  const handleRetryStuck = async () => {
    setIsRetrying(true);
    try {
      const result = await systemApi.retryStuckTasks();
      toast.success(result.message);
      loadDebugInfo();
    } catch {
      toast.error('Retry failed');
    } finally {
      setIsRetrying(false);
    }
  };

  // SSL pre-check
  const [sslChecks, setSSLChecks] = useState<Array<{ name: string; status: string; detail: string }>>([]);
  const [sslTestLoading, setSSLTestLoading] = useState(false);
  const [sslReady, setSSLReady] = useState<boolean | null>(null);

  // --- SSL Functions ---
  const loadSSLStatus = useCallback(async () => {
    if (!user?.is_superadmin) return;
    setSSLLoading(true);
    try {
      const data = await systemApi.getSSLStatus();
      setSSLStatus(data);
      if (data.domain) setSSLDomain(data.domain);
    } catch {
      // Silently fail - agent might not be reachable
    } finally {
      setSSLLoading(false);
    }
  }, [user?.is_superadmin]);

  useEffect(() => {
    if (user?.is_superadmin) {
      loadSSLStatus();
    }
  }, [user?.is_superadmin, loadSSLStatus]);

  const handleSSLTest = async () => {
    if (!sslDomain.trim()) {
      toast.error('Please enter a domain');
      return;
    }
    setSSLTestLoading(true);
    setSSLChecks([]);
    setSSLReady(null);
    try {
      const result = await systemApi.testSSL({ domain: sslDomain.trim(), email: sslEmail.trim() });
      setSSLChecks(result.checks);
      setSSLReady(result.ready);
      if (result.ready) {
        toast.success('All requirements met! You can now set up SSL.');
      } else {
        toast.error('Some requirements are not met. See details below.');
      }
    } catch (err: unknown) {
      const msg = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Unknown error'
        : String(err);
      toast.error(`Test failed: ${msg}`);
    } finally {
      setSSLTestLoading(false);
    }
  };

  const handleSSLSetup = async () => {
    if (!sslDomain.trim()) {
      toast.error('Please enter a domain');
      return;
    }
    if (!window.confirm(
      `Set up SSL certificate for "${sslDomain}"?\n\n` +
      'Requirements:\n' +
      '• The domain must point to this server\'s IP (A record)\n' +
      '• Port 80 must be reachable from outside\n' +
      '• Certbot will run on the host\n\n' +
      'Continue?'
    )) return;

    setSSLSetupLoading(true);
    setSSLSetupLog([]);
    try {
      const result = await systemApi.setupSSL({ domain: sslDomain.trim(), email: sslEmail.trim() });
      setSSLSetupLog(result.steps || []);
      if ((result as Record<string, unknown>).warnings && Array.isArray((result as Record<string, unknown>).warnings)) {
        ((result as Record<string, unknown>).warnings as string[]).forEach((w: string) =>
          toast.error(w, { duration: 10000 })
        );
      }
      toast.success(`SSL certificate for ${result.domain} set up successfully!`, { duration: 8000 });
      await loadSSLStatus();
      if (result.ssl_enabled && window.location.protocol === 'http:') {
        setTimeout(() => {
          window.location.href = `https://${sslDomain.trim()}${window.location.pathname}`;
        }, 4000);
        toast.success('Redirecting to HTTPS in 4 seconds...', { duration: 4000 });
      }
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Unknown error'
        : String(err);
      // Show error in the log area for better readability
      setSSLSetupLog(detail.split('\n').filter((l: string) => l.trim()));
      toast.error('SSL setup failed. See details below.', { duration: 10000 });
    } finally {
      setSSLSetupLoading(false);
    }
  };

  const handleSSLDisable = async () => {
    if (!window.confirm('Really disable SSL? The server will then only be reachable via HTTP.')) return;
    try {
      await systemApi.disableSSL();
      toast.success('SSL disabled');
      await loadSSLStatus();
      if (window.location.protocol === 'https:') {
        setTimeout(() => {
          window.location.href = `http://${window.location.host}${window.location.pathname}`;
        }, 2000);
      }
    } catch (err: unknown) {
      const msg = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Unknown error'
        : String(err);
      toast.error(`Error: ${msg}`);
    }
  };

  const handleCleanup = async () => {
    if (!window.confirm(
      'WARNING: This will clean up all stuck VMs, tasks and downloads.\n\n' +
      '• Stuck VMs → Error status\n' +
      '• Error/Deleting VMs → Soft-delete (removed from DB)\n' +
      '• Stuck Tasks → Failed\n' +
      '• Stuck image downloads → Failed\n\n' +
      'Continue?'
    )) return;

    setIsCleaning(true);
    try {
      const result = await systemApi.cleanup();
      const parts = [];
      if (result.vms_reset > 0) parts.push(`${result.vms_reset} VMs reset`);
      if (result.vms_purged > 0) parts.push(`${result.vms_purged} VMs deleted`);
      if (result.tasks_failed > 0) parts.push(`${result.tasks_failed} tasks cleaned`);
      if (result.images_reset > 0) parts.push(`${result.images_reset} images reset`);
      toast.success(parts.length > 0 ? parts.join(', ') : 'Nothing to clean up');
      if (result.errors && result.errors.length > 0) {
        toast.error(`Warnings: ${result.errors.join('; ')}`, { duration: 10000 });
      }
      loadDebugInfo();
    } catch (err: unknown) {
      const msg = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || JSON.stringify((err as { response?: { data?: unknown } }).response?.data)
        : String(err);
      toast.error(`Cleanup failed: ${msg}`, { duration: 15000 });
    } finally {
      setIsCleaning(false);
    }
  };

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Settings</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          System configuration and diagnostics
        </p>
      </div>

      <div className="card p-5 mb-6">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <UserCircleIcon className="h-6 w-6 text-gray-500 dark:text-gray-400" />
            <div>
              <h2 className="text-base font-semibold text-gray-900 dark:text-white">Account security</h2>
              <p className="text-sm text-gray-500 dark:text-gray-400">
                Profile, password, 2FA and API keys live under User Profile.
              </p>
            </div>
          </div>
          <Link to="/profile" className="btn-secondary text-sm">
            Open User Profile
          </Link>
        </div>
      </div>

      {/* SSL / Domain Configuration - only for superadmin */}
      {user?.is_superadmin && (
        <div className="card p-6 mb-6">
          <div className="flex items-center gap-3 mb-5">
            <GlobeAltIcon className="h-6 w-6 text-gray-500 dark:text-gray-400" />
            <div>
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Domain & SSL</h2>
              <p className="text-sm text-gray-500 dark:text-gray-400">
                Configure domain and set up Let's Encrypt SSL certificate
              </p>
            </div>
          </div>

          {/* Current SSL Status */}
          {sslStatus && (
            <div className={`rounded-lg p-4 mb-5 ${sslStatus.ssl_enabled ? 'bg-green-50 dark:bg-green-900/10 border border-green-200 dark:border-green-800' : 'bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700'}`}>
              <div className="flex items-center gap-3">
                {sslStatus.ssl_enabled ? (
                  <ShieldCheckIcon className="h-8 w-8 text-green-500 flex-shrink-0" />
                ) : (
                  <LockOpenIcon className="h-8 w-8 text-gray-400 dark:text-gray-500 flex-shrink-0" />
                )}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${sslStatus.ssl_enabled ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400'}`}>
                      {sslStatus.ssl_enabled ? (
                        <><LockClosedIcon className="h-3 w-3" /> SSL active</>
                      ) : (
                        <><LockOpenIcon className="h-3 w-3" /> No SSL</>
                      )}
                    </span>
                  </div>
                  {sslStatus.domain && (
                    <p className="text-sm font-medium text-gray-800 dark:text-gray-200 mt-1">{sslStatus.domain}</p>
                  )}
                  {sslStatus.cert_expiry && (
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Certificate valid until: {sslStatus.cert_expiry}</p>
                  )}
                </div>
                {sslStatus.ssl_enabled && (
                  <div className="flex items-center gap-3 flex-shrink-0">
                    <button
                      onClick={async () => {
                        try {
                          const r = await systemApi.regenerateSSLConfig();
                          toast.success(r.message);
                        } catch { toast.error('Config update failed'); }
                      }}
                      className="text-xs text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 underline"
                    >
                      Update config
                    </button>
                    <button
                      onClick={handleSSLDisable}
                      className="text-xs text-red-600 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 underline"
                    >
                      Disable SSL
                    </button>
                  </div>
                )}
                <button
                  onClick={loadSSLStatus}
                  disabled={sslLoading}
                  className="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 flex-shrink-0"
                  title="Refresh status"
                >
                  <ArrowPathIcon className={`h-4 w-4 ${sslLoading ? 'animate-spin' : ''}`} />
                </button>
              </div>
            </div>
          )}

          {/* Domain Input */}
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Domain
              </label>
              <input
                type="text"
                value={sslDomain}
                onChange={(e) => setSSLDomain(e.target.value)}
                placeholder="e.g. panel.example.com"
                className="input w-full"
                disabled={sslSetupLoading}
              />
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                The domain must point to this server's IP via A record
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Email (optional, for Let's Encrypt notifications)
              </label>
              <input
                type="email"
                value={sslEmail}
                onChange={(e) => setSSLEmail(e.target.value)}
                placeholder="admin@example.com"
                className="input w-full"
                disabled={sslSetupLoading}
              />
            </div>

            <div className="flex items-center gap-3 flex-wrap">
              <button
                onClick={handleSSLTest}
                disabled={sslTestLoading || !sslDomain.trim()}
                className="inline-flex items-center gap-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-4 py-2.5 text-sm font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors disabled:opacity-50"
              >
                {sslTestLoading ? (
                  <><ArrowPathIcon className="h-4 w-4 animate-spin" /> Checking...</>
                ) : (
                  <><ShieldCheckIcon className="h-4 w-4" /> Test requirements</>
                )}
              </button>
              <button
                onClick={handleSSLSetup}
                disabled={sslSetupLoading || !sslDomain.trim()}
                className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-primary-700 transition-colors disabled:opacity-50"
              >
                {sslSetupLoading ? (
                  <><ArrowPathIcon className="h-4 w-4 animate-spin" /> Creating certificate...</>
                ) : (
                  <><LockClosedIcon className="h-4 w-4" /> Set up SSL certificate</>
                )}
              </button>
            </div>

            {/* Pre-check Results */}
            {sslChecks.length > 0 && (
              <div className={`rounded-lg p-4 mt-2 border ${sslReady ? 'bg-green-50 dark:bg-green-900/10 border-green-200 dark:border-green-800' : 'bg-red-50 dark:bg-red-900/10 border-red-200 dark:border-red-800'}`}>
                <h4 className="text-sm font-medium mb-3 text-gray-800 dark:text-gray-200">
                  {sslReady ? 'All checks passed' : 'Some checks failed'}
                </h4>
                <div className="space-y-2">
                  {sslChecks.map((check, i) => (
                    <div key={i} className="flex items-start gap-2 text-sm">
                      {check.status === 'ok' ? (
                        <CheckCircleIcon className="h-4 w-4 text-green-500 flex-shrink-0 mt-0.5" />
                      ) : check.status === 'warning' ? (
                        <ExclamationTriangleIcon className="h-4 w-4 text-yellow-500 flex-shrink-0 mt-0.5" />
                      ) : (
                        <XCircleIcon className="h-4 w-4 text-red-500 flex-shrink-0 mt-0.5" />
                      )}
                      <div>
                        <span className="font-medium text-gray-800 dark:text-gray-200">{check.name}: </span>
                        <span className={`${check.status === 'error' ? 'text-red-600 dark:text-red-400' : check.status === 'warning' ? 'text-yellow-600 dark:text-yellow-400' : 'text-green-600 dark:text-green-400'}`}>
                          {check.detail}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Setup Log */}
            {sslSetupLog.length > 0 && (
              <div className="rounded-lg bg-gray-900 p-4 mt-4">
                <h4 className="text-xs font-medium text-gray-400 dark:text-gray-500 mb-2 uppercase tracking-wider">Setup log</h4>
                <div className="space-y-1">
                  {sslSetupLog.map((step, i) => {
                    const isError = step.toLowerCase().includes('fehlgeschlagen') || step.toLowerCase().includes('fehler') || step.toLowerCase().includes('error') || step.toLowerCase().includes('hinweis');
                    const isWarning = step.toLowerCase().includes('warnung') || step.toLowerCase().includes('mismatch');
                    return (
                      <div key={i} className="flex items-start gap-2 text-sm">
                        {isError ? (
                          <XCircleIcon className="h-4 w-4 text-red-400 flex-shrink-0 mt-0.5" />
                        ) : isWarning ? (
                          <ExclamationTriangleIcon className="h-4 w-4 text-yellow-400 flex-shrink-0 mt-0.5" />
                        ) : (
                          <CheckCircleIcon className="h-4 w-4 text-green-400 flex-shrink-0 mt-0.5" />
                        )}
                        <span className={`${isError ? 'text-red-400' : isWarning ? 'text-yellow-400' : 'text-gray-300'} break-words`}>
                          {step}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tenant notification inbox */}
      <TenantNotificationEmailCard />

      {/* Email Notifications - for all users */}
      <NotificationPreferencesCard />

      {/* SMTP Configuration - only for superadmin */}
      {user?.is_superadmin && <SMTPConfigCard />}

      {/* Debug Mode Toggle - only for superadmin */}
      {user?.is_superadmin && (
        <div className="card p-6 mb-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <BugAntIcon className="h-6 w-6 text-gray-500 dark:text-gray-400" />
              <div>
                <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Debug Mode</h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  Shows system diagnostics, worker status, logs and failed tasks
                </p>
              </div>
            </div>
            <button
              onClick={toggleDebugMode}
              className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 ${
                debugMode ? 'bg-primary-600' : 'bg-gray-200 dark:bg-gray-700'
              }`}
            >
              <span
                className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                  debugMode ? 'translate-x-5' : 'translate-x-0'
                }`}
              />
            </button>
          </div>
        </div>
      )}

      {/* Debug Panel */}
      {debugMode && user?.is_superadmin && (
        <div className="space-y-6">
          {/* Controls */}
          <div className="flex items-center gap-4 flex-wrap">
            <button
              onClick={loadDebugInfo}
              disabled={isLoading}
              className="btn-secondary text-sm"
            >
              <ArrowPathIcon className={`h-4 w-4 mr-1.5 ${isLoading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
            <button
              onClick={handleCleanup}
              disabled={isCleaning}
              className="inline-flex items-center gap-1.5 rounded-lg border border-red-300 bg-red-50 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-100 transition-colors disabled:opacity-50 dark:border-red-700 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/30"
            >
              <TrashIcon className={`h-4 w-4 ${isCleaning ? 'animate-pulse' : ''}`} />
              System Reset
            </button>
            <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                className="rounded border-gray-300 dark:border-gray-600 text-primary-600 focus:ring-primary-500"
              />
              Auto-Refresh (10s)
            </label>
          </div>

          <LiveLogViewer />

          {debugInfo && (
            <>
              {/* Health Overview */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {/* Database */}
                <div className="card p-4">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <CircleStackIcon className="h-5 w-5 text-gray-500 dark:text-gray-400" />
                      <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Database</h3>
                    </div>
                    <StatusBadge status={debugInfo.database?.status || 'error'} />
                  </div>
                  <p className="text-xs text-gray-500 dark:text-gray-400 font-mono truncate">
                    {debugInfo.database?.url || 'N/A'}
                  </p>
                  {debugInfo.database?.error && (
                    <p className="text-xs text-red-500 mt-1">{debugInfo.database.error}</p>
                  )}
                </div>

                {/* Redis */}
                <div className="card p-4">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <ServerStackIcon className="h-5 w-5 text-gray-500 dark:text-gray-400" />
                      <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Redis</h3>
                    </div>
                    <StatusBadge status={debugInfo.redis?.status || 'error'} />
                  </div>
                  {debugInfo.redis?.queues && (
                    <div className="space-y-1 text-xs text-gray-600 dark:text-gray-400">
                      {Object.entries(debugInfo.redis.queues).map(([q, len]) => (
                        <div key={q} className="flex justify-between">
                          <span className="font-mono">{q}</span>
                          <span className={`font-medium ${(len as number) > 0 ? 'text-yellow-600 dark:text-yellow-400' : 'text-gray-400 dark:text-gray-500'}`}>
                            {len as number} pending
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                  {debugInfo.redis?.error && (
                    <p className="text-xs text-red-500 mt-1">{debugInfo.redis.error}</p>
                  )}
                </div>

                {/* Celery Workers */}
                <div className="card p-4">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <CpuChipIcon className="h-5 w-5 text-gray-500 dark:text-gray-400" />
                      <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Worker</h3>
                    </div>
                    <StatusBadge status={debugInfo.celery_workers?.status || 'error'} />
                  </div>
                  {debugInfo.celery_workers?.workers ? (
                    Object.entries(debugInfo.celery_workers.workers).map(([name, info]) => (
                      <div key={name} className="text-xs text-gray-600 dark:text-gray-400 space-y-0.5">
                        <div className="font-mono truncate text-gray-800 dark:text-gray-200">{name.split('@')[1] || name}</div>
                        <div>Active: {info.active_tasks} | Concurrency: {info.concurrency} | Tasks: {info.registered_tasks}</div>
                      </div>
                    ))
                  ) : (
                    <p className="text-xs text-yellow-600 dark:text-yellow-400">
                      {debugInfo.celery_workers?.message || debugInfo.celery_workers?.error || 'No workers found'}
                    </p>
                  )}
                </div>
              </div>

              {/* VM and Image Overview */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* VMs by status */}
                <div className="card p-4">
                  <div className="flex items-center gap-2 mb-3">
                    <ServerStackIcon className="h-5 w-5 text-gray-500 dark:text-gray-400" />
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-white">VMs by status</h3>
                  </div>
                  {debugInfo.vms?.counts_by_status && Object.keys(debugInfo.vms.counts_by_status).length > 0 ? (
                    <div className="space-y-1.5">
                      {Object.entries(debugInfo.vms.counts_by_status).map(([status, count]) => (
                        <div key={status} className="flex items-center justify-between text-sm">
                          <span className="text-gray-600 dark:text-gray-400 capitalize">{status}</span>
                          <span className={`font-medium ${status === 'error' ? 'text-red-500' : status === 'running' ? 'text-green-500' : 'text-gray-700 dark:text-gray-300'}`}>
                            {count as number}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-gray-400 dark:text-gray-500">No VMs</p>
                  )}
                </div>

                {/* Cloud Images by status */}
                <div className="card p-4">
                  <div className="flex items-center gap-2 mb-3">
                    <CloudIcon className="h-5 w-5 text-gray-500 dark:text-gray-400" />
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Cloud images by status</h3>
                  </div>
                  {debugInfo.cloud_images?.counts_by_status && Object.keys(debugInfo.cloud_images.counts_by_status).length > 0 ? (
                    <div className="space-y-1.5">
                      {Object.entries(debugInfo.cloud_images.counts_by_status).map(([status, count]) => (
                        <div key={status} className="flex items-center justify-between text-sm">
                          <span className="text-gray-600 dark:text-gray-400 capitalize">{status}</span>
                          <span className={`font-medium ${status === 'failed' ? 'text-red-500' : status === 'ready' ? 'text-green-500' : 'text-gray-700 dark:text-gray-300'}`}>
                            {count as number}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-gray-400 dark:text-gray-500">No images</p>
                  )}
                </div>
              </div>

              {/* Task Stats (24h) */}
              {debugInfo.tasks_24h?.stats && (
                <div className="card p-4">
                  <h3 className="text-sm font-semibold text-gray-900 dark:text-white mb-3">Tasks (last 24h)</h3>
                  <div className="flex flex-wrap gap-3">
                    {Object.entries(debugInfo.tasks_24h.stats).map(([status, count]) => {
                      const colors: Record<string, string> = {
                        success: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
                        failed: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
                        queued: 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-400',
                        running: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
                      };
                      return (
                        <span key={status} className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium ${colors[status] || colors.queued}`}>
                          {status}: {count as number}
                        </span>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Stuck Tasks */}
              {debugInfo.tasks_24h?.stuck_tasks && debugInfo.tasks_24h.stuck_tasks.length > 0 && (
                <div className="card p-4 border-l-4 border-yellow-500">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <ClockIcon className="h-5 w-5 text-yellow-500" />
                      <h3 className="text-sm font-semibold text-yellow-700 dark:text-yellow-400">
                        Stuck Tasks ({debugInfo.tasks_24h.stuck_tasks.length})
                      </h3>
                    </div>
                    <button
                      onClick={handleRetryStuck}
                      disabled={isRetrying}
                      className="btn-secondary text-sm text-yellow-700 dark:text-yellow-400 border-yellow-300 dark:border-yellow-600 hover:bg-yellow-50 dark:hover:bg-yellow-900/20"
                    >
                      <ArrowPathIcon className={`h-4 w-4 mr-1 ${isRetrying ? 'animate-spin' : ''}`} />
                      Reset all
                    </button>
                  </div>
                  <div className="space-y-2">
                    {debugInfo.tasks_24h.stuck_tasks.map((t) => (
                      <div key={t.id} className="flex items-center justify-between text-sm bg-yellow-50 dark:bg-yellow-900/10 rounded p-2">
                        <div>
                          <span className="font-medium text-gray-800 dark:text-gray-200">{t.task_type}</span>
                          <span className="text-xs text-gray-500 dark:text-gray-400 ml-2">({t.status})</span>
                        </div>
                        <div className="text-xs text-gray-500 dark:text-gray-400">
                          {t.age_minutes != null ? `${t.age_minutes} min` : '-'}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Recent Failures */}
              {debugInfo.tasks_24h?.recent_failures && debugInfo.tasks_24h.recent_failures.length > 0 && (
                <div className="card p-4 border-l-4 border-red-500">
                  <div className="flex items-center gap-2 mb-3">
                    <ExclamationTriangleIcon className="h-5 w-5 text-red-500" />
                    <h3 className="text-sm font-semibold text-red-700 dark:text-red-400">
                      Recent failures ({debugInfo.tasks_24h.recent_failures.length})
                    </h3>
                  </div>
                  <div className="space-y-2 max-h-80 overflow-y-auto">
                    {debugInfo.tasks_24h.recent_failures.map((t) => (
                      <div key={t.id} className="bg-red-50 dark:bg-red-900/10 rounded p-3 text-sm">
                        <div className="flex items-center justify-between mb-1">
                          <span className="font-medium text-gray-800 dark:text-gray-200">{t.task_type}</span>
                          <span className="text-xs text-gray-500 dark:text-gray-400">
                            {t.created_at ? new Date(t.created_at).toLocaleString() : '-'}
                          </span>
                        </div>
                        {t.error_message && (
                          <pre className="text-xs text-red-600 dark:text-red-400 whitespace-pre-wrap break-words font-mono bg-red-100 dark:bg-red-900/20 rounded p-2 mt-1 max-h-24 overflow-y-auto">
                            {t.error_message}
                          </pre>
                        )}
                        {t.resource_id && (
                          <span className="text-xs text-gray-400 dark:text-gray-500 mt-1 block font-mono">
                            Resource: {t.resource_id}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* System Config */}
              {debugInfo.config && (
                <div className="card p-4">
                  <h3 className="text-sm font-semibold text-gray-900 dark:text-white mb-3">System configuration</h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                    {Object.entries(debugInfo.config).map(([key, value]) => (
                      <div key={key} className="flex justify-between text-sm py-1 border-b border-gray-100 dark:border-gray-800 last:border-0">
                        <span className="text-gray-500 dark:text-gray-400 font-mono text-xs">{key}</span>
                        <span className="text-gray-800 dark:text-gray-200 font-mono text-xs truncate max-w-[200px]">
                          {String(value)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

          {/* Loading state */}
          {isLoading && !debugInfo && (
            <div className="text-center py-12 text-gray-500 dark:text-gray-400">
              <ArrowPathIcon className="h-8 w-8 animate-spin mx-auto mb-2 text-primary-500" />
              Loading diagnostic data...
            </div>
          )}
        </div>
      )}

    </div>
  );
}
