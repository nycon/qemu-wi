import { useEffect, useState } from 'react';
import { PlusIcon, PencilIcon, TrashIcon } from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { ipamApi, tenantApi, userApi, type PublicIpAssignment } from '@/services/api';
import type { Tenant, User } from '@/types';

/** Slug must match backend: ^[a-z0-9-]+$ */
function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 255);
}

function apiErrorMessage(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item;
        const loc = Array.isArray(item?.loc) ? item.loc.filter((x: unknown) => x !== 'body').join('.') : '';
        const msg = item?.msg || 'Invalid value';
        return loc ? `${loc}: ${msg}` : String(msg);
      })
      .join('; ');
  }
  return fallback;
}

export function TenantsPage() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [editingTenant, setEditingTenant] = useState<Tenant | null>(null);
  const [slugManual, setSlugManual] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [availablePublicIps, setAvailablePublicIps] = useState<PublicIpAssignment[]>([]);
  const [assignedPublicIps, setAssignedPublicIps] = useState<PublicIpAssignment[]>([]);
  const [selectedPublicIpIds, setSelectedPublicIpIds] = useState<string[]>([]);
  const [form, setForm] = useState({
    name: '', slug: '', description: '',
    max_vms: 10, max_vcpus: 32, max_ram_mb: 65536, max_storage_gb: 500,
    notification_email: '',
    admin_user_id: '',
  });

  const loadData = async () => {
    setIsLoading(true);
    try {
      const [tenantList, userList] = await Promise.all([
        tenantApi.list(),
        userApi.list().catch(() => [] as User[]),
      ]);
      setTenants(tenantList);
      setUsers(userList.filter((u) => u.is_active && !u.is_superadmin));
    } catch {
      toast.error('Tenants could not be loaded');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => { loadData(); }, []);

  const loadPublicIpOptions = async (tenantId?: string) => {
    try {
      const free = await ipamApi.listAvailablePublicIps();
      let assigned: PublicIpAssignment[] = [];
      if (tenantId) {
        assigned = await tenantApi.listPublicIps(tenantId);
      }
      setAvailablePublicIps(free);
      setAssignedPublicIps(assigned);
      setSelectedPublicIpIds(assigned.map((a) => a.id));
    } catch {
      setAvailablePublicIps([]);
      setAssignedPublicIps([]);
      setSelectedPublicIpIds([]);
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    const { name, value, type } = e.target;
    if (name === 'slug') {
      setSlugManual(true);
      setForm((prev) => ({ ...prev, slug: slugify(value) }));
      return;
    }
    if (name === 'name') {
      setForm((prev) => ({
        ...prev,
        name: value,
        slug: !editingTenant && !slugManual ? slugify(value) : prev.slug,
      }));
      return;
    }
    setForm((prev) => ({ ...prev, [name]: type === 'number' ? Number(value) : value }));
  };

  const emptyForm = () => ({
    name: '', slug: '', description: '',
    max_vms: 10, max_vcpus: 32, max_ram_mb: 65536, max_storage_gb: 500,
    notification_email: '',
    admin_user_id: '',
  });

  const openCreate = async () => {
    setEditingTenant(null);
    setSlugManual(false);
    setForm(emptyForm());
    setShowForm(true);
    await loadPublicIpOptions();
  };

  const openEdit = async (t: Tenant) => {
    setEditingTenant(t);
    setSlugManual(true);
    setForm({
      name: t.name, slug: t.slug, description: t.description || '',
      max_vms: t.max_vms, max_vcpus: t.max_vcpus, max_ram_mb: t.max_ram_mb,
      max_storage_gb: t.max_storage_gb,
      notification_email: t.notification_email || '',
      admin_user_id: '',
    });
    setShowForm(true);
    await loadPublicIpOptions(t.id);
    try {
      const members = await tenantApi.listUsers(t.id);
      const admin = members.find((m) => m.is_active && m.role === 'tenant_admin');
      if (admin) {
        setForm((prev) => ({ ...prev, admin_user_id: admin.user_id }));
      }
    } catch {
      // optional
    }
  };

  const togglePublicIp = (id: string, locked: boolean) => {
    if (locked) return;
    setSelectedPublicIpIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const slug = slugify(form.slug || form.name);
    if (slug.length < 2) {
      toast.error('Slug must be at least 2 characters (a-z, 0-9, hyphen)');
      return;
    }
    const { admin_user_id, notification_email, ...rest } = form;
    try {
      if (editingTenant) {
        await tenantApi.update(editingTenant.id, {
          ...rest,
          notification_email: notification_email.trim() || null,
          ...(admin_user_id ? { admin_user_id } : {}),
          public_ip_allocation_ids: selectedPublicIpIds,
          max_ips: selectedPublicIpIds.length,
        });
        toast.success('Tenant updated');
      } else {
        await tenantApi.create({
          ...rest,
          slug,
          notification_email: notification_email.trim() || null,
          admin_user_id: admin_user_id || null,
          public_ip_allocation_ids: selectedPublicIpIds,
          max_ips: selectedPublicIpIds.length,
        });
        toast.success('Tenant created');
      }
      setShowForm(false);
      setEditingTenant(null);
      setSlugManual(false);
      setForm(emptyForm());
      setSelectedPublicIpIds([]);
      loadData();
    } catch (error) {
      toast.error(apiErrorMessage(error, 'Action failed'));
    }
  };

  const handleDelete = async (t: Tenant) => {
    if (!confirm(`Really delete tenant "${t.name}"?`)) return;
    try {
      await tenantApi.delete(t.id);
      toast.success('Tenant deleted');
      loadData();
    } catch {
      toast.error('Delete failed');
    }
  };

  const selectableIps = [
    ...assignedPublicIps,
    ...availablePublicIps.filter((a) => !selectedPublicIpIds.includes(a.id)),
  ];
  // de-dupe by id
  const ipOptions = Array.from(new Map(selectableIps.map((a) => [a.id, a])).values())
    .sort((a, b) => a.ip_address.localeCompare(b.ip_address, undefined, { numeric: true }));

  return (
    <div>
      <div className="sm:flex sm:items-center sm:justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Tenant Management</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{tenants.length} Tenant(s)</p>
        </div>
        <button onClick={openCreate} className="btn-primary mt-4 sm:mt-0">
          <PlusIcon className="h-5 w-5 mr-1.5" />
          Create Tenant
        </button>
      </div>

      {showForm && (
        <div className="card p-6 mb-8">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            {editingTenant ? 'Edit Tenant' : 'New Tenant'}
          </h2>
          <form onSubmit={handleSubmit} className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name *</label>
              <input name="name" value={form.name} onChange={handleChange} required minLength={2} className="input-field" placeholder="Acme Corp" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Slug *</label>
              <input
                name="slug"
                value={form.slug}
                onChange={handleChange}
                required
                minLength={2}
                className="input-field font-mono"
                placeholder="acme-corp"
                pattern="^[a-z0-9-]+$"
                title="Only lowercase letters, digits and hyphens"
                disabled={!!editingTenant}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Tenant Admin</label>
              <select
                name="admin_user_id"
                value={form.admin_user_id}
                onChange={handleChange}
                className="input-field"
              >
                <option value="">
                  {editingTenant ? '— No change —' : '— Assign later —'}
                </option>
                {users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.full_name || u.username} ({u.email})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Max VMs</label>
              <input name="max_vms" type="number" min={1} value={form.max_vms} onChange={handleChange} className="input-field" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Max vCPUs</label>
              <input name="max_vcpus" type="number" min={1} value={form.max_vcpus} onChange={handleChange} className="input-field" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Max RAM (MB)</label>
              <input name="max_ram_mb" type="number" min={512} value={form.max_ram_mb} onChange={handleChange} className="input-field" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Max Storage (GB)</label>
              <input name="max_storage_gb" type="number" min={10} value={form.max_storage_gb} onChange={handleChange} className="input-field" />
            </div>
            <div className="lg:col-span-2">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Description</label>
              <textarea name="description" value={form.description} onChange={handleChange} className="input-field" rows={2} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Notification email
              </label>
              <input
                name="notification_email"
                type="email"
                value={form.notification_email}
                onChange={handleChange}
                className="input-field"
                placeholder="ops@example.com"
              />
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                Tenant inbox for VM / backup / quota alerts
              </p>
            </div>

            <div className="lg:col-span-3">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Public IPs ({selectedPublicIpIds.length} selected)
              </label>
              <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">
                Select the exact public IPs this tenant may use. The tenant only sees these IPs — not the whole pool.
              </p>
              {ipOptions.length === 0 ? (
                <p className="text-sm text-gray-500 dark:text-gray-400">No free public IPs in the platform pool.</p>
              ) : (
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2 max-h-48 overflow-y-auto border border-gray-200 dark:border-gray-700 rounded-lg p-3">
                  {ipOptions.map((ip) => {
                    const locked = ip.status === 'allocated';
                    const checked = selectedPublicIpIds.includes(ip.id);
                    return (
                      <label
                        key={ip.id}
                        className={`flex items-center gap-2 text-sm rounded px-2 py-1.5 cursor-pointer ${
                          locked ? 'opacity-70 cursor-not-allowed' : 'hover:bg-gray-50 dark:hover:bg-gray-800/60'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={locked}
                          onChange={() => togglePublicIp(ip.id, locked)}
                          className="rounded border-gray-300"
                        />
                        <span className="font-mono text-gray-900 dark:text-white">{ip.ip_address}</span>
                        {locked && <span className="text-xs text-amber-600">in use</span>}
                      </label>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="lg:col-span-3 flex justify-end gap-3">
              <button type="button" onClick={() => { setShowForm(false); setEditingTenant(null); setSlugManual(false); }} className="btn-secondary">Cancel</button>
              <button type="submit" className="btn-primary">{editingTenant ? 'Save' : 'Create'}</button>
            </div>
          </form>
        </div>
      )}

      <div className="card overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          <thead className="bg-gray-50 dark:bg-gray-800/50">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Tenant</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Quotas</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Status</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Created</th>
              <th className="relative px-6 py-3"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
            {isLoading ? (
              <tr><td colSpan={5} className="px-6 py-12 text-center text-gray-500 dark:text-gray-400">Loading...</td></tr>
            ) : tenants.length === 0 ? (
              <tr><td colSpan={5} className="px-6 py-12 text-center text-gray-500 dark:text-gray-400">No tenants</td></tr>
            ) : tenants.map((t) => (
              <tr key={t.id} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                <td className="px-6 py-4">
                  <div className="text-sm font-medium text-gray-900 dark:text-white">{t.name}</div>
                  <div className="text-xs text-gray-500 dark:text-gray-400 font-mono">{t.slug}</div>
                  {t.description && <div className="text-xs text-gray-400 mt-0.5">{t.description}</div>}
                </td>
                <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                  <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs">
                    <span className="col-span-2 font-mono truncate" title={t.notification_email || undefined}>
                      Mail: {t.notification_email || '—'}
                    </span>
                    <span>VMs: {t.max_vms}</span>
                    <span>vCPUs: {t.max_vcpus}</span>
                    <span>RAM: {Math.round(t.max_ram_mb / 1024)} GB</span>
                    <span>Storage: {t.max_storage_gb} GB</span>
                    <span>Public IPs: {t.max_ips}</span>
                  </div>
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${t.is_active ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'}`}>
                    {t.is_active ? 'Active' : 'Inactive'}
                  </span>
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                  {new Date(t.created_at).toLocaleDateString()}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-right flex gap-1 justify-end">
                  <button onClick={() => openEdit(t)} className="text-gray-400 hover:text-primary-600 dark:hover:text-primary-400 p-1"><PencilIcon className="h-4 w-4" /></button>
                  <button onClick={() => handleDelete(t)} className="text-gray-400 hover:text-red-600 dark:hover:text-red-400 p-1"><TrashIcon className="h-4 w-4" /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
