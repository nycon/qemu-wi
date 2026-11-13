import { useEffect, useState } from 'react';
import {
  PlusIcon,
  PencilIcon,
  TrashIcon,
  ShieldCheckIcon,
  NoSymbolIcon,
  UserGroupIcon,
  ArrowPathIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { userApi, tenantApi } from '@/services/api';
import type { User, Tenant } from '@/types';

export function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [assignableVMs, setAssignableVMs] = useState<Array<{ id: string; name: string; tenant_id: string; assigned_user_id: string | null }>>([]);
  const [userAssignments, setUserAssignments] = useState<Record<string, Array<{ vm_id: string; vm_name: string }>>>({});
  const [assignableProjects, setAssignableProjects] = useState<
    Array<{ id: string; name: string; tenant_id: string; tenant_name: string }>
  >([]);
  const [projectAssignments, setProjectAssignments] = useState<
    Record<string, Array<{ project_id: string; project_name: string; tenant_id: string; tenant_name?: string }>>
  >({});
  const [showForm, setShowForm] = useState(false);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [form, setForm] = useState({
    email: '', username: '', full_name: '', password: '',
    is_superadmin: false, tenant_id: '', tenant_role: 'user', assigned_vm_ids: [] as string[],
  });

  const loadData = async () => {
    setIsLoading(true);
    try {
      const [u, t, vms, vmAssignments, projects, userProjectAssignments] = await Promise.all([
        userApi.list(),
        tenantApi.list(),
        userApi.listAssignableVMs(),
        userApi.listVMAssignments(),
        userApi.listAssignableProjects(),
        userApi.listProjectAssignments(),
      ]);
      setUsers(u);
      setTenants(t);
      setAssignableVMs(vms);
      setAssignableProjects(projects);
      setUserAssignments(
        vmAssignments.reduce<Record<string, Array<{ vm_id: string; vm_name: string }>>>((acc, item) => {
          if (!acc[item.user_id]) acc[item.user_id] = [];
          acc[item.user_id].push({ vm_id: item.vm_id, vm_name: item.vm_name });
          return acc;
        }, {})
      );
      setProjectAssignments(
        userProjectAssignments.reduce<
          Record<string, Array<{ project_id: string; project_name: string; tenant_id: string; tenant_name?: string }>>
        >((acc, item) => {
          if (!acc[item.user_id]) acc[item.user_id] = [];
          acc[item.user_id].push({
            project_id: item.project_id,
            project_name: item.project_name,
            tenant_id: item.tenant_id,
            tenant_name: item.tenant_name,
          });
          return acc;
        }, {})
      );
    } catch {
      toast.error('Failed to load data');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => { loadData(); }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const target = e.target;
    const { name, value } = target;
    const checked = target instanceof HTMLInputElement ? target.checked : false;
    const type = target instanceof HTMLInputElement ? target.type : 'text';
    setForm((prev) => {
      const nextValue = type === 'checkbox' ? checked : value;
      if (name === 'tenant_id') {
        return {
          ...prev,
          tenant_id: String(nextValue),
          assigned_vm_ids: [],
        };
      }
      return { ...prev, [name]: nextValue };
    });
  };

  const toggleAssignedVM = (vmId: string) => {
    setForm((prev) => {
      const exists = prev.assigned_vm_ids.includes(vmId);
      if (exists) {
        return { ...prev, assigned_vm_ids: prev.assigned_vm_ids.filter((id) => id !== vmId) };
      }
      return { ...prev, assigned_vm_ids: [...prev.assigned_vm_ids, vmId] };
    });
  };

  const openEdit = (u: User) => {
    setEditingUser(u);
    setForm({
      email: u.email, username: u.username, full_name: u.full_name || '', password: '',
      is_superadmin: u.is_superadmin, tenant_id: '', tenant_role: 'user', assigned_vm_ids: [],
    });
    setShowForm(true);
  };

  const resetForm = () => {
    setForm({ email: '', username: '', full_name: '', password: '', is_superadmin: false, tenant_id: '', tenant_role: 'user', assigned_vm_ids: [] });
    setShowForm(false);
    setEditingUser(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      if (editingUser) {
        const updateData: Record<string, unknown> = { email: form.email, full_name: form.full_name || undefined };
        if (form.password) updateData.password = form.password;
        await userApi.update(editingUser.id, updateData);
        toast.success('User updated');
      } else {
        await userApi.create({
          email: form.email,
          username: form.username,
          password: form.password,
          full_name: form.full_name || undefined,
          is_superadmin: form.is_superadmin,
          tenant_id: form.tenant_id || undefined,
          tenant_role: form.tenant_id ? form.tenant_role : undefined,
          assigned_vm_ids: form.tenant_id ? form.assigned_vm_ids : undefined,
        });
        toast.success('User created');
      }
      resetForm();
      loadData();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    }
  };

  const handleDeactivate = async (u: User) => {
    if (!confirm(`Disable user "${u.username}"?\n\nThe user will no longer be able to log in, but will remain in the system.`)) return;
    try {
      await userApi.deactivate(u.id);
      toast.success('User disabled');
      loadData();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    }
  };

  const handleDelete = async (u: User) => {
    if (!confirm(`Permanently delete user "${u.username}"?\n\n⚠ This action cannot be undone!\nAll tenant assignments will be removed.`)) return;
    try {
      await userApi.delete(u.id);
      toast.success('User deleted');
      loadData();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    }
  };

  const handleReactivate = async (u: User) => {
    try {
      await userApi.update(u.id, { is_active: true });
      toast.success('User reactivated');
      loadData();
    } catch {
      toast.error('Reactivation failed');
    }
  };

  const handleAssignVM = async (userId: string, vmId: string | null) => {
    try {
      await userApi.assignSingleVM(userId, vmId);
      toast.success(vmId ? 'VM assigned' : 'Assignment cleared');
      loadData();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    }
  };

  const handleAssignProject = async (userId: string, projectId: string) => {
    try {
      const currentProjectIds = (projectAssignments[userId] || []).map((item) => item.project_id);
      const nextProjectIds = Array.from(new Set([...currentProjectIds, projectId]));
      await userApi.assignProjects(userId, nextProjectIds);
      toast.success('Project assigned');
      loadData();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    }
  };

  const handleClearProjects = async (userId: string) => {
    try {
      await userApi.assignProjects(userId, []);
      toast.success('Project assignments cleared');
      loadData();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    }
  };

  const selectedTenantVMs = assignableVMs.filter(
    (vm) => vm.tenant_id === form.tenant_id && !vm.assigned_user_id
  );

  return (
    <div>
      <div className="sm:flex sm:items-center sm:justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">User Management</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {users.filter((u) => u.is_active).length} active / {users.length} total
          </p>
        </div>
        <button
          onClick={() => { setEditingUser(null); setForm({ email: '', username: '', full_name: '', password: '', is_superadmin: false, tenant_id: '', tenant_role: 'user', assigned_vm_ids: [] }); setShowForm(!showForm); }}
          className="btn-primary mt-4 sm:mt-0"
        >
          <PlusIcon className="h-5 w-5 mr-1.5" />
          Create User
        </button>
      </div>

      {showForm && (
        <div className="card p-6 mb-8">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            {editingUser ? 'Edit User' : 'New User'}
          </h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {!editingUser && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Username *</label>
                  <input name="username" value={form.username} onChange={handleChange} required className="input-field" placeholder="e.g. maxm" />
                </div>
              )}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Email *</label>
                <input name="email" type="email" value={form.email} onChange={handleChange} required className="input-field" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Display Name</label>
                <input name="full_name" value={form.full_name} onChange={handleChange} className="input-field" placeholder="John Doe" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Password {editingUser ? '(empty = unchanged)' : '*'}
                </label>
                <input name="password" type="password" value={form.password} onChange={handleChange} required={!editingUser} minLength={8} className="input-field" />
              </div>
            </div>

            {/* Tenant & Role Assignment (only for new users) */}
            {!editingUser && (
              <div className="bg-gray-50 dark:bg-gray-800/50 rounded-lg p-4 space-y-3">
                <div className="flex items-center gap-2 mb-1">
                  <UserGroupIcon className="h-4 w-4 text-gray-500" />
                  <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Tenant Assignment</span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Tenant</label>
                    <select name="tenant_id" value={form.tenant_id} onChange={handleChange} className="input-field">
                      <option value="">— Assign no tenant —</option>
                      {tenants.filter((t) => t.is_active).map((t) => (
                        <option key={t.id} value={t.id}>{t.name}</option>
                      ))}
                    </select>
                    <p className="text-xs text-gray-400 mt-1">
                      Without a tenant, the user cannot manage VMs.
                    </p>
                  </div>
                  {form.tenant_id && (
                    <div className="space-y-3">
                      <div>
                        <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Role in tenant</label>
                        <select name="tenant_role" value={form.tenant_role} onChange={handleChange} className="input-field">
                          <option value="user">User – Create & manage VMs</option>
                          <option value="tenant_admin">Tenant Admin – Full access in tenant</option>
                          <option value="readonly">Read-Only – Can view VMs</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-2">
                          Assign existing tenant VMs (optional)
                        </label>
                        {selectedTenantVMs.length === 0 ? (
                          <p className="text-xs text-gray-400">No unassigned VMs in this tenant.</p>
                        ) : (
                          <div className="max-h-36 overflow-y-auto space-y-2 rounded-md border border-gray-200 dark:border-gray-700 p-2">
                            {selectedTenantVMs.map((vm) => (
                              <label key={vm.id} className="flex items-center gap-2 text-xs text-gray-700 dark:text-gray-300">
                                <input
                                  type="checkbox"
                                  checked={form.assigned_vm_ids.includes(vm.id)}
                                  onChange={() => toggleAssignedVM(vm.id)}
                                  className="rounded border-gray-300 text-primary-600"
                                />
                                <span className="truncate">{vm.name}</span>
                              </label>
                            ))}
                          </div>
                        )}
                        <p className="text-xs text-gray-400 mt-1">
                          Selected: {form.assigned_vm_ids.length}
                        </p>
                      </div>
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-2 pt-2">
                  <input name="is_superadmin" type="checkbox" checked={form.is_superadmin} onChange={handleChange} className="rounded border-gray-300 text-primary-600" />
                  <label className="text-sm text-gray-700 dark:text-gray-300">Super-Admin</label>
                  <span className="text-xs text-gray-400">(Access to all tenants + Admin panel)</span>
                </div>
              </div>
            )}

            <div className="flex justify-end gap-3">
              <button type="button" onClick={resetForm} className="btn-secondary">Cancel</button>
              <button type="submit" className="btn-primary">{editingUser ? 'Save' : 'Create'}</button>
            </div>
          </form>
        </div>
      )}

      <div className="card overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          <thead className="bg-gray-50 dark:bg-gray-800/50">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">User</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Email</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Role</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Assigned Projects</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Assigned VMs</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Created</th>
              <th className="relative px-6 py-3"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
            {isLoading ? (
              <tr><td colSpan={8} className="px-6 py-12 text-center text-gray-500">Loading...</td></tr>
            ) : users.length === 0 ? (
              <tr><td colSpan={8} className="px-6 py-12 text-center text-gray-500">No users</td></tr>
            ) : users.map((u) => (
              <tr key={u.id} className={`hover:bg-gray-50 dark:hover:bg-gray-800/50 ${!u.is_active ? 'opacity-60' : ''}`}>
                <td className="px-6 py-4 whitespace-nowrap">
                  <div className="text-sm font-medium text-gray-900 dark:text-white">{u.username}</div>
                  {u.full_name && <div className="text-xs text-gray-500">{u.full_name}</div>}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{u.email}</td>
                <td className="px-6 py-4 whitespace-nowrap">
                  {u.is_superadmin ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-purple-100 px-2.5 py-0.5 text-xs font-medium text-purple-800 dark:bg-purple-900/30 dark:text-purple-400">
                      <ShieldCheckIcon className="h-3 w-3" /> Super Admin
                    </span>
                  ) : (
                    <span className="text-xs text-gray-500">User</span>
                  )}
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  {u.is_superadmin ? (
                    <span className="text-xs text-gray-400">n/a</span>
                  ) : (
                    <div className="space-y-2 min-w-[260px]">
                      <div className="text-xs text-gray-500 dark:text-gray-400">
                        {(projectAssignments[u.id]?.length || 0) > 0
                          ? (projectAssignments[u.id] || [])
                              .map((item) =>
                                item.tenant_name
                                  ? `${item.tenant_name} / ${item.project_name}`
                                  : item.project_name,
                              )
                              .join(', ')
                          : 'No project assignments'}
                      </div>
                      <div className="flex gap-2">
                        <select
                          className="input-field text-xs"
                          value=""
                          onChange={(e) => {
                            const value = e.target.value;
                            if (value) {
                              handleAssignProject(u.id, value);
                            }
                          }}
                        >
                          <option value="">+ Add Project</option>
                          {assignableProjects
                            .filter((project) => !(projectAssignments[u.id] || []).some((item) => item.project_id === project.id))
                            .map((project) => (
                              <option key={project.id} value={project.id}>
                                {project.tenant_name} / {project.name}
                              </option>
                            ))}
                        </select>
                        <button type="button" className="btn-secondary text-xs" onClick={() => handleClearProjects(u.id)}>
                          Clear
                        </button>
                      </div>
                    </div>
                  )}
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  {u.is_superadmin ? (
                    <span className="text-xs text-gray-400">n/a</span>
                  ) : (
                    <div className="space-y-2 min-w-[260px]">
                      <div className="text-xs text-gray-500 dark:text-gray-400">
                        {(userAssignments[u.id]?.length || 0) > 0
                          ? (userAssignments[u.id] || []).map((item) => item.vm_name).join(', ')
                          : 'No VM assignments'}
                      </div>
                      <div className="flex gap-2">
                        <select
                          className="input-field text-xs"
                          value=""
                          onChange={(e) => {
                            const value = e.target.value;
                            if (value) {
                              handleAssignVM(u.id, value);
                            }
                          }}
                        >
                          <option value="">+ Add VM</option>
                          {assignableVMs
                            .filter((vm) => !vm.assigned_user_id)
                            .map((vm) => (
                              <option key={vm.id} value={vm.id}>
                                {vm.name}
                              </option>
                            ))}
                        </select>
                        <button type="button" className="btn-secondary text-xs" onClick={() => handleAssignVM(u.id, null)}>
                          Clear
                        </button>
                      </div>
                    </div>
                  )}
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${u.is_active ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' : 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400'}`}>
                    {u.is_active ? 'Active' : 'Deactivated'}
                  </span>
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                  {new Date(u.created_at).toLocaleDateString()}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-right">
                  <div className="flex gap-1 justify-end">
                    <button onClick={() => openEdit(u)} className="text-gray-400 hover:text-primary-600 p-1" title="Edit">
                      <PencilIcon className="h-4 w-4" />
                    </button>
                    {u.is_active ? (
                      <button onClick={() => handleDeactivate(u)} className="text-gray-400 hover:text-yellow-600 p-1" title="Disable">
                        <NoSymbolIcon className="h-4 w-4" />
                      </button>
                    ) : (
                      <button onClick={() => handleReactivate(u)} className="text-gray-400 hover:text-green-600 p-1" title="Reactivate">
                        <ArrowPathIcon className="h-4 w-4" />
                      </button>
                    )}
                    <button onClick={() => handleDelete(u)} className="text-gray-400 hover:text-red-600 p-1" title="Delete permanently">
                      <TrashIcon className="h-4 w-4" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
