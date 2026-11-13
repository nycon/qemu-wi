import { useEffect, useState } from 'react';
import {
  GlobeAltIcon,
  LockClosedIcon,
  PlusIcon,
  TrashIcon,
  ArrowPathIcon,
  PencilIcon,
  PowerIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { proxyApi, vmApi, tenantApi } from '@/services/api';
import type { ReverseProxyRule, VirtualMachine } from '@/types';

function extractVMIP(vm: VirtualMachine): string {
  const net = vm.cloud_init_network;
  if (!net?.ethernets) return '';
  for (const cfg of Object.values(net.ethernets as Record<string, Record<string, unknown>>)) {
    if (Array.isArray(cfg.addresses) && cfg.addresses.length > 0) {
      return String(cfg.addresses[0]).split('/')[0];
    }
  }
  return '';
}

const emptyForm = {
  domain: '',
  target_ip: '',
  target_port: 80,
  vm_id: '',
  websocket_support: false,
  ssl_mode: 'none' as 'none' | 'auto',
};

export function ReverseProxyPage() {
  // tenantStore no longer needed for VM loading – VMs loaded from all tenants
  const [rules, setRules] = useState<ReverseProxyRule[]>([]);
  const [total, setTotal] = useState(0);
  const [vms, setVms] = useState<VirtualMachine[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [editingRule, setEditingRule] = useState<ReverseProxyRule | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [vmsLoading, setVmsLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [sslLoadingId, setSslLoadingId] = useState<string | null>(null);
  const [form, setForm] = useState(emptyForm);

  const loadRules = async () => {
    setIsLoading(true);
    try {
      const res = await proxyApi.list();
      setRules(res.items);
      setTotal(res.total);
    } catch {
      toast.error('Reverse proxy rules could not be loaded');
    } finally {
      setIsLoading(false);
    }
  };

  const loadVms = async () => {
    setVmsLoading(true);
    try {
      const tenants = await tenantApi.list();
      if (!tenants || !Array.isArray(tenants) || tenants.length === 0) {
        setVms([]);
        return;
      }
      const allVms: VirtualMachine[] = [];
      for (const t of tenants) {
        try {
          const res = await vmApi.list(t.id, { page_size: 100 });
          if (res?.items) {
            allVms.push(...res.items);
          }
        } catch { /* skip tenant */ }
      }
      setVms(allVms);
    } catch {
      toast.error('Failed to load VMs');
      setVms([]);
    } finally {
      setVmsLoading(false);
    }
  };

  useEffect(() => {
    loadRules();
  }, []);

  useEffect(() => {
    loadVms();
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const { name, value, type } = e.target;
    const checked = (e.target as HTMLInputElement).checked;
    setForm((prev) => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : type === 'number' ? Number(value) : value,
    }));
  };

  const handleVmSelect = (vmId: string) => {
    setForm((prev) => ({ ...prev, vm_id: vmId }));
    if (!vmId) return;
    const vm = vms.find((v) => v.id === vmId);
    if (vm) {
      const ip = extractVMIP(vm);
      if (ip) setForm((prev) => ({ ...prev, target_ip: ip }));
    }
  };

  const openCreate = () => {
    setEditingRule(null);
    setForm(emptyForm);
    setShowForm(true);
  };

  const openEdit = (rule: ReverseProxyRule) => {
    setEditingRule(rule);
    setForm({
      domain: rule.domain,
      target_ip: rule.target_ip,
      target_port: rule.target_port,
      vm_id: rule.vm_id ?? '',
      websocket_support: rule.websocket_support,
      ssl_mode: (rule.ssl_mode === 'auto' ? 'auto' : 'none') as 'none' | 'auto',
    });
    setShowForm(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      if (editingRule) {
        await proxyApi.update(editingRule.id, {
          target_ip: form.target_ip,
          target_port: form.target_port,
          vm_id: form.vm_id || undefined,
          ssl_mode: form.ssl_mode,
          websocket_support: form.websocket_support,
        });
        toast.success('Rule updated');
      } else {
        await proxyApi.create({
          domain: form.domain,
          target_ip: form.target_ip,
          target_port: form.target_port,
          vm_id: form.vm_id || undefined,
          ssl_mode: form.ssl_mode,
          websocket_support: form.websocket_support,
        });
        toast.success('Rule created');
      }
      setShowForm(false);
      setEditingRule(null);
      setForm(emptyForm);
      loadRules();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Action failed';
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (rule: ReverseProxyRule) => {
    if (!confirm(`Really delete rule for "${rule.domain}"?`)) return;
    try {
      await proxyApi.delete(rule.id);
      toast.success('Rule deleted');
      if (editingRule?.id === rule.id) {
        setShowForm(false);
        setEditingRule(null);
      }
      loadRules();
    } catch {
      toast.error('Delete failed');
    }
  };

  const handleToggleActive = async (rule: ReverseProxyRule) => {
    try {
      await proxyApi.update(rule.id, { is_active: !rule.is_active });
      toast.success(rule.is_active ? 'Rule disabled' : 'Rule enabled');
      loadRules();
    } catch {
      toast.error('Toggle failed');
    }
  };

  const handleRequestSSL = async (rule: ReverseProxyRule) => {
    setSslLoadingId(rule.id);
    try {
      const res = await proxyApi.requestSSL(rule.id);
      toast.success(res.message ?? 'SSL certificate requested');
      loadRules();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'SSL request failed';
      toast.error(msg);
    } finally {
      setSslLoadingId(null);
    }
  };

  return (
    <div>
      <div className="sm:flex sm:items-center sm:justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Reverse Proxy</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {total} rule(s) configured
          </p>
        </div>
        <button onClick={openCreate} className="btn-primary mt-4 sm:mt-0">
          <PlusIcon className="h-5 w-5 mr-1.5" />
          Create Rule
        </button>
      </div>

      {showForm && (
        <div className="card p-6 mb-8">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            {editingRule ? 'Edit Rule' : 'New Rule'}
          </h2>
          <form onSubmit={handleSubmit} className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Domain *</label>
              <input
                name="domain"
                value={form.domain}
                onChange={handleChange}
                required
                disabled={!!editingRule}
                className="input-field disabled:opacity-60 disabled:cursor-not-allowed"
                placeholder="web1.example.com"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Target IP *</label>
              <input
                name="target_ip"
                value={form.target_ip}
                onChange={handleChange}
                required
                className="input-field"
                placeholder="10.10.1.5"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Target Port</label>
              <input
                name="target_port"
                type="number"
                min={1}
                max={65535}
                value={form.target_port}
                onChange={handleChange}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                VM (optional) {!vmsLoading && <span className="text-xs text-gray-400">({vms.length} found)</span>}
              </label>
              <select
                name="vm_id"
                value={form.vm_id}
                onChange={(e) => handleVmSelect(e.target.value)}
                className="input-field"
                disabled={vmsLoading}
              >
                <option value="">{vmsLoading ? 'Loading VMs...' : '— Select VM —'}</option>
                {vms.map((vm) => (
                  <option key={vm.id} value={vm.id}>
                    {vm.name} ({vm.hostname})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">SSL Mode</label>
              <select
                name="ssl_mode"
                value={form.ssl_mode}
                onChange={handleChange}
                className="input-field"
              >
                <option value="none">None</option>
                <option value="auto">Auto</option>
              </select>
            </div>
            <div className="flex items-center">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  name="websocket_support"
                  type="checkbox"
                  checked={form.websocket_support}
                  onChange={handleChange}
                  className="rounded border-gray-300 dark:border-gray-600 text-primary-600 focus:ring-primary-500"
                />
                <span className="text-sm font-medium text-gray-700 dark:text-gray-300">WebSocket Support</span>
              </label>
            </div>
            <div className="md:col-span-2 lg:col-span-3 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  setShowForm(false);
                  setEditingRule(null);
                  setForm(emptyForm);
                }}
                className="btn-secondary"
              >
                Cancel
              </button>
              <button type="submit" disabled={submitting} className="btn-primary disabled:opacity-50">
                {submitting ? 'Saving...' : editingRule ? 'Save' : 'Create'}
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead className="bg-gray-50 dark:bg-gray-800/50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Domain
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Target
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  SSL
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Status
                </th>
                <th className="relative px-6 py-3">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {isLoading ? (
                <tr>
                  <td colSpan={5} className="px-6 py-12 text-center text-gray-500 dark:text-gray-400">
                    Loading...
                  </td>
                </tr>
              ) : rules.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-6 py-12 text-center text-gray-500 dark:text-gray-400">
                    <GlobeAltIcon className="h-10 w-10 mx-auto mb-2 text-gray-400" />
                    <p>No reverse proxy rules</p>
                    <p className="text-xs mt-1">Create a rule to route domains to VM IPs</p>
                  </td>
                </tr>
              ) : (
                rules.map((rule) => (
                  <tr key={rule.id} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        <GlobeAltIcon className="h-4 w-4 text-gray-400" />
                        <span className="text-sm font-medium text-gray-900 dark:text-white font-mono">
                          {rule.domain}
                        </span>
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400 font-mono">
                      {rule.target_ip}:{rule.target_port}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${
                          rule.ssl_enabled
                            ? 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                            : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
                        }`}
                      >
                        {rule.ssl_enabled ? (
                          <LockClosedIcon className="h-3 w-3" />
                        ) : null}
                        {rule.ssl_enabled ? 'SSL' : 'No SSL'}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
                          rule.is_active
                            ? 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                            : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
                        }`}
                      >
                        {rule.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center justify-end gap-1">
                        {!rule.ssl_enabled && (
                          <button
                            onClick={() => handleRequestSSL(rule)}
                            disabled={sslLoadingId === rule.id}
                            className="p-1.5 rounded text-amber-600 hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-900/20 disabled:opacity-50"
                            title="Request SSL certificate"
                          >
                            {sslLoadingId === rule.id ? (
                              <ArrowPathIcon className="h-4 w-4 animate-spin" />
                            ) : (
                              <LockClosedIcon className="h-4 w-4" />
                            )}
                          </button>
                        )}
                        <button
                          onClick={() => handleToggleActive(rule)}
                          className="p-1.5 rounded text-gray-400 hover:text-primary-600 hover:bg-gray-100 dark:hover:text-primary-400 dark:hover:bg-gray-800"
                          title={rule.is_active ? 'Disable' : 'Enable'}
                        >
                          <PowerIcon className="h-4 w-4" />
                        </button>
                        <button
                          onClick={() => openEdit(rule)}
                          className="p-1.5 rounded text-gray-400 hover:text-primary-600 hover:bg-gray-100 dark:hover:text-primary-400 dark:hover:bg-gray-800"
                          title="Edit"
                        >
                          <PencilIcon className="h-4 w-4" />
                        </button>
                        <button
                          onClick={() => handleDelete(rule)}
                          className="p-1.5 rounded text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:text-red-400 dark:hover:bg-red-900/20"
                          title="Delete"
                        >
                          <TrashIcon className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
