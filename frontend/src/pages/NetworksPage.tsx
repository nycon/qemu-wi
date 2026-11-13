import { useCallback, useEffect, useState } from 'react';
import {
  PlusIcon,
  TrashIcon,
  PencilIcon,
  GlobeAltIcon,
  ShieldCheckIcon,
  ServerStackIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { useTenantStore } from '@/store/tenantStore';
import { useAuthStore } from '@/store/authStore';
import { tenantApi, tenantNetworkApi, type PublicIpAssignment } from '@/services/api';
import type { IPPool } from '@/types';

function poolIcon(type: string) {
  if (type === 'internal') return ServerStackIcon;
  return ShieldCheckIcon;
}

function poolLabel(type: string) {
  return type === 'internal' ? 'Internal' : 'NAT';
}

/** Strip tenant slug prefix from pool name for the edit form. */
function shortNetworkName(poolName: string, tenantSlug?: string) {
  if (tenantSlug && poolName.startsWith(`${tenantSlug}-`)) {
    return poolName.slice(tenantSlug.length + 1);
  }
  return poolName;
}

const emptyNetForm = {
  name: '',
  pool_type: 'nat' as 'nat' | 'internal',
  description: '',
  network: '',
  gateway: '',
  dns_primary: '8.8.8.8',
  dns_secondary: '1.1.1.1',
  internal_dhcp_enabled: true,
  dhcp_start: '',
  dhcp_end: '',
  advanced: false,
};

export function NetworksPage() {
  const { currentTenant } = useTenantStore();
  const user = useAuthStore((s) => s.user);
  const [pools, setPools] = useState<IPPool[]>([]);
  const [publicIps, setPublicIps] = useState<PublicIpAssignment[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingPool, setEditingPool] = useState<IPPool | null>(null);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState(emptyNetForm);

  const canManage = Boolean(user);

  const loadData = useCallback(async () => {
    if (!currentTenant) return;
    setIsLoading(true);
    try {
      const [nets, ips] = await Promise.all([
        tenantNetworkApi.list(currentTenant.id),
        tenantApi.listPublicIps(currentTenant.id).catch(() => [] as PublicIpAssignment[]),
      ]);
      setPools(nets);
      setPublicIps(ips);
    } catch {
      toast.error('Networks could not be loaded');
    } finally {
      setIsLoading(false);
    }
  }, [currentTenant]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const closeForm = () => {
    setShowForm(false);
    setEditingPool(null);
    setForm(emptyNetForm);
  };

  const openCreate = () => {
    setEditingPool(null);
    setForm(emptyNetForm);
    setShowForm(true);
  };

  const openEdit = (pool: IPPool) => {
    setEditingPool(pool);
    setForm({
      name: shortNetworkName(pool.name, currentTenant?.slug),
      pool_type: pool.pool_type === 'internal' ? 'internal' : 'nat',
      description: pool.description || '',
      network: pool.network,
      gateway: pool.gateway,
      dns_primary: pool.dns_primary || '8.8.8.8',
      dns_secondary: pool.dns_secondary || '',
      internal_dhcp_enabled: pool.internal_dhcp_enabled,
      dhcp_start: pool.dhcp_start || '',
      dhcp_end: pool.dhcp_end || '',
      advanced: true,
    });
    setShowForm(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentTenant) return;
    setSaving(true);
    try {
      if (editingPool) {
        await tenantNetworkApi.update(currentTenant.id, editingPool.id, {
          name: form.name.trim(),
          description: form.description.trim() || undefined,
          dns_primary: form.dns_primary || '8.8.8.8',
          dns_secondary: form.dns_secondary || undefined,
          internal_dhcp_enabled: form.internal_dhcp_enabled,
          dhcp_start: form.internal_dhcp_enabled
            ? form.dhcp_start.trim() || null
            : null,
          dhcp_end: form.internal_dhcp_enabled ? form.dhcp_end.trim() || null : null,
        });
        toast.success('Network updated');
      } else {
        const payload: Parameters<typeof tenantNetworkApi.create>[1] = {
          name: form.name.trim(),
          pool_type: form.pool_type,
          description: form.description.trim() || undefined,
          dns_primary: form.dns_primary || '8.8.8.8',
          dns_secondary: form.dns_secondary || undefined,
          internal_dhcp_enabled: form.internal_dhcp_enabled,
        };
        if (form.advanced && form.network.trim() && form.gateway.trim()) {
          payload.network = form.network.trim();
          payload.gateway = form.gateway.trim();
          if (form.internal_dhcp_enabled) {
            payload.dhcp_start = form.dhcp_start.trim() || undefined;
            payload.dhcp_end = form.dhcp_end.trim() || undefined;
          }
        }
        await tenantNetworkApi.create(currentTenant.id, payload);
        toast.success('Network created');
      }
      closeForm();
      loadData();
    } catch (error: unknown) {
      const msg =
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (editingPool ? 'Could not update network' : 'Could not create network');
      toast.error(typeof msg === 'string' ? msg : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (pool: IPPool) => {
    if (!currentTenant) return;
    if (!window.confirm(`Delete network "${pool.name}"?`)) return;
    try {
      await tenantNetworkApi.delete(currentTenant.id, pool.id);
      toast.success('Network deleted');
      if (editingPool?.id === pool.id) closeForm();
      loadData();
    } catch (error: unknown) {
      const msg =
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Could not delete network';
      toast.error(typeof msg === 'string' ? msg : 'Could not delete network');
    }
  };

  if (!currentTenant) {
    return (
      <div className="p-6">
        <p className="text-gray-500 dark:text-gray-400">Select a tenant first.</p>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Networks</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            NAT and internal networks for this tenant. Public IPs are assigned by the platform admin.
          </p>
        </div>
        {canManage && (
          <button
            type="button"
            onClick={() => (showForm && !editingPool ? closeForm() : openCreate())}
            className="btn-primary inline-flex items-center gap-2"
          >
            <PlusIcon className="h-5 w-5" />
            New NAT / Internal
          </button>
        )}
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider">
          Assigned public IPs ({publicIps.length})
        </h2>
        {isLoading ? (
          <p className="text-gray-500 dark:text-gray-400">Loading…</p>
        ) : publicIps.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">
            No public IPs assigned yet. Ask a platform admin to assign them under Admin → Tenants.
          </p>
        ) : (
          <div className="grid gap-3">
            {publicIps.map((ip) => (
              <div key={ip.id} className="card px-4 py-3 flex items-center gap-3">
                <GlobeAltIcon className="h-6 w-6 text-primary-500 shrink-0" />
                <div>
                  <div className="text-sm font-medium text-gray-900 dark:text-white font-mono">
                    {ip.ip_address}
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400">
                    {ip.status === 'allocated'
                      ? 'In use on a VM'
                      : 'Reserved · available for new VMs'}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {showForm && (
        <div className="card p-6">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            {editingPool ? `Edit network – ${editingPool.name}` : 'New NAT / Internal network'}
          </h2>
          <form onSubmit={handleSubmit} className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name</label>
              <input
                required
                minLength={2}
                value={form.name}
                onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                className="input-field"
                placeholder="e.g. web-dmz"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Type</label>
              {editingPool ? (
                <input
                  value={poolLabel(form.pool_type)}
                  disabled
                  className="input-field bg-gray-100 dark:bg-gray-800 cursor-not-allowed"
                />
              ) : (
                <select
                  value={form.pool_type}
                  onChange={(e) =>
                    setForm((p) => ({ ...p, pool_type: e.target.value as 'nat' | 'internal' }))
                  }
                  className="input-field"
                >
                  <option value="nat">NAT (internet via host)</option>
                  <option value="internal">Internal (VM-to-VM only)</option>
                </select>
              )}
            </div>
            <div className="md:col-span-2">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Description (optional)
              </label>
              <input
                value={form.description}
                onChange={(e) => setForm((p) => ({ ...p, description: e.target.value }))}
                className="input-field"
              />
            </div>

            {editingPool ? (
              <>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Network (CIDR)
                  </label>
                  <input
                    value={form.network}
                    disabled
                    className="input-field font-mono bg-gray-100 dark:bg-gray-800 cursor-not-allowed"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Gateway
                  </label>
                  <input
                    value={form.gateway}
                    disabled
                    className="input-field font-mono bg-gray-100 dark:bg-gray-800 cursor-not-allowed"
                  />
                </div>
                <p className="md:col-span-2 text-xs text-gray-500 dark:text-gray-400">
                  Type, CIDR and gateway cannot be changed after creation.
                </p>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    DNS primary
                  </label>
                  <input
                    value={form.dns_primary}
                    onChange={(e) => setForm((p) => ({ ...p, dns_primary: e.target.value }))}
                    className="input-field font-mono"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    DNS secondary
                  </label>
                  <input
                    value={form.dns_secondary}
                    onChange={(e) => setForm((p) => ({ ...p, dns_secondary: e.target.value }))}
                    className="input-field font-mono"
                  />
                </div>
                <div className="md:col-span-2">
                  <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                    <input
                      type="checkbox"
                      checked={form.internal_dhcp_enabled}
                      onChange={(e) =>
                        setForm((p) => ({ ...p, internal_dhcp_enabled: e.target.checked }))
                      }
                      className="rounded border-gray-300"
                    />
                    DHCP enabled
                  </label>
                </div>
                {form.internal_dhcp_enabled && (
                  <>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        DHCP start (optional)
                      </label>
                      <input
                        value={form.dhcp_start}
                        onChange={(e) => setForm((p) => ({ ...p, dhcp_start: e.target.value }))}
                        className="input-field font-mono"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        DHCP end (optional)
                      </label>
                      <input
                        value={form.dhcp_end}
                        onChange={(e) => setForm((p) => ({ ...p, dhcp_end: e.target.value }))}
                        className="input-field font-mono"
                      />
                    </div>
                  </>
                )}
              </>
            ) : (
              <>
                <div className="md:col-span-2">
                  <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                    <input
                      type="checkbox"
                      checked={form.advanced}
                      onChange={(e) => setForm((p) => ({ ...p, advanced: e.target.checked }))}
                      className="rounded border-gray-300"
                    />
                    Advanced settings (CIDR, gateway, DNS, DHCP)
                  </label>
                </div>
                {form.advanced && (
                  <>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        Network (CIDR)
                      </label>
                      <input
                        value={form.network}
                        onChange={(e) => setForm((p) => ({ ...p, network: e.target.value }))}
                        className="input-field font-mono"
                        placeholder="10.42.0.0/24"
                        required={form.advanced}
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        Gateway
                      </label>
                      <input
                        value={form.gateway}
                        onChange={(e) => setForm((p) => ({ ...p, gateway: e.target.value }))}
                        className="input-field font-mono"
                        placeholder="10.42.0.1"
                        required={form.advanced}
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        DNS primary
                      </label>
                      <input
                        value={form.dns_primary}
                        onChange={(e) => setForm((p) => ({ ...p, dns_primary: e.target.value }))}
                        className="input-field font-mono"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        DNS secondary
                      </label>
                      <input
                        value={form.dns_secondary}
                        onChange={(e) => setForm((p) => ({ ...p, dns_secondary: e.target.value }))}
                        className="input-field font-mono"
                      />
                    </div>
                    <div className="md:col-span-2">
                      <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                        <input
                          type="checkbox"
                          checked={form.internal_dhcp_enabled}
                          onChange={(e) =>
                            setForm((p) => ({ ...p, internal_dhcp_enabled: e.target.checked }))
                          }
                          className="rounded border-gray-300"
                        />
                        DHCP enabled
                      </label>
                    </div>
                    {form.internal_dhcp_enabled && (
                      <>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                            DHCP start (optional)
                          </label>
                          <input
                            value={form.dhcp_start}
                            onChange={(e) => setForm((p) => ({ ...p, dhcp_start: e.target.value }))}
                            className="input-field font-mono"
                          />
                        </div>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                            DHCP end (optional)
                          </label>
                          <input
                            value={form.dhcp_end}
                            onChange={(e) => setForm((p) => ({ ...p, dhcp_end: e.target.value }))}
                            className="input-field font-mono"
                          />
                        </div>
                      </>
                    )}
                  </>
                )}
                {!form.advanced && (
                  <p className="md:col-span-2 text-xs text-gray-500 dark:text-gray-400">
                    Without advanced settings, CIDR and gateway are assigned automatically.
                  </p>
                )}
              </>
            )}

            <div className="md:col-span-2 flex justify-end gap-3">
              <button type="button" onClick={closeForm} className="btn-secondary">
                Cancel
              </button>
              <button type="submit" disabled={saving} className="btn-primary">
                {saving
                  ? editingPool
                    ? 'Saving…'
                    : 'Creating…'
                  : editingPool
                    ? 'Save'
                    : 'Create'}
              </button>
            </div>
          </form>
        </div>
      )}

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider">
          NAT & Internal networks
        </h2>
        {isLoading ? (
          <p className="text-gray-500 dark:text-gray-400 py-8 text-center">Loading…</p>
        ) : pools.length === 0 ? (
          <div className="card px-6 py-8 text-center space-y-2">
            <p className="text-gray-600 dark:text-gray-300">
              No NAT or internal network yet.
            </p>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              You can use assigned public IPs for VMs immediately, or create a NAT/Internal network when you need one.
            </p>
          </div>
        ) : (
          <div className="grid gap-3">
            {pools.map((pool) => {
              const Icon = poolIcon(pool.pool_type);
              const isEditing = editingPool?.id === pool.id;
              return (
                <div
                  key={pool.id}
                  className={`card px-4 py-3 flex items-center justify-between gap-4 flex-wrap ${
                    isEditing ? 'ring-2 ring-primary-500' : ''
                  }`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <Icon className="h-6 w-6 text-primary-500 shrink-0" />
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-gray-900 dark:text-white truncate">
                        {pool.name}
                      </div>
                      <div className="text-xs text-gray-500 dark:text-gray-400">
                        {poolLabel(pool.pool_type)} · {pool.network} · gateway {pool.gateway} ·{' '}
                        {pool.available_ips} free / {pool.total_ips}
                        {pool.internal_dhcp_enabled ? ' · DHCP on' : ' · DHCP off'}
                      </div>
                    </div>
                  </div>
                  {canManage && (
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => openEdit(pool)}
                        className="inline-flex items-center gap-1 text-sm text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 px-2 py-1"
                      >
                        <PencilIcon className="h-4 w-4" />
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(pool)}
                        className="inline-flex items-center gap-1 text-sm text-red-600 hover:text-red-700 px-2 py-1"
                      >
                        <TrashIcon className="h-4 w-4" />
                        Delete
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
