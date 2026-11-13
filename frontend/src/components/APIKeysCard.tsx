import { useEffect, useState } from 'react';
import {
  CheckCircleIcon,
  ShieldCheckIcon,
  TrashIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { apiKeyApi } from '@/services/api';
import { useAuthStore } from '@/store/authStore';
import { useTenantStore } from '@/store/tenantStore';

interface ApiKeyItem {
  id: string;
  name: string;
  description: string | null;
  key_prefix: string;
  tenant_id: string | null;
  is_active: boolean;
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

export function APIKeysCard() {
  const user = useAuthStore((s) => s.user);
  const tenants = useTenantStore((s) => s.tenants);
  const currentTenant = useTenantStore((s) => s.currentTenant);
  const [keys, setKeys] = useState<ApiKeyItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newKeyName, setNewKeyName] = useState('');
  const [newKeyDescription, setNewKeyDescription] = useState('');
  const [newKeyExpiry, setNewKeyExpiry] = useState('');
  const [newKeyTenantId, setNewKeyTenantId] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (currentTenant?.id && !newKeyTenantId) {
      setNewKeyTenantId(currentTenant.id);
    }
  }, [currentTenant?.id, newKeyTenantId]);

  const loadKeys = async () => {
    try {
      const data = await apiKeyApi.list();
      setKeys(data.items);
    } catch {
      toast.error('API keys could not be loaded');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => { loadKeys(); }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newKeyName.trim()) return;
    const scopedTenantId = newKeyTenantId || currentTenant?.id || '';
    if (!user?.is_superadmin && !scopedTenantId) {
      toast.error('Please select a tenant for this API key');
      return;
    }
    setIsCreating(true);
    try {
      const result = await apiKeyApi.create({
        name: newKeyName.trim(),
        description: newKeyDescription.trim() || undefined,
        tenant_id: scopedTenantId || undefined,
        expires_in_days: newKeyExpiry ? parseInt(newKeyExpiry) : undefined,
      });
      setCreatedKey(result.api_key);
      setNewKeyName('');
      setNewKeyDescription('');
      setNewKeyExpiry('');
      setShowCreate(false);
      loadKeys();
      toast.success('API key created');
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    } finally {
      setIsCreating(false);
    }
  };

  const handleRevoke = async (id: string, name: string) => {
    if (!window.confirm(`Really revoke API key "${name}"? All integrations using this key will be blocked immediately.`)) return;
    try {
      await apiKeyApi.revoke(id);
      toast.success('API key revoked');
      loadKeys();
    } catch {
      toast.error('Error revoking key');
    }
  };

  const handleDelete = async (id: string, name: string) => {
    if (!window.confirm(`Permanently delete API key "${name}"? This cannot be undone.`)) return;
    try {
      await apiKeyApi.deletePermanent(id);
      toast.success('API key deleted');
      loadKeys();
    } catch {
      toast.error('Error deleting key');
    }
  };

  const handleCopy = () => {
    if (createdKey) {
      navigator.clipboard.writeText(createdKey);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="card p-6 mb-6">
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
          <ShieldCheckIcon className="h-6 w-6 text-gray-500 dark:text-gray-400" />
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">API Keys</h2>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Programmatic access with X-API-Key. Keys inherit your role; tenant-scoped keys stay inside that tenant.
            </p>
          </div>
        </div>
        <button
          onClick={() => { setShowCreate(!showCreate); setCreatedKey(null); }}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-700"
        >
          + New API Key
        </button>
      </div>

      {/* Created Key Banner (shown only once!) */}
      {createdKey && (
        <div className="mb-5 p-4 rounded-lg bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800">
          <div className="flex items-start gap-3">
            <CheckCircleIcon className="h-5 w-5 text-green-600 mt-0.5 flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-green-800 dark:text-green-300 mb-1">
                API key created – copy it now!
              </p>
              <p className="text-xs text-green-700 dark:text-green-400 mb-2">
                This key will only be shown once. Save it securely.
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 text-xs bg-green-100 dark:bg-green-900/40 text-green-900 dark:text-green-200 px-3 py-2 rounded font-mono break-all">
                  {createdKey}
                </code>
                <button
                  onClick={handleCopy}
                  className="flex-shrink-0 text-xs bg-green-600 hover:bg-green-700 text-white px-3 py-2 rounded transition-colors"
                >
                  {copied ? 'Copied!' : 'Copy'}
                </button>
              </div>
              <p className="text-xs text-green-600 dark:text-green-500 mt-2 font-mono">
                Usage: curl -H &quot;X-API-Key: {createdKey.slice(0, 12)}...&quot; https://your-panel.example.com/api/v1/...
              </p>
            </div>
          </div>
          <button
            onClick={() => setCreatedKey(null)}
            className="mt-2 text-xs text-green-700 dark:text-green-400 hover:underline"
          >
            Dismiss (key has been saved)
          </button>
        </div>
      )}

      {/* Create Form */}
      {showCreate && (
        <form onSubmit={handleCreate} className="mb-5 p-4 rounded-lg bg-gray-50 dark:bg-gray-800/50 space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Name *</label>
              <input
                value={newKeyName}
                onChange={(e) => setNewKeyName(e.target.value)}
                required
                placeholder="e.g. Terraform, Monitoring, CI/CD"
                className="input w-full"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Validity</label>
              <select
                value={newKeyExpiry}
                onChange={(e) => setNewKeyExpiry(e.target.value)}
                className="input w-full"
              >
                <option value="">Unlimited</option>
                <option value="30">30 days</option>
                <option value="90">90 days</option>
                <option value="180">180 days</option>
                <option value="365">1 year</option>
              </select>
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Description (optional)</label>
            <input
              value={newKeyDescription}
              onChange={(e) => setNewKeyDescription(e.target.value)}
              placeholder="What is this key used for?"
              className="input w-full"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
              Tenant scope {user?.is_superadmin ? '(optional for superadmin)' : '*'}
            </label>
            <select
              value={newKeyTenantId}
              onChange={(e) => setNewKeyTenantId(e.target.value)}
              required={!user?.is_superadmin}
              className="input w-full"
            >
              {user?.is_superadmin && <option value="">All tenants (global)</option>}
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Superadmin: optional global key (all tenants) or scoped. Tenant admin/user: must pick a tenant you belong to — API access is limited to that tenant and your role.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={isCreating || !newKeyName.trim() || (!user?.is_superadmin && !newKeyTenantId)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-2 text-xs font-medium text-white hover:bg-primary-700 disabled:opacity-50"
            >
              {isCreating ? 'Creating...' : 'Create API Key'}
            </button>
            <button
              type="button"
              onClick={() => setShowCreate(false)}
              className="text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 px-3 py-2"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {/* Key List */}
      {isLoading ? (
        <p className="text-sm text-gray-400 dark:text-gray-500 text-center py-4">Loading...</p>
      ) : keys.length === 0 ? (
        <div className="text-center py-6 text-gray-400 dark:text-gray-500">
          <ShieldCheckIcon className="h-10 w-10 mx-auto mb-2 opacity-30" />
          <p className="text-sm">No API keys</p>
          <p className="text-xs mt-1 text-gray-500 dark:text-gray-400">Create a key for external API access (Terraform, scripts, CI/CD)</p>
        </div>
      ) : (
        <div className="space-y-2">
          {keys.map((k) => (
            <div
              key={k.id}
              className={`flex items-center justify-between p-3 rounded-lg border ${
                k.is_active
                  ? 'border-gray-200 dark:border-gray-700'
                  : 'border-red-200 dark:border-red-900 bg-red-50/50 dark:bg-red-900/10'
              }`}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-gray-900 dark:text-white">{k.name}</span>
                  <code className="text-xs bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 px-1.5 py-0.5 rounded font-mono">
                    {k.key_prefix}...
                  </code>
                  {!k.is_active && (
                    <span className="text-xs bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 px-1.5 py-0.5 rounded">
                      Revoked
                    </span>
                  )}
                  {k.expires_at && new Date(k.expires_at) < new Date() && k.is_active && (
                    <span className="text-xs bg-yellow-100 dark:bg-yellow-900/30 text-yellow-600 dark:text-yellow-400 px-1.5 py-0.5 rounded">
                      Expired
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-4 mt-1 text-xs text-gray-400 dark:text-gray-500 flex-wrap">
                  <span>
                    Scope:{' '}
                    {k.tenant_id
                      ? (tenants.find((t) => t.id === k.tenant_id)?.name || 'Tenant')
                      : 'All tenants (global)'}
                  </span>
                  {k.description && <span>{k.description}</span>}
                  <span>Created: {new Date(k.created_at).toLocaleDateString()}</span>
                  {k.last_used_at && (
                    <span>Last used: {new Date(k.last_used_at).toLocaleDateString()}</span>
                  )}
                  {k.expires_at && (
                    <span>Expires: {new Date(k.expires_at).toLocaleDateString()}</span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-1 flex-shrink-0">
                {k.is_active ? (
                  <button
                    onClick={() => handleRevoke(k.id, k.name)}
                    className="text-xs text-red-600 hover:text-red-800 dark:text-red-400 px-2 py-1 rounded hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                    title="Revoke key"
                  >
                    <TrashIcon className="h-4 w-4" />
                  </button>
                ) : (
                  <button
                    onClick={() => handleDelete(k.id, k.name)}
                    className="text-xs text-red-600 hover:text-red-800 dark:text-red-400 px-2 py-1 rounded hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                    title="Delete permanently"
                  >
                    <TrashIcon className="h-4 w-4" />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

