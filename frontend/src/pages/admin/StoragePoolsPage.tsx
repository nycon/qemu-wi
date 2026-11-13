import { useEffect, useState } from 'react';
import { PlusIcon, PencilIcon, TrashIcon, ServerStackIcon } from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { storageApi, tenantApi } from '@/services/api';
import type { StoragePool, Tenant } from '@/types';

export function StoragePoolsPage() {
  const [pools, setPools] = useState<StoragePool[]>([]);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [editingPool, setEditingPool] = useState<StoragePool | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [form, setForm] = useState({
    name: '',
    pool_type: 'directory',
    path: '/var/lib/qemu-interface/storage',
    total_size_gb: 500,
    is_default: false,
    is_active: true,
    description: '',
    tenant_id: '',
  });

  const tenantName = (id: string | null | undefined) => {
    if (!id) return null;
    return tenants.find((t) => t.id === id)?.name ?? id.slice(0, 8);
  };

  const loadData = async () => {
    setIsLoading(true);
    try {
      const [poolList, tenantList] = await Promise.all([
        storageApi.listPools(),
        tenantApi.list().catch(() => [] as Tenant[]),
      ]);
      setPools(poolList);
      setTenants(tenantList);
    } catch {
      toast.error('Pools could not be loaded');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => { loadData(); }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const { name, value, type } = e.target;
    setForm((prev) => ({
      ...prev,
      [name]: type === 'number' ? Number(value) : type === 'checkbox' ? (e.target as HTMLInputElement).checked : value,
    }));
  };

  const openCreate = () => {
    setEditingPool(null);
    setForm({
      name: '',
      pool_type: 'directory',
      path: '/var/lib/qemu-interface/storage',
      total_size_gb: 500,
      is_default: pools.length === 0,
      is_active: true,
      description: '',
      tenant_id: '',
    });
    setShowForm(true);
  };

  const openEdit = (pool: StoragePool) => {
    setEditingPool(pool);
    setForm({
      name: pool.name,
      pool_type: pool.pool_type,
      path: pool.path,
      total_size_gb: pool.total_size_gb,
      is_default: pool.is_default,
      is_active: pool.is_active,
      description: pool.description || '',
      tenant_id: pool.tenant_id || '',
    });
    setShowForm(true);
  };

  const handleDelete = async (pool: StoragePool) => {
    if (!confirm(`Really delete storage pool "${pool.name}"?`)) return;
    try {
      await storageApi.deletePool(pool.id);
      toast.success('Pool deleted');
      loadData();
    } catch (error: unknown) {
      const msg = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Delete failed';
      toast.error(msg);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const tenantPayload = form.tenant_id ? form.tenant_id : null;
    try {
      if (editingPool) {
        await storageApi.updatePool(editingPool.id, {
          name: form.name,
          path: form.path,
          total_size_gb: form.total_size_gb,
          is_default: form.is_default,
          is_active: form.is_active,
          description: form.description || undefined,
          tenant_id: tenantPayload,
        } as Partial<StoragePool>);
        toast.success('Pool updated');
      } else {
        await storageApi.createPool({
          name: form.name,
          pool_type: form.pool_type,
          path: form.path,
          total_size_gb: form.total_size_gb,
          is_default: form.is_default,
          description: form.description || undefined,
          tenant_id: tenantPayload,
        } as Partial<StoragePool>);
        toast.success('Pool created');
      }
      setShowForm(false);
      setEditingPool(null);
      loadData();
    } catch (error: unknown) {
      const msg = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Action failed';
      toast.error(msg);
    }
  };

  const totalVmUsed = pools.reduce((acc, p) => acc + p.used_size_gb, 0);
  const totalFree = pools.reduce((acc, p) => acc + (p.free_size_gb ?? 0), 0);
  const activePools = pools.filter((p) => p.is_active).length;

  return (
    <div>
      <div className="sm:flex sm:items-center sm:justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Storage Pools</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {pools.length} Pool(s) — {activePools} active — {totalVmUsed} GB VM storage — {totalFree} GB free
          </p>
        </div>
        <button onClick={openCreate} className="btn-primary mt-4 sm:mt-0">
          <PlusIcon className="h-5 w-5 mr-1.5" />
          Create pool
        </button>
      </div>

      {showForm && (
        <div className="card p-6 mb-8">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            {editingPool ? 'Edit pool' : 'New Storage Pool'}
          </h2>
          <form onSubmit={handleSubmit} className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name *</label>
              <input name="name" value={form.name} onChange={handleChange} required className="input-field" />
            </div>
            {!editingPool ? (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Type</label>
                <select name="pool_type" value={form.pool_type} onChange={handleChange} className="input-field">
                  <option value="directory">Directory</option>
                  <option value="lvm_thin">LVM Thin</option>
                  <option value="zfs">ZFS</option>
                </select>
              </div>
            ) : (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Type</label>
                <input value={form.pool_type.toUpperCase()} disabled className="input-field bg-gray-100 dark:bg-gray-800 cursor-not-allowed" />
              </div>
            )}
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Path *</label>
              <input name="path" value={form.path} onChange={handleChange} required className="input-field font-mono text-sm" />
              <p className="mt-1 text-xs text-gray-400">Filesystem path for VM disk storage</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Total Size (GB)</label>
              <input name="total_size_gb" type="number" min={10} value={form.total_size_gb} onChange={handleChange} className="input-field" />
              {editingPool && form.total_size_gb < editingPool.used_size_gb && (
                <p className="mt-1 text-xs text-red-500">Cannot be less than current usage ({editingPool.used_size_gb} GB)</p>
              )}
            </div>
            <div className="md:col-span-2">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Tenant assignment</label>
              <select name="tenant_id" value={form.tenant_id} onChange={handleChange} className="input-field">
                <option value="">Shared (all tenants)</option>
                {tenants.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
              <p className="mt-1 text-xs text-gray-400">
                Shared pools are visible to every tenant. Assigning a tenant makes the pool exclusive to that tenant.
              </p>
            </div>
            <div className="flex items-center gap-6 pt-2">
              <label className="flex items-center gap-2 cursor-pointer">
                <input name="is_default" type="checkbox" checked={form.is_default} onChange={handleChange} className="rounded border-gray-300 dark:border-gray-600 text-primary-600" />
                <span className="text-sm text-gray-700 dark:text-gray-300">Default pool</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input name="is_active" type="checkbox" checked={form.is_active} onChange={handleChange} className="rounded border-gray-300 dark:border-gray-600 text-primary-600" />
                <span className="text-sm text-gray-700 dark:text-gray-300">Active</span>
              </label>
            </div>
            <div className="md:col-span-2">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Description</label>
              <textarea name="description" value={form.description} onChange={handleChange} className="input-field" rows={2} />
            </div>
            <div className="md:col-span-2 flex justify-end gap-3">
              <button type="button" onClick={() => { setShowForm(false); setEditingPool(null); }} className="btn-secondary">Cancel</button>
              <button type="submit" className="btn-primary">{editingPool ? 'Save' : 'Create'}</button>
            </div>
          </form>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {isLoading ? (
          <div className="col-span-full text-center py-12 text-gray-500 dark:text-gray-400">Loading...</div>
        ) : pools.length === 0 ? (
          <div className="col-span-full text-center py-12">
            <ServerStackIcon className="h-12 w-12 mx-auto text-gray-300 dark:text-gray-600 mb-3" />
            <p className="text-gray-500 dark:text-gray-400">No Storage Pools configured</p>
            <p className="text-sm text-gray-400 dark:text-gray-500 mt-1">Create your first pool to specify where VM disks are stored.</p>
          </div>
        ) : pools.map((pool) => {
          const freeGb = pool.free_size_gb ?? (pool.total_size_gb - pool.used_size_gb);
          const diskUsedGb = pool.total_size_gb - freeGb;
          const diskUsedPercent = pool.total_size_gb > 0 ? Math.round((diskUsedGb / pool.total_size_gb) * 100) : 0;
          const poolPercent = pool.total_size_gb > 0 ? Math.round((pool.used_size_gb / pool.total_size_gb) * 100) : 0;
          const barColor = diskUsedPercent >= 90 ? 'bg-red-500' : diskUsedPercent >= 70 ? 'bg-yellow-500' : 'bg-green-500';
          const assigned = tenantName(pool.tenant_id);
          return (
            <div key={pool.id} className={`card p-6 ${!pool.is_active ? 'opacity-60' : ''}`}>
              <div className="flex items-center justify-between mb-3">
                <div className="min-w-0 flex-1">
                  <h3 className="text-lg font-semibold text-gray-900 dark:text-white truncate">{pool.name}</h3>
                  <p className="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider">{pool.pool_type}</p>
                </div>
                <div className="flex items-center gap-1.5 flex-shrink-0 ml-2">
                  {pool.is_default && (
                    <span className="text-xs bg-primary-100 text-primary-700 dark:bg-primary-900/30 dark:text-primary-400 rounded-full px-2 py-0.5 font-medium">
                      Default
                    </span>
                  )}
                  <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${pool.is_active ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'}`}>
                    {pool.is_active ? 'Active' : 'Inactive'}
                  </span>
                  <button onClick={() => openEdit(pool)} className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1" title="Edit">
                    <PencilIcon className="h-4 w-4" />
                  </button>
                  <button onClick={() => handleDelete(pool)} className="text-red-400 hover:text-red-600 dark:hover:text-red-400 p-1" title="Delete">
                    <TrashIcon className="h-4 w-4" />
                  </button>
                </div>
              </div>

              <div className="mb-3 px-2 py-1.5 bg-gray-50 dark:bg-gray-800/50 rounded">
                <p className="text-xs text-gray-500 dark:text-gray-400 font-mono truncate" title={pool.path}>{pool.path}</p>
              </div>

              <div className="mb-3">
                <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${assigned ? 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-300' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'}`}>
                  {assigned ? `Tenant: ${assigned}` : 'Shared'}
                </span>
              </div>

              <div className="mb-3">
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-gray-600 dark:text-gray-300 font-medium">{pool.used_size_gb} GB VM storage</span>
                  <span className="text-gray-400 dark:text-gray-500">{pool.total_size_gb} GB Disk</span>
                </div>
                <div className="w-full bg-gray-200 rounded-full h-2.5 dark:bg-gray-700 overflow-hidden relative">
                  <div className="bg-gray-400/30 dark:bg-gray-500/30 h-2.5 rounded-full absolute" style={{ width: `${Math.min(diskUsedPercent, 100)}%` }} />
                  <div className={`${barColor} h-2.5 rounded-full relative`} style={{ width: `${Math.min(poolPercent, 100)}%` }} />
                </div>
                <div className="flex justify-between text-xs text-gray-400 dark:text-gray-500 mt-1">
                  <span>Disk: {diskUsedPercent}% used</span>
                  <span>{freeGb} GB free</span>
                </div>
              </div>

              {pool.description && (
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-2 line-clamp-2">{pool.description}</p>
              )}

              <div className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-800">
                <p className="text-xs text-gray-400 dark:text-gray-500">
                  Created {new Date(pool.created_at).toLocaleDateString('en-US')}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
