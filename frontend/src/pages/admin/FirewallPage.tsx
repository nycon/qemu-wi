import { useEffect, useState, useCallback } from 'react';
import {
  ShieldCheckIcon,
  PlusIcon,
  TrashIcon,
  ArrowPathIcon,
  ExclamationTriangleIcon,
  CheckCircleIcon,
  XCircleIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { systemApi } from '@/services/api';
import type { EgressConfig, IngressConfig } from '@/services/api';

export function FirewallPage() {
  const [config, setConfig] = useState<EgressConfig | null>(null);
  const [ingressConfig, setIngressConfig] = useState<IngressConfig | null>(null);
  const [chainActive, setChainActive] = useState(false);
  const [ingressChainActive, setIngressChainActive] = useState(false);
  const [blockedLog, setBlockedLog] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [showAddPort, setShowAddPort] = useState(false);
  const [newPort, setNewPort] = useState({ port: '', protocol: 'tcp', description: '' });
  const [showAddInboundPort, setShowAddInboundPort] = useState(false);
  const [newInboundPort, setNewInboundPort] = useState({ port: '', protocol: 'tcp', description: '' });
  const [showLog, setShowLog] = useState(false);

  const errorDetail = (err: unknown, fallback: string) =>
    (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || fallback;

  const loadData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [egress, ingress] = await Promise.all([
        systemApi.getEgressRules(),
        systemApi.getIngressRules(),
      ]);
      setConfig(egress.config);
      setChainActive(egress.chain_active);
      setIngressConfig(ingress.config);
      setIngressChainActive(ingress.chain_active);
    } catch {
      toast.error('Failed to load firewall rules');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadLog = async () => {
    try {
      const data = await systemApi.getEgressLog(100);
      setBlockedLog(data.blocked_entries);
      setShowLog(true);
    } catch {
      toast.error('Failed to load blocked log');
    }
  };

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleToggle = async () => {
    if (!config) return;
    setIsSaving(true);
    try {
      await systemApi.updateEgressRules({ enabled: !config.enabled });
      toast.success(config.enabled ? 'Egress filtering disabled' : 'Egress filtering enabled');
      await loadData();
    } catch {
      toast.error('Failed to update');
    } finally {
      setIsSaving(false);
    }
  };

  const handleIngressToggle = async () => {
    if (!ingressConfig) return;
    setIsSaving(true);
    try {
      await systemApi.updateIngressRules({ enabled: !ingressConfig.enabled });
      toast.success(ingressConfig.enabled ? 'Ingress filtering disabled' : 'Ingress filtering enabled');
      await loadData();
    } catch {
      toast.error('Failed to update');
    } finally {
      setIsSaving(false);
    }
  };

  const handleAddPort = async () => {
    const port = parseInt(newPort.port);
    if (!port || port < 1 || port > 65535) {
      toast.error('Invalid port number (1-65535)');
      return;
    }
    setIsSaving(true);
    try {
      const result = await systemApi.addEgressPort({
        port,
        protocol: newPort.protocol,
        description: newPort.description,
      });
      toast.success(result.message);
      setNewPort({ port: '', protocol: 'tcp', description: '' });
      setShowAddPort(false);
      await loadData();
    } catch {
      toast.error('Failed to add port');
    } finally {
      setIsSaving(false);
    }
  };

  const handleRemovePort = async (port: number, protocol: string) => {
    if (!confirm(`Remove port ${protocol.toUpperCase()}:${port} from allowed list?`)) return;
    setIsSaving(true);
    try {
      await systemApi.removeEgressPort(port, protocol);
      toast.success(`Port ${protocol.toUpperCase()}:${port} removed`);
      await loadData();
    } catch {
      toast.error('Failed to remove port');
    } finally {
      setIsSaving(false);
    }
  };

  const handleAddInboundPort = async () => {
    const port = parseInt(newInboundPort.port);
    if (!port || port < 1 || port > 65535) {
      toast.error('Invalid port number (1-65535)');
      return;
    }
    setIsSaving(true);
    try {
      const result = await systemApi.addIngressPort({
        port,
        protocol: newInboundPort.protocol,
        description: newInboundPort.description,
      });
      toast.success(result.message);
      setNewInboundPort({ port: '', protocol: 'tcp', description: '' });
      setShowAddInboundPort(false);
      await loadData();
    } catch {
      toast.error('Failed to add inbound port');
    } finally {
      setIsSaving(false);
    }
  };

  const handleRemoveInboundPort = async (port: number, protocol: string) => {
    if (!confirm(`Remove inbound port ${protocol.toUpperCase()}:${port} from allowed list?`)) return;
    setIsSaving(true);
    try {
      await systemApi.removeIngressPort(port, protocol);
      toast.success(`Inbound port ${protocol.toUpperCase()}:${port} removed`);
      await loadData();
    } catch {
      toast.error('Failed to remove inbound port');
    } finally {
      setIsSaving(false);
    }
  };

  const handleAllowAllForTesting = async () => {
    if (!confirm('Enable test mode and allow all inbound + outbound traffic?')) return;
    setIsSaving(true);
    try {
      await Promise.all([
        systemApi.updateIngressRules({ enabled: false }),
        systemApi.updateEgressRules({ enabled: false }),
      ]);
      toast.success('Test mode enabled: inbound and outbound are fully open');
      await loadData();
    } catch (err: unknown) {
      toast.error(errorDetail(err, 'Failed to enable test mode'));
    } finally {
      setIsSaving(false);
    }
  };

  const handleRateLimitUpdate = async (connPerSec: number, burst: number) => {
    setIsSaving(true);
    try {
      await systemApi.updateEgressRules({
        rate_limit_per_sec: connPerSec,
        rate_limit_burst: burst,
      });
      toast.success('Rate limit updated');
      await loadData();
    } catch {
      toast.error('Failed to update rate limit');
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-500" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <ShieldCheckIcon className="h-7 w-7 text-primary-500" />
            Firewall / Egress Filtering
          </h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Control which outgoing ports VMs are allowed to use. Blocks port scans and unauthorized traffic.
          </p>
        </div>
        <button
          onClick={loadData}
          className="btn btn-secondary flex items-center gap-1.5"
          disabled={isLoading}
        >
          <ArrowPathIcon className="h-4 w-4" />
          Refresh
        </button>
      </div>

      <div className="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-xl p-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-yellow-800 dark:text-yellow-300">
          Test mode: temporarily disable both filters (inbound + outbound allow all).
        </p>
        <button
          onClick={handleAllowAllForTesting}
          disabled={isSaving}
          className="btn btn-secondary"
        >
          Allow all inbound + outbound (test)
        </button>
      </div>

      {/* Status Card */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-500 dark:text-gray-400">Ingress Filter</p>
              <p className="mt-1 text-lg font-semibold">
                {ingressConfig?.enabled ? (
                  <span className="text-green-600 dark:text-green-400 flex items-center gap-1">
                    <CheckCircleIcon className="h-5 w-5" /> Active
                  </span>
                ) : (
                  <span className="text-yellow-600 dark:text-yellow-400 flex items-center gap-1">
                    <XCircleIcon className="h-5 w-5" /> Open (test mode)
                  </span>
                )}
              </p>
            </div>
            <button
              onClick={handleIngressToggle}
              disabled={isSaving}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                ingressConfig?.enabled
                  ? 'bg-red-100 text-red-700 hover:bg-red-200 dark:bg-red-900/30 dark:text-red-400'
                  : 'bg-green-100 text-green-700 hover:bg-green-200 dark:bg-green-900/30 dark:text-green-400'
              }`}
            >
              {ingressConfig?.enabled ? 'Disable' : 'Enable'}
            </button>
          </div>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-500 dark:text-gray-400">Egress Filter</p>
              <p className="mt-1 text-lg font-semibold">
                {config?.enabled ? (
                  <span className="text-green-600 dark:text-green-400 flex items-center gap-1">
                    <CheckCircleIcon className="h-5 w-5" /> Active
                  </span>
                ) : (
                  <span className="text-red-600 dark:text-red-400 flex items-center gap-1">
                    <XCircleIcon className="h-5 w-5" /> Disabled
                  </span>
                )}
              </p>
            </div>
            <button
              onClick={handleToggle}
              disabled={isSaving}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                config?.enabled
                  ? 'bg-red-100 text-red-700 hover:bg-red-200 dark:bg-red-900/30 dark:text-red-400'
                  : 'bg-green-100 text-green-700 hover:bg-green-200 dark:bg-green-900/30 dark:text-green-400'
              }`}
            >
              {config?.enabled ? 'Disable' : 'Enable'}
            </button>
          </div>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
          <p className="text-sm text-gray-500 dark:text-gray-400">Ingress Chain</p>
          <p className="mt-1 text-lg font-semibold">
            {ingressChainActive ? (
              <span className="text-green-600 dark:text-green-400">VM_INGRESS active</span>
            ) : (
              <span className="text-yellow-600 dark:text-yellow-400">Not loaded</span>
            )}
          </p>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
          <p className="text-sm text-gray-500 dark:text-gray-400">Egress Chain</p>
          <p className="mt-1 text-lg font-semibold">
            {chainActive ? (
              <span className="text-green-600 dark:text-green-400">VM_EGRESS active</span>
            ) : (
              <span className="text-yellow-600 dark:text-yellow-400">Not loaded</span>
            )}
          </p>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
          <p className="text-sm text-gray-500 dark:text-gray-400">Allowed Inbound Ports</p>
          <p className="mt-1 text-lg font-semibold text-gray-900 dark:text-white">
            {ingressConfig?.allowed_ports?.length ?? 0} rules
          </p>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
          <p className="text-sm text-gray-500 dark:text-gray-400">Allowed Outbound Ports</p>
          <p className="mt-1 text-lg font-semibold text-gray-900 dark:text-white">
            {config?.allowed_ports?.length ?? 0} rules
          </p>
        </div>
      </div>

      {/* Inbound Allowed Ports Table */}
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-900 dark:text-white">
            Allowed Incoming Ports (Public VMs)
          </h3>
          <button
            onClick={() => setShowAddInboundPort(!showAddInboundPort)}
            className="btn btn-primary flex items-center gap-1.5 text-sm"
          >
            <PlusIcon className="h-4 w-4" />
            Add Port
          </button>
        </div>

        {showAddInboundPort && (
          <div className="px-5 py-4 bg-gray-50 dark:bg-gray-900/50 border-b border-gray-200 dark:border-gray-700">
            <div className="flex items-end gap-3 flex-wrap">
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Port</label>
                <input
                  type="number"
                  min={1}
                  max={65535}
                  value={newInboundPort.port}
                  onChange={(e) => setNewInboundPort({ ...newInboundPort, port: e.target.value })}
                  placeholder="e.g. 443"
                  className="input w-28"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Protocol</label>
                <select
                  value={newInboundPort.protocol}
                  onChange={(e) => setNewInboundPort({ ...newInboundPort, protocol: e.target.value })}
                  className="input w-24"
                >
                  <option value="tcp">TCP</option>
                  <option value="udp">UDP</option>
                </select>
              </div>
              <div className="flex-1 min-w-[150px]">
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Description</label>
                <input
                  type="text"
                  value={newInboundPort.description}
                  onChange={(e) => setNewInboundPort({ ...newInboundPort, description: e.target.value })}
                  placeholder="e.g. HTTPS"
                  className="input"
                />
              </div>
              <button onClick={handleAddInboundPort} disabled={isSaving} className="btn btn-primary">
                Add
              </button>
              <button onClick={() => setShowAddInboundPort(false)} className="btn btn-secondary">
                Cancel
              </button>
            </div>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50">
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Port</th>
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Protocol</th>
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Description</th>
                <th className="px-5 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {ingressConfig?.allowed_ports?.map((rule, idx) => (
                <tr key={`${rule.protocol}-${rule.port}-${idx}`} className="hover:bg-gray-50 dark:hover:bg-gray-900/30">
                  <td className="px-5 py-3 text-sm font-mono text-gray-900 dark:text-white">{rule.port}</td>
                  <td className="px-5 py-3 text-sm">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                      rule.protocol === 'tcp'
                        ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400'
                        : 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400'
                    }`}>
                      {rule.protocol.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-sm text-gray-700 dark:text-gray-300">{rule.description || '-'}</td>
                  <td className="px-5 py-3 text-right">
                    <button
                      onClick={() => handleRemoveInboundPort(rule.port, rule.protocol)}
                      disabled={isSaving}
                      className="text-red-500 hover:text-red-700 dark:hover:text-red-400 p-1 rounded transition-colors"
                      title="Remove port"
                    >
                      <TrashIcon className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
              {(!ingressConfig?.allowed_ports || ingressConfig.allowed_ports.length === 0) && (
                <tr>
                  <td colSpan={4} className="px-5 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
                    No inbound rules configured. Ingress filter would block all inbound traffic.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Rate Limit */}
      {config && (
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
          <h3 className="text-base font-semibold text-gray-900 dark:text-white mb-3">
            Rate Limiting (Anti-Scan Protection)
          </h3>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
            Limits new outgoing connections per second from VMs. Prevents automated port scanning even on allowed ports.
          </p>
          <div className="flex items-center gap-4 flex-wrap">
            <div>
              <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">
                Max new connections/sec
              </label>
              <input
                type="number"
                min={1}
                max={200}
                value={config.rate_limit?.connections_per_sec ?? 20}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    rate_limit: { ...config.rate_limit, connections_per_sec: parseInt(e.target.value) || 20 },
                  })
                }
                className="input w-28"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Burst</label>
              <input
                type="number"
                min={1}
                max={500}
                value={config.rate_limit?.burst ?? 40}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    rate_limit: { ...config.rate_limit, burst: parseInt(e.target.value) || 40 },
                  })
                }
                className="input w-28"
              />
            </div>
            <div className="pt-5">
              <button
                onClick={() =>
                  handleRateLimitUpdate(
                    config.rate_limit?.connections_per_sec ?? 20,
                    config.rate_limit?.burst ?? 40
                  )
                }
                disabled={isSaving}
                className="btn btn-primary"
              >
                Save Rate Limit
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Allowed Ports Table */}
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-900 dark:text-white">
            Allowed Outgoing Ports
          </h3>
          <button
            onClick={() => setShowAddPort(!showAddPort)}
            className="btn btn-primary flex items-center gap-1.5 text-sm"
          >
            <PlusIcon className="h-4 w-4" />
            Add Port
          </button>
        </div>

        {/* Add Port Form */}
        {showAddPort && (
          <div className="px-5 py-4 bg-gray-50 dark:bg-gray-900/50 border-b border-gray-200 dark:border-gray-700">
            <div className="flex items-end gap-3 flex-wrap">
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Port</label>
                <input
                  type="number"
                  min={1}
                  max={65535}
                  value={newPort.port}
                  onChange={(e) => setNewPort({ ...newPort, port: e.target.value })}
                  placeholder="e.g. 3389"
                  className="input w-28"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Protocol</label>
                <select
                  value={newPort.protocol}
                  onChange={(e) => setNewPort({ ...newPort, protocol: e.target.value })}
                  className="input w-24"
                >
                  <option value="tcp">TCP</option>
                  <option value="udp">UDP</option>
                </select>
              </div>
              <div className="flex-1 min-w-[150px]">
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">
                  Description
                </label>
                <input
                  type="text"
                  value={newPort.description}
                  onChange={(e) => setNewPort({ ...newPort, description: e.target.value })}
                  placeholder="e.g. RDP"
                  className="input"
                />
              </div>
              <button onClick={handleAddPort} disabled={isSaving} className="btn btn-primary">
                Add
              </button>
              <button onClick={() => setShowAddPort(false)} className="btn btn-secondary">
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50">
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Port
                </th>
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Protocol
                </th>
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Description
                </th>
                <th className="px-5 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {config?.allowed_ports?.map((rule, idx) => (
                <tr key={`${rule.protocol}-${rule.port}-${idx}`} className="hover:bg-gray-50 dark:hover:bg-gray-900/30">
                  <td className="px-5 py-3 text-sm font-mono text-gray-900 dark:text-white">
                    {rule.port}
                  </td>
                  <td className="px-5 py-3 text-sm">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                      rule.protocol === 'tcp'
                        ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400'
                        : 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400'
                    }`}>
                      {rule.protocol.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-sm text-gray-700 dark:text-gray-300">
                    {rule.description || '-'}
                  </td>
                  <td className="px-5 py-3 text-right">
                    <button
                      onClick={() => handleRemovePort(rule.port, rule.protocol)}
                      disabled={isSaving}
                      className="text-red-500 hover:text-red-700 dark:hover:text-red-400 p-1 rounded transition-colors"
                      title="Remove port"
                    >
                      <TrashIcon className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
              {(!config?.allowed_ports || config.allowed_ports.length === 0) && (
                <tr>
                  <td colSpan={4} className="px-5 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
                    No egress rules configured. All outbound VM traffic will be blocked.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Blocked Traffic Log */}
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div>
            <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
              <ExclamationTriangleIcon className="h-5 w-5 text-yellow-500" />
              Blocked Traffic Log
            </h3>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              Shows recent blocked outgoing connection attempts from VMs (portscan attempts, etc.)
            </p>
          </div>
          <button
            onClick={loadLog}
            className="btn btn-secondary flex items-center gap-1.5 text-sm"
          >
            <ArrowPathIcon className="h-4 w-4" />
            Load Log
          </button>
        </div>

        {showLog && (
          <div className="p-4 max-h-96 overflow-y-auto">
            {blockedLog.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">
                No blocked entries found. This is good!
              </p>
            ) : (
              <div className="space-y-1">
                {blockedLog.map((entry, idx) => (
                  <div
                    key={idx}
                    className="text-xs font-mono text-gray-700 dark:text-gray-300 bg-gray-50 dark:bg-gray-900/50 rounded px-3 py-1.5"
                  >
                    {entry}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Info Box */}
      <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-xl p-5">
        <h4 className="text-sm font-semibold text-blue-800 dark:text-blue-300 mb-2">
          Firewall behavior
        </h4>
        <ul className="text-sm text-blue-700 dark:text-blue-400 space-y-1 list-disc list-inside">
          <li>Default inbound policy allows only 22, 80 and 443 to public VMs</li>
          <li>Default outbound policy is open (egress filter disabled)</li>
          <li>You can enable egress filtering to whitelist outbound ports</li>
          <li>Rate limiting helps prevent automated scans on allowed egress ports</li>
          <li>Use test mode to temporarily allow all inbound and outbound traffic</li>
          <li>VM-to-VM traffic on internal networks is always allowed</li>
          <li>Review blocked traffic logs before tightening rules further</li>
        </ul>
      </div>
    </div>
  );
}
