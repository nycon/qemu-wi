import { useEffect, useMemo, useState } from 'react';
import {
  ScaleIcon,
  PlusIcon,
  TrashIcon,
  PencilIcon,
  PowerIcon,
  ServerStackIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { loadBalancerApi, tenantApi, vmApi, ipamApi } from '@/services/api';
import type { LoadBalancerItem, LBTarget } from '@/services/api';
import type { IPPool, VirtualMachine } from '@/types';

function isPrivateIPv4(ip: string): boolean {
  const parts = ip.split('.').map(Number);
  if (parts.length !== 4 || parts.some((n) => Number.isNaN(n))) return false;
  if (parts[0] === 10) return true;
  if (parts[0] === 127) return true;
  if (parts[0] === 192 && parts[1] === 168) return true;
  if (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) return true;
  if (parts[0] === 169 && parts[1] === 254) return true;
  if (parts[0] === 100 && parts[1] >= 64 && parts[1] <= 127) return true;
  return false;
}

function extractAllVMIPs(vm: VirtualMachine): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const net = vm.cloud_init_network;
  if (net?.ethernets) {
    for (const cfg of Object.values(net.ethernets as Record<string, Record<string, unknown>>)) {
      if (!Array.isArray(cfg.addresses)) continue;
      for (const raw of cfg.addresses) {
        const ip = String(raw).split('/')[0].trim();
        if (!ip || seen.has(ip)) continue;
        seen.add(ip);
        out.push(ip);
      }
    }
  }
  return out;
}

function natIpOf(vm: VirtualMachine): string | null {
  return extractAllVMIPs(vm).find((ip) => isPrivateIPv4(ip)) || null;
}

const PROTOCOLS = ['http', 'https', 'tcp'] as const;
const ALGORITHMS = [
  { value: 'round_robin', label: 'Round Robin' },
  { value: 'least_conn', label: 'Least Connections' },
  { value: 'ip_hash', label: 'IP Hash' },
];

const emptyForm = {
  name: '',
  description: '',
  protocol: 'http' as string,
  algorithm: 'round_robin' as string,
  listen_port: 8080,
  public_ip: '' as string,
  health_check_path: '/',
  health_check_interval: 10,
};

export function LoadBalancerPage() {
  const [lbs, setLbs] = useState<LoadBalancerItem[]>([]);
  const [vms, setVms] = useState<VirtualMachine[]>([]);
  const [ipPools, setIpPools] = useState<IPPool[]>([]);
  const [freePublicIps, setFreePublicIps] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingLb, setEditingLb] = useState<LoadBalancerItem | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState(emptyForm);

  const [addTargetLbId, setAddTargetLbId] = useState<string | null>(null);
  const [targetForm, setTargetForm] = useState<{
    vm_id: string;
    target_port: number;
    weight: number;
  }>({ vm_id: '', target_port: 80, weight: 1 });

  const publicPools = useMemo(
    () => ipPools.filter((p) => p.pool_type === 'external'),
    [ipPools],
  );
  const publicSubnetHint = publicPools.map((p) => p.network).filter(Boolean).join(', ') || 'Public-Subnet';

  const selectedTargetVm = vms.find((v) => v.id === targetForm.vm_id);
  const previewNatIp = selectedTargetVm ? natIpOf(selectedTargetVm) : null;

  const vipChoices = useMemo(() => {
    const cur = form.public_ip ? [form.public_ip] : [];
    return Array.from(new Set([...cur, ...freePublicIps]));
  }, [form.public_ip, freePublicIps]);

  const loadLbs = async () => {
    setIsLoading(true);
    try {
      const data = await loadBalancerApi.list();
      setLbs(data);
    } catch {
      toast.error('Failed to load load balancers');
    } finally {
      setIsLoading(false);
    }
  };

  const loadVms = async () => {
    try {
      const tenants = await tenantApi.list();
      if (!tenants?.length) {
        setVms([]);
        return;
      }
      const all: VirtualMachine[] = [];
      const errors: string[] = [];
      for (const t of tenants) {
        try {
          const res = await vmApi.list(t.id, { page_size: 100 });
          if (res?.items) all.push(...res.items);
        } catch {
          errors.push(t.name || t.id);
        }
      }
      setVms(all);
      if (!all.length && errors.length) {
        toast.error('VMs konnten nicht geladen werden');
      }
    } catch {
      setVms([]);
      toast.error('Tenants/VMs konnten nicht geladen werden');
    }
  };

  const loadPools = async () => {
    try {
      const pools = await ipamApi.listPools();
      setIpPools(Array.isArray(pools) ? pools : []);
    } catch {
      setIpPools([]);
    }
  };

  const loadFreePublic = async () => {
    try {
      const data = await loadBalancerApi.ipChoices();
      setFreePublicIps(data.public_free || []);
    } catch {
      setFreePublicIps([]);
    }
  };

  useEffect(() => {
    loadLbs();
    loadVms();
    loadPools();
    loadFreePublic();
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const { name, value, type } = e.target;
    setForm((prev) => ({
      ...prev,
      [name]: type === 'number' ? Number(value) : value,
    }));
  };

  const openCreate = () => {
    setEditingLb(null);
    setForm({
      ...emptyForm,
      public_ip: freePublicIps[0] || '',
    });
    setShowForm(true);
    void loadFreePublic();
  };

  const openEdit = (lb: LoadBalancerItem) => {
    setEditingLb(lb);
    setForm({
      name: lb.name,
      description: lb.description ?? '',
      protocol: lb.protocol,
      algorithm: lb.algorithm,
      listen_port: lb.listen_port,
      public_ip: lb.public_ip ?? '',
      health_check_path: lb.health_check_path ?? '/',
      health_check_interval: lb.health_check_interval,
    });
    setShowForm(true);
    void loadFreePublic();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      if (editingLb) {
        await loadBalancerApi.update(editingLb.id, {
          name: form.name,
          description: form.description || undefined,
          algorithm: form.algorithm,
          public_ip: form.public_ip || undefined,
          health_check_path: form.health_check_path,
          health_check_interval: form.health_check_interval,
        });
        toast.success('Load balancer updated');
      } else {
        await loadBalancerApi.create({
          name: form.name,
          description: form.description || undefined,
          protocol: form.protocol,
          algorithm: form.algorithm,
          listen_port: form.listen_port,
          public_ip: form.public_ip || undefined,
          health_check_path: form.health_check_path || '/',
          health_check_interval: form.health_check_interval,
        });
        toast.success('Load balancer created');
      }
      setShowForm(false);
      setEditingLb(null);
      setForm(emptyForm);
      loadLbs();
      loadFreePublic();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Action failed';
      toast.error(typeof msg === 'string' ? msg : 'Action failed');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (lb: LoadBalancerItem) => {
    if (!confirm(`Delete load balancer "${lb.name}"?`)) return;
    try {
      await loadBalancerApi.delete(lb.id);
      toast.success('Load balancer deleted');
      loadLbs();
      loadFreePublic();
    } catch {
      toast.error('Delete failed');
    }
  };

  const handleToggle = async (lb: LoadBalancerItem) => {
    try {
      await loadBalancerApi.update(lb.id, { is_active: !lb.is_active });
      toast.success(lb.is_active ? 'Disabled' : 'Enabled');
      loadLbs();
    } catch {
      toast.error('Toggle failed');
    }
  };

  const handleAddTarget = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!addTargetLbId) return;
    if (!previewNatIp) {
      toast.error('Diese VM hat keine interne/NAT-IP.');
      return;
    }
    try {
      await loadBalancerApi.addTarget(addTargetLbId, {
        vm_id: targetForm.vm_id,
        target_port: targetForm.target_port,
        weight: targetForm.weight,
      });
      toast.success(`Target added (${previewNatIp})`);
      setAddTargetLbId(null);
      setTargetForm({ vm_id: '', target_port: 80, weight: 1 });
      loadLbs();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed to add target';
      toast.error(typeof msg === 'string' ? msg : 'Failed to add target');
    }
  };

  const handleRemoveTarget = async (lbId: string, target: LBTarget) => {
    if (!confirm(`Remove "${target.vm_name}" from load balancer?`)) return;
    try {
      await loadBalancerApi.removeTarget(lbId, target.id);
      toast.success('Target removed');
      loadLbs();
    } catch {
      toast.error('Remove failed');
    }
  };

  const algoLabel = (a: string) => ALGORITHMS.find((x) => x.value === a)?.label ?? a;

  return (
    <div>
      <div className="sm:flex sm:items-center sm:justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Load Balancer</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            1 Public-IP (VIP) · N interne Backends · Round Robin · {vms.length} VM(s)
          </p>
        </div>
        <button onClick={openCreate} className="btn-primary mt-4 sm:mt-0">
          <PlusIcon className="h-5 w-5 mr-1.5" />
          Create Load Balancer
        </button>
      </div>

      {showForm && (
        <div className="card p-6 mb-8">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            {editingLb ? 'Edit Load Balancer' : 'New Load Balancer'}
          </h2>
          <form onSubmit={handleSubmit} className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name *</label>
              <input name="name" value={form.name} onChange={handleChange} required className="input-field" placeholder="web-lb" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Protocol</label>
              <select name="protocol" value={form.protocol} onChange={handleChange} className="input-field" disabled={!!editingLb}>
                {PROTOCOLS.map((p) => <option key={p} value={p}>{p.toUpperCase()}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Algorithm</label>
              <select name="algorithm" value={form.algorithm} onChange={handleChange} className="input-field">
                {ALGORITHMS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Listen Port *</label>
              <input name="listen_port" type="number" min={1} max={65535} value={form.listen_port} onChange={handleChange} required className="input-field" disabled={!!editingLb} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Public IP (VIP) *</label>
              <select
                name="public_ip"
                value={form.public_ip}
                onChange={handleChange}
                required={!editingLb}
                className="input-field font-mono"
              >
                <option value="">{vipChoices.length ? '— Public-IP wählen —' : 'Keine freie Public-IP'}</option>
                {vipChoices.map((ip) => (
                  <option key={ip} value={ip}>{ip}</option>
                ))}
              </select>
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                Eine IP aus {publicSubnetHint}. Clients → VIP → interne Backends.
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Health Check Path</label>
              <input name="health_check_path" value={form.health_check_path} onChange={handleChange} className="input-field" placeholder="/" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Health Check Interval (s)</label>
              <input name="health_check_interval" type="number" min={5} max={300} value={form.health_check_interval} onChange={handleChange} className="input-field" />
            </div>
            <div className="md:col-span-2 lg:col-span-3">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Description</label>
              <input name="description" value={form.description} onChange={handleChange} className="input-field" placeholder="Optional description" />
            </div>
            <div className="md:col-span-2 lg:col-span-3 flex justify-end gap-3">
              <button type="button" onClick={() => { setShowForm(false); setEditingLb(null); setForm(emptyForm); }} className="btn-secondary">
                Cancel
              </button>
              <button type="submit" disabled={submitting} className="btn-primary disabled:opacity-50">
                {submitting ? 'Saving...' : editingLb ? 'Save' : 'Create'}
              </button>
            </div>
          </form>
        </div>
      )}

      {isLoading ? (
        <div className="card p-12 text-center text-gray-500 dark:text-gray-400">Loading...</div>
      ) : lbs.length === 0 ? (
        <div className="card p-12 text-center text-gray-500 dark:text-gray-400">
          <ScaleIcon className="h-10 w-10 mx-auto mb-2 text-gray-400" />
          <p>No load balancers configured</p>
          <p className="text-xs mt-1">1 Public-IP + interne VM-Backends (Round Robin)</p>
        </div>
      ) : (
        <div className="space-y-6">
          {lbs.map((lb) => (
            <div key={lb.id} className="card overflow-hidden">
              <div className="px-6 py-4 flex items-center justify-between bg-gray-50 dark:bg-gray-800/50 border-b border-gray-200 dark:border-gray-700">
                <div className="flex items-center gap-3">
                  <ScaleIcon className="h-5 w-5 text-primary-500" />
                  <div>
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-white">{lb.name}</h3>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      VIP{' '}
                      <span className="font-mono text-gray-700 dark:text-gray-200">
                        {lb.public_ip || '—'}
                      </span>
                      :{lb.listen_port}
                      {' · '}
                      {lb.protocol.toUpperCase()}
                      {' · '}
                      {algoLabel(lb.algorithm)}
                      {' · '}
                      {lb.targets.length} Backend(s)
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium mr-2 ${
                    lb.is_active
                      ? 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                      : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
                  }`}>
                    {lb.is_active ? 'Active' : 'Inactive'}
                  </span>
                  <button onClick={() => handleToggle(lb)} className="p-1.5 rounded text-gray-400 hover:text-primary-600 hover:bg-gray-100 dark:hover:text-primary-400 dark:hover:bg-gray-800" title={lb.is_active ? 'Disable' : 'Enable'}>
                    <PowerIcon className="h-4 w-4" />
                  </button>
                  <button onClick={() => openEdit(lb)} className="p-1.5 rounded text-gray-400 hover:text-primary-600 hover:bg-gray-100 dark:hover:text-primary-400 dark:hover:bg-gray-800" title="Edit">
                    <PencilIcon className="h-4 w-4" />
                  </button>
                  <button onClick={() => handleDelete(lb)} className="p-1.5 rounded text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:text-red-400 dark:hover:bg-red-900/20" title="Delete">
                    <TrashIcon className="h-4 w-4" />
                  </button>
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                  <thead className="bg-gray-50/50 dark:bg-gray-800/30">
                    <tr>
                      <th className="px-6 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">VM</th>
                      <th className="px-6 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Intern (NAT)</th>
                      <th className="px-6 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Port</th>
                      <th className="px-6 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Weight</th>
                      <th className="px-6 py-2"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                    {lb.targets.length === 0 ? (
                      <tr>
                        <td colSpan={5} className="px-6 py-4 text-center text-sm text-gray-400">No backends yet</td>
                      </tr>
                    ) : (
                      lb.targets.map((t) => (
                        <tr key={t.id} className="hover:bg-gray-50 dark:hover:bg-gray-800/30">
                          <td className="px-6 py-3 text-sm text-gray-900 dark:text-white">
                            <div className="flex items-center gap-2">
                              <ServerStackIcon className="h-4 w-4 text-gray-400" />
                              {t.vm_name ?? 'Unknown'}
                            </div>
                          </td>
                          <td className="px-6 py-3 text-sm font-mono text-gray-500 dark:text-gray-400">
                            {t.vm_ip ?? '—'}
                          </td>
                          <td className="px-6 py-3 text-sm text-gray-500 dark:text-gray-400">{t.target_port}</td>
                          <td className="px-6 py-3 text-sm text-gray-500 dark:text-gray-400">{t.weight}</td>
                          <td className="px-6 py-3 text-right">
                            <button onClick={() => handleRemoveTarget(lb.id, t)} className="p-1 rounded text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:text-red-400 dark:hover:bg-red-900/20" title="Remove target">
                              <TrashIcon className="h-4 w-4" />
                            </button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>

              {addTargetLbId === lb.id ? (
                <form onSubmit={handleAddTarget} className="px-6 py-4 border-t border-gray-200 dark:border-gray-700 space-y-3 bg-gray-50/50 dark:bg-gray-800/20">
                  <div className="flex flex-wrap items-end gap-3">
                    <div className="flex-1 min-w-[220px]">
                      <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">VM</label>
                      <select
                        value={targetForm.vm_id}
                        onChange={(e) => setTargetForm((p) => ({ ...p, vm_id: e.target.value }))}
                        required
                        className="input-field text-sm"
                      >
                        <option value="">— Select VM —</option>
                        {vms.map((vm) => {
                          const nat = natIpOf(vm);
                          return (
                            <option key={vm.id} value={vm.id} disabled={!nat}>
                              {vm.name} ({nat || 'keine NAT-IP'})
                            </option>
                          );
                        })}
                      </select>
                      {vms.length === 0 && (
                        <p className="mt-1 text-xs text-red-500">Keine VMs geladen. Seite neu laden.</p>
                      )}
                    </div>
                    <div className="w-44">
                      <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Intern-IP</label>
                      <input
                        readOnly
                        value={previewNatIp || '—'}
                        className="input-field text-sm font-mono bg-gray-100 dark:bg-gray-900/40"
                      />
                    </div>
                    <div className="w-24">
                      <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Port</label>
                      <input type="number" min={1} max={65535} value={targetForm.target_port} onChange={(e) => setTargetForm((p) => ({ ...p, target_port: Number(e.target.value) }))} required className="input-field text-sm" />
                    </div>
                    <div className="w-20">
                      <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Weight</label>
                      <input type="number" min={1} max={100} value={targetForm.weight} onChange={(e) => setTargetForm((p) => ({ ...p, weight: Number(e.target.value) }))} className="input-field text-sm" />
                    </div>
                    <button type="submit" disabled={!previewNatIp} className="btn-primary text-sm py-2 disabled:opacity-50">Add</button>
                    <button type="button" onClick={() => setAddTargetLbId(null)} className="btn-secondary text-sm py-2">Cancel</button>
                  </div>
                  {selectedTargetVm && !previewNatIp && (
                    <p className="text-xs text-amber-600 dark:text-amber-400">
                      {selectedTargetVm.name} hat keine interne/NAT-IP.
                    </p>
                  )}
                </form>
              ) : (
                <div className="px-6 py-3 border-t border-gray-200 dark:border-gray-700">
                  <button onClick={() => { setAddTargetLbId(lb.id); setTargetForm({ vm_id: '', target_port: 80, weight: 1 }); void loadVms(); }} className="text-sm text-primary-600 dark:text-primary-400 hover:underline flex items-center gap-1">
                    <PlusIcon className="h-4 w-4" />
                    Add Backend
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
