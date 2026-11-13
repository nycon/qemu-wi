import { useEffect, useState } from 'react';
import {
  PlusIcon,
  EyeIcon,
  ArrowUturnLeftIcon,
  GlobeAltIcon,
  TrashIcon,
  PencilIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { ipamApi, systemApi } from '@/services/api';
import type { IPPool, IPAllocation } from '@/types';

interface HostNetwork {
  bridges: string[];
  libvirt_networks: { name: string; active: boolean; autostart: boolean }[];
}

const emptyForm = {
  name: '',
  description: '',
  ip_version: 'ipv4',
  network: '',
  gateway: '',
  dns_primary: '8.8.8.8',
  dns_secondary: '1.1.1.1',
  vlan_id: '',
  bridge: '',
  is_active: true,
};

export function IPPoolsPage() {
  const [pools, setPools] = useState<IPPool[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [editingPool, setEditingPool] = useState<IPPool | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [selectedPool, setSelectedPool] = useState<IPPool | null>(null);
  const [allocations, setAllocations] = useState<IPAllocation[]>([]);
  const [allocLoading, setAllocLoading] = useState(false);
  const [hostNetworks, setHostNetworks] = useState<HostNetwork | null>(null);
  const [form, setForm] = useState(emptyForm);

  const loadPools = async () => {
    setIsLoading(true);
    try {
      const all = await ipamApi.listPools();
      setPools(all.filter((p) => p.pool_type === 'external'));
    } catch {
      toast.error('Public IP pools could not be loaded');
    } finally {
      setIsLoading(false);
    }
  };

  const loadNetworks = async () => {
    try {
      const result = await systemApi.getNetworks();
      setHostNetworks(result.data);
    } catch {
      // silent
    }
  };

  useEffect(() => {
    loadPools();
    loadNetworks();
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  };

  const handleCheckboxChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, checked } = e.target;
    setForm((prev) => ({ ...prev, [name]: checked }));
  };

  const openCreate = () => {
    setEditingPool(null);
    setForm({ ...emptyForm });
    setShowForm(true);
  };

  const openEdit = (pool: IPPool) => {
    setEditingPool(pool);
    setForm({
      name: pool.name,
      description: pool.description || '',
      ip_version: pool.ip_version,
      network: pool.network,
      gateway: pool.gateway,
      dns_primary: pool.dns_primary,
      dns_secondary: pool.dns_secondary || '',
      vlan_id: pool.vlan_id != null ? String(pool.vlan_id) : '',
      bridge: pool.bridge || '',
      is_active: pool.is_active,
    });
    setShowForm(true);
  };

  const closeForm = () => {
    setShowForm(false);
    setEditingPool(null);
    setForm(emptyForm);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      if (editingPool) {
        await ipamApi.updatePool(editingPool.id, {
          name: form.name,
          description: form.description || undefined,
          dns_primary: form.dns_primary,
          dns_secondary: form.dns_secondary || undefined,
          vlan_id: form.vlan_id ? Number(form.vlan_id) : null,
          is_active: form.is_active,
          bridge: form.bridge,
        } as Partial<IPPool>);
        toast.success('Public IP pool updated');
      } else {
        await ipamApi.createPool({
          name: form.name,
          description: form.description || undefined,
          pool_type: 'external',
          ip_version: form.ip_version,
          network: form.network,
          gateway: form.gateway,
          dns_primary: form.dns_primary,
          dns_secondary: form.dns_secondary || undefined,
          vlan_id: form.vlan_id ? Number(form.vlan_id) : undefined,
          bridge: form.bridge,
        } as Partial<IPPool>);
        toast.success('Public IP pool created');
      }
      closeForm();
      loadPools();
      loadNetworks();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (editingPool ? 'Pool could not be updated' : 'Pool could not be created');
      toast.error(typeof msg === 'string' ? msg : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const handleDeletePool = async (pool: IPPool) => {
    if (!confirm(`Really delete public pool "${pool.name}"? All IPs will be released.`)) return;
    try {
      await ipamApi.deletePool(pool.id);
      toast.success('Pool deleted');
      if (selectedPool?.id === pool.id) setSelectedPool(null);
      if (editingPool?.id === pool.id) closeForm();
      loadPools();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Delete failed';
      toast.error(typeof msg === 'string' ? msg : 'Delete failed');
    }
  };

  const viewAllocations = async (pool: IPPool) => {
    setSelectedPool(pool);
    setAllocLoading(true);
    try {
      setAllocations(await ipamApi.listAllocations(pool.id));
    } catch {
      toast.error('Allocations could not be loaded');
    } finally {
      setAllocLoading(false);
    }
  };

  const handleRelease = async (allocationId: string) => {
    if (!selectedPool || !confirm('Really release IP?')) return;
    try {
      await ipamApi.releaseIp(selectedPool.id, allocationId);
      toast.success('IP released');
      viewAllocations(selectedPool);
      loadPools();
    } catch {
      toast.error('Release failed');
    }
  };

  return (
    <div>
      <div className="sm:flex sm:items-center sm:justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Public IP Pools</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Platform-wide public networks. Assign concrete IPs to tenants under Tenants.
            NAT and internal networks are created by each tenant under Networks.
          </p>
        </div>
        <button onClick={openCreate} className="btn-primary mt-4 sm:mt-0">
          <PlusIcon className="h-5 w-5 mr-1.5" />
          Add public network
        </button>
      </div>

      <div className="mb-6 rounded-xl bg-primary-50 dark:bg-primary-900/10 border border-primary-200 dark:border-primary-800 p-4">
        <div className="flex gap-3">
          <GlobeAltIcon className="h-6 w-6 text-primary-500 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-gray-700 dark:text-gray-300">
            <p className="font-medium text-primary-700 dark:text-primary-400 mb-1">Public IPs only</p>
            <p>
              Create external CIDR pools here, then pick free IPs when creating or editing a tenant.
              Tenants only see the public IPs you assign — not the whole pool.
            </p>
          </div>
        </div>
      </div>

      {showForm && (
        <div className="card p-6 mb-8">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            {editingPool ? `Edit public pool – ${editingPool.name}` : 'New public network'}
          </h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            {editingPool && (
              <div className="rounded-lg bg-gray-50 dark:bg-gray-800/50 px-3 py-2 text-xs text-gray-500">
                Type: <strong className="text-gray-700 dark:text-gray-300">EXTERNAL</strong>
                {' · '}Subnet:{' '}
                <strong className="font-mono text-gray-700 dark:text-gray-300">{form.network}</strong>
                {' · '}Gateway:{' '}
                <strong className="font-mono text-gray-700 dark:text-gray-300">{form.gateway}</strong>
                <span className="block mt-1">Network CIDR and gateway cannot be changed after creation.</span>
              </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name *</label>
                <input
                  name="name"
                  value={form.name}
                  onChange={handleChange}
                  required
                  className="input-field"
                  placeholder="Public-IPv4"
                />
              </div>
              {!editingPool && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Subnet (CIDR) *
                    </label>
                    <input
                      name="network"
                      value={form.network}
                      onChange={handleChange}
                      required
                      className="input-field font-mono"
                      placeholder="203.0.113.0/28"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Gateway *
                    </label>
                    <input
                      name="gateway"
                      value={form.gateway}
                      onChange={handleChange}
                      required
                      className="input-field font-mono"
                      placeholder="203.0.113.1"
                    />
                  </div>
                </>
              )}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">DNS Primary</label>
                <input name="dns_primary" value={form.dns_primary} onChange={handleChange} className="input-field" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">DNS Secondary</label>
                <input
                  name="dns_secondary"
                  value={form.dns_secondary}
                  onChange={handleChange}
                  className="input-field"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Host Bridge *</label>
                {hostNetworks && hostNetworks.bridges.length > 0 ? (
                  <select name="bridge" value={form.bridge} onChange={handleChange} required className="input-field">
                    <option value="">Select bridge...</option>
                    {hostNetworks.bridges.map((b) => (
                      <option key={b} value={b}>
                        {b}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    name="bridge"
                    value={form.bridge}
                    onChange={handleChange}
                    required
                    className="input-field"
                    placeholder="br0"
                  />
                )}
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  VLAN ID (optional)
                </label>
                <input
                  name="vlan_id"
                  value={form.vlan_id}
                  onChange={handleChange}
                  className="input-field"
                  placeholder="e.g. 100"
                />
              </div>
              {editingPool && (
                <div className="flex items-center gap-2 pt-6">
                  <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                    <input
                      type="checkbox"
                      name="is_active"
                      checked={form.is_active}
                      onChange={handleCheckboxChange}
                      className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                    />
                    Active
                  </label>
                </div>
              )}
              <div className="md:col-span-2 lg:col-span-3">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Description</label>
                <textarea
                  name="description"
                  value={form.description}
                  onChange={handleChange}
                  className="input-field"
                  rows={2}
                />
              </div>
            </div>

            <div className="flex justify-end gap-3">
              <button type="button" onClick={closeForm} className="btn-secondary">
                Cancel
              </button>
              <button type="submit" disabled={saving} className="btn-primary">
                {saving ? (editingPool ? 'Saving...' : 'Creating...') : editingPool ? 'Save' : 'Create pool'}
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-8">
        {isLoading ? (
          <div className="col-span-full text-center py-12 text-gray-500 dark:text-gray-400">Loading...</div>
        ) : pools.length === 0 ? (
          <div className="col-span-full text-center py-12 text-gray-500 dark:text-gray-400">
            <GlobeAltIcon className="h-10 w-10 mx-auto mb-2 text-gray-400" />
            <p>No public IP pools yet</p>
            <p className="text-xs mt-1">Add an external CIDR to assign IPs to tenants</p>
          </div>
        ) : (
          pools.map((pool) => {
            const usedPercent =
              pool.total_ips > 0 ? Math.round((pool.allocated_ips / pool.total_ips) * 100) : 0;
            return (
              <div
                key={pool.id}
                className={`card p-5 transition-all cursor-pointer ${
                  selectedPool?.id === pool.id || editingPool?.id === pool.id
                    ? 'ring-2 ring-primary-500'
                    : 'hover:shadow-md'
                }`}
                onClick={() => openEdit(pool)}
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <GlobeAltIcon className="h-5 w-5 text-green-500" />
                    <div>
                      <h3 className="text-sm font-semibold text-gray-900 dark:text-white">{pool.name}</h3>
                      <span className="text-xs font-medium text-green-600">Public / External</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                        pool.is_active
                          ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
                          : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
                      }`}
                    >
                      {pool.is_active ? 'Active' : 'Inactive'}
                    </span>
                    <button
                      onClick={() => openEdit(pool)}
                      className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
                      title="Edit"
                    >
                      <PencilIcon className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => viewAllocations(pool)}
                      className="p-1 text-gray-400 hover:text-primary-500 transition-colors"
                      title="View IPs"
                    >
                      <EyeIcon className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => handleDeletePool(pool)}
                      className="p-1 text-gray-400 hover:text-red-500 dark:hover:text-red-400 transition-colors"
                      title="Delete"
                    >
                      <TrashIcon className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                <div className="space-y-1 text-xs text-gray-500 mb-3">
                  <p>
                    <span className="font-medium text-gray-600 dark:text-gray-400">Subnet:</span> {pool.network}
                  </p>
                  <p>
                    <span className="font-medium text-gray-600 dark:text-gray-400">Gateway:</span> {pool.gateway}
                  </p>
                  <p>
                    <span className="font-medium text-gray-600 dark:text-gray-400">Bridge:</span> {pool.bridge}
                  </p>
                  {pool.description && <p className="text-gray-400">{pool.description}</p>}
                </div>

                <div>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-gray-500">{pool.allocated_ips} used</span>
                    <span className="text-gray-500">{pool.available_ips} free</span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-1.5 dark:bg-gray-700">
                    <div
                      className={`${
                        usedPercent >= 90
                          ? 'bg-red-500'
                          : usedPercent >= 70
                            ? 'bg-yellow-500'
                            : 'bg-primary-500'
                      } h-1.5 rounded-full transition-all`}
                      style={{ width: `${usedPercent}%` }}
                    />
                  </div>
                  <p className="text-xs text-gray-400 mt-1">
                    {pool.total_ips} IPs, {usedPercent}% used
                  </p>
                </div>
              </div>
            );
          })
        )}
      </div>

      {selectedPool && (
        <div className="card">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <h2 className="text-base font-semibold text-gray-900 dark:text-white">
              IP Allocations – {selectedPool.name}
            </h2>
            <button onClick={() => setSelectedPool(null)} className="text-xs text-gray-400 hover:text-gray-600">
              Close
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-800/50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    IP Address
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Tenant
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    VM
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Created
                  </th>
                  <th className="relative px-6 py-3">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {allocLoading ? (
                  <tr>
                    <td colSpan={6} className="px-6 py-8 text-center text-gray-500 dark:text-gray-400">
                      Loading...
                    </td>
                  </tr>
                ) : allocations.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-6 py-8 text-center text-gray-500 dark:text-gray-400">
                      No allocations
                    </td>
                  </tr>
                ) : (
                  allocations.map((alloc) => (
                    <tr key={alloc.id} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                      <td className="px-6 py-3 whitespace-nowrap text-sm font-mono font-medium text-gray-900 dark:text-white">
                        {alloc.ip_address}
                      </td>
                      <td className="px-6 py-3 whitespace-nowrap">
                        <span
                          className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
                            alloc.status === 'allocated'
                              ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400'
                              : alloc.status === 'reserved'
                                ? 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400'
                                : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
                          }`}
                        >
                          {alloc.status === 'allocated'
                            ? 'Used'
                            : alloc.status === 'reserved'
                              ? 'Reserved'
                              : 'Free'}
                        </span>
                      </td>
                      <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400 font-mono">
                        {alloc.tenant_id ? `${alloc.tenant_id.slice(0, 8)}…` : '—'}
                      </td>
                      <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                        {alloc.vm_id ? `${alloc.vm_id.slice(0, 8)}…` : '—'}
                      </td>
                      <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                        {new Date(alloc.created_at).toLocaleString()}
                      </td>
                      <td className="px-6 py-3 whitespace-nowrap text-right">
                        {alloc.status === 'allocated' && (
                          <button
                            onClick={() => handleRelease(alloc.id)}
                            className="text-yellow-600 hover:text-yellow-800 dark:text-yellow-400 dark:hover:text-yellow-300 p-1"
                            title="Release IP"
                          >
                            <ArrowUturnLeftIcon className="h-4 w-4" />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
