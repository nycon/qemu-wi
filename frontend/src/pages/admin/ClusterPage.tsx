import { useEffect, useState } from 'react';
import {
  ServerStackIcon,
  PlusIcon,
  TrashIcon,
  ArrowPathIcon,
  SignalIcon,
  SignalSlashIcon,
  PencilIcon,
  CpuChipIcon,
  CircleStackIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { clusterApi } from '@/services/api';
import type { ClusterNodeItem, ClusterOverview, ClusterVMItem } from '@/services/api';

const statusColors: Record<string, string> = {
  online: 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  offline: 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  maintenance: 'bg-yellow-50 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  unknown: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400',
};

const vmStatusColors: Record<string, string> = {
  running: 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  stopped: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400',
  creating: 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  error: 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-400',
};

function ProgressBar({ used, total, label, unit }: { used: number; total: number; label: string; unit: string }) {
  const pct = total > 0 ? Math.min(Math.round((used / total) * 100), 100) : 0;
  const color = pct > 85 ? 'bg-red-500' : pct > 60 ? 'bg-yellow-500' : 'bg-primary-500';
  return (
    <div>
      <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
        <span>{label}</span>
        <span>{used} / {total} {unit} ({pct}%)</span>
      </div>
      <div className="w-full bg-gray-200 rounded-full h-2 dark:bg-gray-700">
        <div className={`${color} h-2 rounded-full transition-all`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

const emptyForm = { name: '', description: '', url: '', api_key: '' };

export function ClusterPage() {
  const [overview, setOverview] = useState<ClusterOverview | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncingNodeId, setSyncingNodeId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [vmFilter, setVmFilter] = useState('');

  const load = async () => {
    setIsLoading(true);
    try {
      const data = await clusterApi.overview();
      setOverview(data);
    } catch {
      // No nodes yet is fine
      setOverview(null);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleSyncAll = async () => {
    setSyncing(true);
    try {
      const res = await clusterApi.syncAll();
      toast.success(`Synced: ${res.synced} online, ${res.errors} offline`);
      load();
    } catch {
      toast.error('Sync failed');
    } finally {
      setSyncing(false);
    }
  };

  const handleSyncNode = async (nodeId: string) => {
    setSyncingNodeId(nodeId);
    try {
      await clusterApi.syncNode(nodeId);
      toast.success('Node synced');
      load();
    } catch {
      toast.error('Sync failed');
    } finally {
      setSyncingNodeId(null);
    }
  };

  const handleAddNode = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await clusterApi.addNode({
        name: form.name,
        description: form.description || undefined,
        url: form.url,
        api_key: form.api_key,
      });
      toast.success('Node added');
      setShowForm(false);
      setForm(emptyForm);
      load();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed to add node';
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const handleRemoveNode = async (node: ClusterNodeItem) => {
    if (!confirm(`Remove node "${node.name}" from cluster?`)) return;
    try {
      await clusterApi.removeNode(node.id);
      toast.success('Node removed');
      load();
    } catch {
      toast.error('Remove failed');
    }
  };

  const filteredVms = (overview?.vms ?? []).filter((vm) =>
    !vmFilter ||
    vm.name.toLowerCase().includes(vmFilter.toLowerCase()) ||
    vm.node_name.toLowerCase().includes(vmFilter.toLowerCase()) ||
    vm.hostname.toLowerCase().includes(vmFilter.toLowerCase())
  );

  const nodes = overview?.nodes ?? [];

  return (
    <div>
      {/* Header */}
      <div className="sm:flex sm:items-center sm:justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Cluster</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {overview
              ? `${overview.total_nodes} node(s) — ${overview.online_nodes} online — ${overview.total_vms} VM(s)`
              : 'No nodes registered'}
          </p>
        </div>
        <div className="flex gap-2 mt-4 sm:mt-0">
          <button onClick={handleSyncAll} disabled={syncing} className="btn-secondary">
            <ArrowPathIcon className={`h-5 w-5 mr-1.5 ${syncing ? 'animate-spin' : ''}`} />
            Sync All
          </button>
          <button onClick={() => { setShowForm(true); setForm(emptyForm); }} className="btn-primary">
            <PlusIcon className="h-5 w-5 mr-1.5" />
            Add Node
          </button>
        </div>
      </div>

      {/* Add node form */}
      {showForm && (
        <div className="card p-6 mb-8">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">Add Node</h2>
          <form onSubmit={handleAddNode} className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name *</label>
              <input value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))} required className="input-field" placeholder="node-2" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">URL *</label>
              <input value={form.url} onChange={(e) => setForm((p) => ({ ...p, url: e.target.value }))} required className="input-field" placeholder="https://192.168.1.100" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">API Key *</label>
              <input value={form.api_key} onChange={(e) => setForm((p) => ({ ...p, api_key: e.target.value }))} required className="input-field font-mono" placeholder="qi_..." />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Description</label>
              <input value={form.description} onChange={(e) => setForm((p) => ({ ...p, description: e.target.value }))} className="input-field" placeholder="Production server 2" />
            </div>
            <div className="md:col-span-2 flex justify-end gap-3">
              <button type="button" onClick={() => setShowForm(false)} className="btn-secondary">Cancel</button>
              <button type="submit" disabled={submitting} className="btn-primary disabled:opacity-50">
                {submitting ? 'Connecting...' : 'Add Node'}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Aggregate stats */}
      {overview && overview.total_nodes > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <div className="card p-4">
            <div className="flex items-center gap-3">
              <ServerStackIcon className="h-8 w-8 text-primary-500" />
              <div>
                <p className="text-2xl font-bold text-gray-900 dark:text-white">{overview.online_nodes}/{overview.total_nodes}</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">Nodes Online</p>
              </div>
            </div>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-3">
              <ServerStackIcon className="h-8 w-8 text-green-500" />
              <div>
                <p className="text-2xl font-bold text-gray-900 dark:text-white">{overview.running_vms}/{overview.total_vms}</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">VMs Running</p>
              </div>
            </div>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-3">
              <CpuChipIcon className="h-8 w-8 text-amber-500" />
              <div>
                <p className="text-2xl font-bold text-gray-900 dark:text-white">{overview.used_vcpus}/{overview.total_vcpus}</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">vCPUs Used</p>
              </div>
            </div>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-3">
              <CircleStackIcon className="h-8 w-8 text-purple-500" />
              <div>
                <p className="text-2xl font-bold text-gray-900 dark:text-white">{Math.round(overview.used_ram_mb / 1024)}/{Math.round(overview.total_ram_mb / 1024)} GB</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">RAM Used</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Nodes */}
      {isLoading ? (
        <div className="card p-12 text-center text-gray-500 dark:text-gray-400">Loading...</div>
      ) : nodes.length === 0 ? (
        <div className="card p-12 text-center text-gray-500 dark:text-gray-400">
          <ServerStackIcon className="h-10 w-10 mx-auto mb-2 text-gray-400" />
          <p>No cluster nodes registered</p>
          <p className="text-xs mt-1">Add remote QEMU Interface instances to manage them from here</p>
        </div>
      ) : (
        <>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">Nodes</h2>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-8">
            {nodes.map((node) => (
              <div key={node.id} className="card p-5">
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    {node.status === 'online'
                      ? <SignalIcon className="h-5 w-5 text-green-500" />
                      : <SignalSlashIcon className="h-5 w-5 text-red-500" />
                    }
                    <div>
                      <h3 className="text-sm font-semibold text-gray-900 dark:text-white">{node.name}</h3>
                      <p className="text-xs text-gray-500 dark:text-gray-400 font-mono">{node.url}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${statusColors[node.status] ?? statusColors.unknown}`}>
                      {node.status}
                    </span>
                    <button onClick={() => handleSyncNode(node.id)} disabled={syncingNodeId === node.id} className="p-1.5 rounded text-gray-400 hover:text-primary-600 hover:bg-gray-100 dark:hover:text-primary-400 dark:hover:bg-gray-800" title="Sync">
                      <ArrowPathIcon className={`h-4 w-4 ${syncingNodeId === node.id ? 'animate-spin' : ''}`} />
                    </button>
                    <button onClick={() => handleRemoveNode(node)} className="p-1.5 rounded text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:text-red-400 dark:hover:bg-red-900/20" title="Remove">
                      <TrashIcon className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {node.status === 'online' ? (
                  <div className="space-y-3">
                    <div className="flex justify-between text-sm">
                      <span className="text-gray-500 dark:text-gray-400">{node.running_vms} / {node.total_vms} VMs running</span>
                      <span className="text-gray-400 dark:text-gray-500 text-xs">v{node.app_version ?? '?'}</span>
                    </div>
                    <ProgressBar used={node.used_vcpus} total={node.total_vcpus} label="CPU" unit="vCPUs" />
                    <ProgressBar used={Math.round(node.used_ram_mb / 1024)} total={Math.round(node.total_ram_mb / 1024)} label="RAM" unit="GB" />
                    <ProgressBar used={node.used_storage_gb} total={node.total_storage_gb} label="Storage" unit="GB" />
                  </div>
                ) : (
                  <div className="text-sm text-red-500 dark:text-red-400">
                    {node.last_error ? node.last_error.substring(0, 120) : 'Node unreachable'}
                  </div>
                )}

                {node.description && (
                  <p className="text-xs text-gray-400 mt-3">{node.description}</p>
                )}
              </div>
            ))}
          </div>

          {/* All VMs across cluster */}
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">All VMs ({overview?.vms.length ?? 0})</h2>
            <input
              value={vmFilter}
              onChange={(e) => setVmFilter(e.target.value)}
              className="input-field w-64"
              placeholder="Filter by name, node..."
            />
          </div>

          <div className="card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-800/50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">VM</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Node</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Status</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Resources</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                  {filteredVms.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="px-6 py-8 text-center text-gray-500 dark:text-gray-400 text-sm">
                        {vmFilter ? 'No VMs matching filter' : 'No VMs across cluster'}
                      </td>
                    </tr>
                  ) : (
                    filteredVms.map((vm) => (
                      <tr key={`${vm.node_id}-${vm.id}`} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                        <td className="px-6 py-3">
                          <div className="text-sm font-medium text-gray-900 dark:text-white">{vm.name}</div>
                          <div className="text-xs text-gray-500 dark:text-gray-400">{vm.hostname}</div>
                        </td>
                        <td className="px-6 py-3">
                          <span className="text-sm text-gray-600 dark:text-gray-300">{vm.node_name}</span>
                        </td>
                        <td className="px-6 py-3">
                          <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${vmStatusColors[vm.status] ?? vmStatusColors.stopped}`}>
                            {vm.status}
                          </span>
                        </td>
                        <td className="px-6 py-3 text-sm text-gray-500 dark:text-gray-400">
                          {vm.vcpus} vCPU · {vm.ram_mb >= 1024 ? `${(vm.ram_mb / 1024).toFixed(0)} GB` : `${vm.ram_mb} MB`} · {vm.disk_size_gb} GB
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
