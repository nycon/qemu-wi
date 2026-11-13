import { Component, useCallback, useEffect, useRef, useState, type ErrorInfo, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  AdjustmentsHorizontalIcon,
  ArrowDownTrayIcon,
  ArrowUpTrayIcon,
  ArchiveBoxIcon,
  ArrowLeftIcon,
  ArrowPathIcon,
  ArrowsPointingInIcon,
  ArrowsPointingOutIcon,
  ArrowUturnLeftIcon,
  CheckCircleIcon,
  XCircleIcon,
  ClockIcon,
  CommandLineIcon,
  ComputerDesktopIcon,
  CpuChipIcon,
  DocumentDuplicateIcon,
  ExclamationTriangleIcon,
  GlobeAltIcon,
  KeyIcon,
  PlayIcon,
  ShieldCheckIcon,
  ServerStackIcon,
  SignalIcon,
  SignalSlashIcon,
  StopIcon,
  TrashIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { useTenantStore } from '@/store/tenantStore';
import { useAuthStore } from '@/store/authStore';
import { vmApi, portForwardApi, ipamApi, storageApi, systemApi, type DiskItem, type VMFirewallProfile } from '@/services/api';
import type { VirtualMachine, PortForward, IPPool, IPAllocation, StoragePool } from '@/types';
import { PencilIcon } from '@heroicons/react/24/outline';
import { localTimeToUtc, utcTimeToLocal, localTzLabel } from '@/lib/scheduleTime';

/* ── Network helpers ───────────────────────────────────────────── */

function extractPrimaryVmIp(vm: VirtualMachine): string | null {
  const net = vm.cloud_init_network;
  if (!net || !net.ethernets) return null;
  const ethernets = net.ethernets as Record<string, Record<string, unknown>>;
  for (const cfg of Object.values(ethernets)) {
    if (Array.isArray(cfg.addresses) && cfg.addresses.length > 0) {
      return String(cfg.addresses[0]).split('/')[0];
    }
  }
  return null;
}

function isPrivateOrNatIp(ip: string): boolean {
  const parts = ip.split('.').map((x) => parseInt(x, 10));
  if (parts.length !== 4 || parts.some((n) => Number.isNaN(n))) return false;
  const [a, b] = parts;
  if (a === 10) return true;
  if (a === 192 && b === 168) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 100 && b >= 64 && b <= 127) return true; // CGNAT
  return false;
}

/** Port-forwarding only applies to NAT; public/external VMs are reached directly. */
function resolveVmNetworkKind(vm: VirtualMachine): 'nat' | 'external' | 'internal' | null {
  const bridge = (vm.bridge || '').toLowerCase();
  if (bridge.includes('qemu-nat') || bridge.includes('virbr')) return 'nat';
  if (bridge.includes('qemu-int')) return 'internal';
  const ip = extractPrimaryVmIp(vm);
  if (ip) return isPrivateOrNatIp(ip) ? 'nat' : 'external';
  if (bridge.startsWith('network:')) return 'nat';
  if (bridge) return 'external';
  return null;
}

/* ── Status Config ─────────────────────────────────────────────── */

const statusConfig: Record<string, { label: string; color: string; dot: string; bg: string }> = {
  running:   { label: 'Running',           color: 'text-green-700 dark:text-green-400',  dot: 'bg-green-500',                  bg: 'bg-green-100 dark:bg-green-900/30' },
  stopped:   { label: 'Stopped',          color: 'text-gray-700 dark:text-gray-400',    dot: 'bg-gray-400',                   bg: 'bg-gray-100 dark:bg-gray-800' },
  creating:  { label: 'Creating...',   color: 'text-blue-700 dark:text-blue-400',    dot: 'bg-blue-500 animate-pulse',     bg: 'bg-blue-100 dark:bg-blue-900/30' },
  starting:  { label: 'Starting...',         color: 'text-yellow-700 dark:text-yellow-400',dot: 'bg-yellow-500 animate-pulse',   bg: 'bg-yellow-100 dark:bg-yellow-900/30' },
  stopping:  { label: 'Stopping...',          color: 'text-yellow-700 dark:text-yellow-400',dot: 'bg-yellow-500 animate-pulse',   bg: 'bg-yellow-100 dark:bg-yellow-900/30' },
  rebooting: { label: 'Rebooting...',        color: 'text-yellow-700 dark:text-yellow-400',dot: 'bg-yellow-500 animate-pulse',   bg: 'bg-yellow-100 dark:bg-yellow-900/30' },
  error:     { label: 'Error',             color: 'text-red-700 dark:text-red-400',      dot: 'bg-red-500',                    bg: 'bg-red-100 dark:bg-red-900/30' },
  deleting:  { label: 'Deleting...',   color: 'text-red-700 dark:text-red-400',      dot: 'bg-red-500 animate-pulse',      bg: 'bg-red-100 dark:bg-red-900/30' },
  deleted:   { label: 'Deleted',           color: 'text-gray-500 dark:text-gray-400',     dot: 'bg-gray-300 dark:bg-gray-600',   bg: 'bg-gray-100 dark:bg-gray-800' },
};

/* ── Main Component ────────────────────────────────────────────── */

export function VMDetailPage() {
  const { vmId } = useParams<{ vmId: string }>();
  const { currentTenant } = useTenantStore();
  const isSuperadmin = useAuthStore((s) => s.user?.is_superadmin === true);
  const navigate = useNavigate();
  const [showNetplanRaw, setShowNetplanRaw] = useState(false);
  const [vm, setVm] = useState<VirtualMachine | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [showConsole, setShowConsole] = useState(false);
  const [portForwards, setPortForwards] = useState<PortForward[]>([]);
  const [showPFForm, setShowPFForm] = useState(false);
  const [pfForm, setPfForm] = useState({ protocol: 'tcp', external_port: '', internal_port: '', description: '' });
  const [pfLoading, setPfLoading] = useState(false);
  const [showChangeIP, setShowChangeIP] = useState(false);
  const [storagePool, setStoragePool] = useState<StoragePool | null>(null);
  const loadPortForwards = useCallback(async () => {
    if (!vmId) return;
    try {
      const data = await portForwardApi.list({ vm_id: vmId });
      setPortForwards(data);
    } catch { /* ignore */ }
  }, [vmId]);

  /** Derive network kind without IPAM admin APIs (works for all roles). */
  const primaryPoolType = vm ? resolveVmNetworkKind(vm) : null;

  const handleAddPF = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!vmId) return;
    setPfLoading(true);
    try {
      await portForwardApi.create({
        vm_id: vmId,
        protocol: pfForm.protocol,
        external_port: Number(pfForm.external_port),
        internal_port: Number(pfForm.internal_port),
        description: pfForm.description || undefined,
      });
      toast.success('Port forward created');
      setPfForm({ protocol: 'tcp', external_port: '', internal_port: '', description: '' });
      setShowPFForm(false);
      loadPortForwards();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Error creating';
      toast.error(msg);
    } finally {
      setPfLoading(false);
    }
  };

  const handleDeletePF = async (id: string) => {
    if (!confirm('Really delete port forward?')) return;
    try {
      await portForwardApi.delete(id);
      toast.success('Port forward deleted');
      loadPortForwards();
    } catch {
      toast.error('Delete failed');
    }
  };

  const loadVm = useCallback(async () => {
    if (!currentTenant || !vmId) return;
    try {
      const data = await vmApi.get(currentTenant.id, vmId);
      setVm(data);
      // Load storage pool name if assigned
      if (data.storage_pool_id && !storagePool) {
        try {
          const pools = await storageApi.listPools();
          const pool = pools.find((p) => p.id === data.storage_pool_id);
          if (pool) setStoragePool(pool);
        } catch { /* ignore */ }
      }
    } catch {
      toast.error('VM could not be loaded');
    } finally {
      setIsLoading(false);
    }
  }, [currentTenant, vmId, storagePool]);

  useEffect(() => {
    loadVm();
    loadPortForwards();
    const interval = setInterval(loadVm, 5000);
    return () => clearInterval(interval);
  }, [loadVm, loadPortForwards]);

  const handleAction = async (action: string) => {
    if (!currentTenant || !vmId) return;
    setActionLoading(action);
    try {
      await vmApi.action(currentTenant.id, vmId, action);
      toast.success(
        action === 'start' ? 'VM is starting' :
        action === 'stop' ? 'VM is stopping' :
        action === 'reboot' ? 'VM is rebooting' :
        action === 'force_stop' ? 'VM is being force stopped' :
        `Action '${action}' executed`
      );
      setTimeout(loadVm, 1000);
    } catch {
      toast.error(`Action '${action}' failed`);
    } finally {
      setActionLoading(null);
    }
  };

  const handleEjectInstaller = async () => {
    if (!currentTenant || !vmId) return;
    setActionLoading('eject_installer');
    try {
      const result = await vmApi.ejectInstaller(currentTenant.id, vmId);
      toast.success(result.message || 'Installer media ejected');
      setTimeout(loadVm, 500);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Eject failed';
      toast.error(msg);
    } finally {
      setActionLoading(null);
    }
  };

  const handleAttachVirtio = async () => {
    if (!currentTenant || !vmId) return;
    setActionLoading('attach_virtio');
    try {
      const result = await vmApi.attachVirtio(currentTenant.id, vmId);
      toast.success(result.message || 'VirtIO ISO attached');
      setTimeout(loadVm, 500);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Attach VirtIO failed';
      toast.error(msg);
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async (force = false) => {
    if (!currentTenant || !vmId || !vm) return;
    const msg = force
      ? `VM "${vm.name}" FORCE-DELETE? The VM will be deleted immediately!`
      : `VM "${vm.name}" really delete? All data will be lost.`;
    if (!window.confirm(msg)) return;

    setActionLoading('delete');
    try {
      await vmApi.delete(currentTenant.id, vmId, force);
      toast.success('VM is being deleted');
      navigate('/vms');
    } catch {
      toast.error('Delete failed');
    } finally {
      setActionLoading(null);
    }
  };

  const handleClone = async () => {
    if (!currentTenant || !vm) return;
    const cloneName = window.prompt(`New name for clone of "${vm.name}":`, `${vm.name}-clone`);
    if (!cloneName) return;

    setActionLoading('clone');
    try {
      let password = '';
      try {
        const generated = await systemApi.generatePassword(16);
        password = generated.password;
      } catch {
        password = window.prompt(
          'Passwort für den Clone-User "cloud" (min. 8 Zeichen, Groß/Klein/Zahl/Sonderzeichen):',
          '',
        ) || '';
      }
      if (!password || password.length < 8) {
        toast.error('Clone abgebrochen: kein gültiges Passwort');
        return;
      }
      const clonedVm = await vmApi.clone(currentTenant.id, vm.id, {
        name: cloneName,
        project_id: vm.project_id,
        cloud_init_identity: {
          hostname: cloneName.replace(/[^a-zA-Z0-9-]/g, '-').toLowerCase(),
          default_user: 'cloud',
          password,
          disable_password_login: false,
        },
      });
      toast.success(`VM "${cloneName}" wird erstellt. Cloud-Passwort: ${password}`);
      navigate(`/vms/${clonedVm.id}`);
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(`Clone failed: ${detail}`);
    } finally {
      setActionLoading(null);
    }
  };

  const handleExport = async () => {
    if (!currentTenant || !vm) return;
    if (!window.confirm(`Export VM "${vm.name}" as qcow2? The VM must be stopped.\n\nThis may take several minutes depending on disk size.`)) return;

    setActionLoading('export');
    try {
      const result = await vmApi.exportVM(currentTenant.id, vm.id);
      toast.success(result.message);
      // Poll for completion
      const poll = setInterval(async () => {
        try {
          const status = await vmApi.getExportStatus(currentTenant.id, vm.id);
          if (status.status === 'success') {
            clearInterval(poll);
            toast.success(`Export ready! Downloading...`);
            // Trigger download
            const blob = await vmApi.downloadExport(currentTenant.id, vm.id);
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = status.file_name || `${vm.name}.qcow2`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
            setActionLoading(null);
          } else if (status.status === 'failed') {
            clearInterval(poll);
            toast.error(`Export failed: ${status.error || 'Unknown error'}`);
            setActionLoading(null);
          }
        } catch {
          clearInterval(poll);
          setActionLoading(null);
        }
      }, 5000);
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(`Export failed: ${detail}`);
      setActionLoading(null);
    }
  };

  if (!currentTenant) {
    return <div className="text-center py-12 text-gray-500 dark:text-gray-400">Please select a tenant.</div>;
  }

  if (isLoading) {
    return (
      <div className="text-center py-20">
        <ArrowPathIcon className="h-8 w-8 animate-spin mx-auto mb-3 text-primary-500" />
        <p className="text-gray-500 dark:text-gray-400">Loading VM...</p>
      </div>
    );
  }

  if (!vm) {
    return (
      <div className="text-center py-20">
        <ExclamationTriangleIcon className="h-10 w-10 mx-auto mb-3 text-red-400" />
        <p className="text-gray-500 dark:text-gray-400">VM not found</p>
        <Link to="/vms" className="btn-primary mt-4 inline-flex">Back to list</Link>
      </div>
    );
  }

  const status = statusConfig[vm.status] ?? statusConfig['stopped']!;
  const isTransitioning = ['creating', 'starting', 'stopping', 'rebooting', 'deleting'].includes(vm.status);
  const isWindowsGuest = vm.os_profile === 'windows';
  const isCloudGuest = (vm.install_source ?? 'cloud') === 'cloud';
  const isIsoGuest = !isCloudGuest;

  return (
    <div className="max-w-6xl mx-auto">
      {/* Back + Header */}
      <div className="mb-6">
        <Link to="/vms" className="inline-flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 mb-4">
          <ArrowLeftIcon className="h-4 w-4" />
          Back to list
        </Link>

        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className={`p-3 rounded-xl ${status.bg}`}>
              <ComputerDesktopIcon className={`h-8 w-8 ${status.color}`} />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-gray-900 dark:text-white">{vm.name}</h1>
              <div className="flex items-center gap-3 mt-1">
                <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${status.bg} ${status.color}`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${status.dot}`} />
                  {status.label}
                </span>
                <span className="text-sm text-gray-500 dark:text-gray-400">{vm.hostname}</span>
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex flex-wrap gap-2">
            {vm.status === 'stopped' && (
              <ActionBtn onClick={() => handleAction('start')} disabled={!!actionLoading} color="green" icon={<PlayIcon className="h-4 w-4" />} label="Start" />
            )}
            {vm.status === 'running' && (
              <>
                <ActionBtn onClick={() => handleAction('stop')} disabled={!!actionLoading} color="yellow" icon={<StopIcon className="h-4 w-4" />} label="Stop" />
                <ActionBtn onClick={() => handleAction('reboot')} disabled={!!actionLoading} color="outline" icon={<ArrowPathIcon className="h-4 w-4" />} label="Reboot" />
                <ActionBtn onClick={() => setShowConsole(true)} color="primary" icon={<CommandLineIcon className="h-4 w-4" />} label="Console" />
              </>
            )}
            {(vm.status === 'running' || vm.status === 'error' || vm.status === 'stopping') && (
              <ActionBtn onClick={() => handleAction('force_stop')} disabled={!!actionLoading} color="redOutline" icon={<StopIcon className="h-4 w-4" />} label="Force Stop" />
            )}
            {isCloudGuest && (vm.status === 'running' || vm.status === 'stopped') && (
              <ActionBtn onClick={() => handleClone()} disabled={!!actionLoading} color="outline" icon={<DocumentDuplicateIcon className="h-4 w-4" />} label="Clone" />
            )}
            {vm.status === 'stopped' && (
              <ActionBtn onClick={() => handleExport()} disabled={!!actionLoading} color="outline" icon={<ArrowDownTrayIcon className="h-4 w-4" />} label={actionLoading === 'export' ? 'Exporting...' : 'Export'} />
            )}
            {isWindowsGuest && vm.status === 'stopped' && (
              <ActionBtn onClick={handleAttachVirtio} disabled={!!actionLoading} color="outline" icon={<ArrowUpTrayIcon className="h-4 w-4" />} label="Attach VirtIO ISO" />
            )}
            {isIsoGuest && vm.status === 'stopped' && (
              <ActionBtn onClick={handleEjectInstaller} disabled={!!actionLoading} color="outline" icon={<ArrowUturnLeftIcon className="h-4 w-4" />} label="Eject Installer ISO" />
            )}
            {(vm.status === 'stopped' || vm.status === 'error') && (
              <ActionBtn onClick={() => handleDelete(false)} disabled={!!actionLoading} color="red" icon={<TrashIcon className="h-4 w-4" />} label="Delete" />
            )}
            {isTransitioning && (
              <ActionBtn onClick={() => handleDelete(true)} disabled={!!actionLoading} color="redOutline" icon={<TrashIcon className="h-3.5 w-3.5" />} label="Force Delete" small />
            )}
          </div>
        </div>
      </div>

      {vm.description && (
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">{vm.description}</p>
      )}

      {/* Info Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-8">
        {/* Hardware */}
        <div className="card p-5">
          <div className="flex items-center gap-2 mb-4">
            <CpuChipIcon className="h-5 w-5 text-gray-400 dark:text-gray-300" />
            <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Hardware</h2>
          </div>
          <div className="space-y-3">
            <InfoRow label="vCPUs" value={`${vm.vcpus} cores`} />
            <InfoRow label="RAM" value={formatRam(vm.ram_mb)} />
            <InfoRow label="Disk" value={`${vm.disk_size_gb} GB (${vm.disk_format})`} />
            <InfoRow
              label="Storage"
              value={storagePool ? `${storagePool.name} (${storagePool.pool_type})` : 'Default'}
              title={storagePool?.path}
            />
          </div>
        </div>

        {/* Network */}
        <div className="card p-5">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <GlobeAltIcon className="h-5 w-5 text-gray-400 dark:text-gray-300" />
              <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Network</h2>
            </div>
            {vm.status === 'stopped' && currentTenant && (
              <button
                onClick={() => setShowChangeIP(!showChangeIP)}
                className="inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300"
              >
                <PencilIcon className="h-3.5 w-3.5" />
                Change IP
              </button>
            )}
          </div>
          <div className="space-y-3">
            {isSuperadmin && (
              <>
                <InfoRow label="MAC Address" value={vm.mac_address || '-'} mono />
                <InfoRow label="Bridge" value={vm.bridge} mono />
              </>
            )}
            {vm.cloud_init_network && <NetworkInfo config={vm.cloud_init_network} />}
          </div>
          {vm.status === 'running' && (
            <p className="mt-3 text-xs text-amber-500 dark:text-amber-400">VM must be stopped to change IP</p>
          )}
        </div>

        {/* System */}
        <div className="card p-5">
          <div className="flex items-center gap-2 mb-4">
            <ServerStackIcon className="h-5 w-5 text-gray-400 dark:text-gray-300" />
            <h2 className="text-sm font-semibold text-gray-900 dark:text-white">System</h2>
          </div>
          <div className="space-y-3">
            <InfoRow
              label={vm.os_profile === 'windows' ? 'OS' : 'Linux Distro'}
              value={formatVmDistro(vm)}
              title={vm.image_name || undefined}
            />
            {isSuperadmin && vm.vnc_port && (
              <InfoRow label="VNC Port" value={String(vm.vnc_port)} mono />
            )}
            <InfoRow label="Created" value={new Date(vm.created_at).toLocaleString('en-US')} />
            <InfoRow label="Updated" value={new Date(vm.updated_at).toLocaleString('en-US')} />
          </div>
        </div>
      </div>

      {/* Raw netplan only for superadmin (collapsed) — normal users see Network card summary only */}
      {isSuperadmin && isCloudGuest && vm.cloud_init_network && (
        <div className="card p-5 mb-8">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <SignalIcon className="h-5 w-5 text-gray-400 dark:text-gray-300" />
              <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Cloud-Init Network</h2>
              <span className="text-[10px] uppercase tracking-wide text-amber-600 dark:text-amber-400">Superadmin</span>
            </div>
            <button
              type="button"
              onClick={() => setShowNetplanRaw((v) => !v)}
              className="text-xs text-primary-600 hover:text-primary-700 dark:text-primary-400"
            >
              {showNetplanRaw ? 'Hide raw config' : 'Show raw config'}
            </button>
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">
            IP, gateway and DNS are shown in the Network card. Raw netplan is hidden by default.
          </p>
          {showNetplanRaw && (
            <pre className="text-xs font-mono bg-gray-50 dark:bg-gray-800/50 rounded-lg p-4 overflow-x-auto text-gray-700 dark:text-gray-300">
              {JSON.stringify(vm.cloud_init_network, null, 2)}
            </pre>
          )}
        </div>
      )}

      {primaryPoolType === 'nat' ? (
        <div className="card p-5 mb-8">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <ArrowsPointingOutIcon className="h-5 w-5 text-gray-400 dark:text-gray-300" />
              <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Port-Forwarding</h2>
              <span className="text-xs text-gray-400 dark:text-gray-300">({portForwards.length} rules)</span>
            </div>
            <button
              onClick={() => setShowPFForm(!showPFForm)}
              className="text-xs bg-primary-600 hover:bg-primary-700 text-white px-3 py-1.5 rounded-lg transition-colors"
            >
              + Add
            </button>
          </div>

          {showPFForm && (
            <form onSubmit={handleAddPF} className="mb-4 p-4 rounded-lg bg-gray-50 dark:bg-gray-800/50 space-y-3">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Protocol</label>
                  <select value={pfForm.protocol} onChange={(e) => setPfForm((p) => ({ ...p, protocol: e.target.value }))} className="input-field text-sm">
                    <option value="tcp">TCP</option>
                    <option value="udp">UDP</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Ext. Port (Host)</label>
                  <input type="number" min={1} max={65535} value={pfForm.external_port} onChange={(e) => setPfForm((p) => ({ ...p, external_port: e.target.value }))} required className="input-field text-sm" placeholder="8080" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Int. Port (VM)</label>
                  <input type="number" min={1} max={65535} value={pfForm.internal_port} onChange={(e) => setPfForm((p) => ({ ...p, internal_port: e.target.value }))} required className="input-field text-sm" placeholder="80" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Description</label>
                  <input type="text" value={pfForm.description} onChange={(e) => setPfForm((p) => ({ ...p, description: e.target.value }))} className="input-field text-sm" placeholder="HTTP" />
                </div>
              </div>
              <div className="flex gap-2 justify-end">
                <button type="button" onClick={() => setShowPFForm(false)} className="text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 px-3 py-1.5">Cancel</button>
                <button type="submit" disabled={pfLoading} className="text-xs bg-primary-600 hover:bg-primary-700 text-white px-4 py-1.5 rounded-lg disabled:opacity-50">
                  {pfLoading ? 'Creating...' : 'Create'}
                </button>
              </div>
            </form>
          )}

          {portForwards.length === 0 ? (
            <p className="text-xs text-gray-400 dark:text-gray-300 text-center py-3">
              No port forwarding rules. Add one to enable access to the VM.
            </p>
          ) : (
            <div className="space-y-2">
              {portForwards.map((pf) => (
                <div key={pf.id} className="flex items-center justify-between bg-gray-50 dark:bg-gray-800/50 rounded-lg px-4 py-2.5">
                  <div className="flex items-center gap-4">
                    <span className="text-xs font-mono font-semibold text-primary-600 dark:text-primary-400 bg-primary-50 dark:bg-primary-900/20 px-2 py-0.5 rounded">
                      {pf.protocol.toUpperCase()}
                    </span>
                    <div className="text-sm">
                      <span className="font-mono text-gray-900 dark:text-white">:{pf.external_port}</span>
                      <span className="text-gray-400 dark:text-gray-300 mx-2">→</span>
                      <span className="font-mono text-gray-600 dark:text-gray-400">{pf.internal_ip}:{pf.internal_port}</span>
                    </div>
                    {pf.description && (
                      <span className="text-xs text-gray-400 dark:text-gray-300">{pf.description}</span>
                    )}
                  </div>
                  <button onClick={() => handleDeletePF(pf.id)} className="text-gray-400 hover:text-red-500 dark:hover:text-red-400 transition-colors p-1">
                    <TrashIcon className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : primaryPoolType ? (
        <div className="card p-5 mb-8">
          <div className="flex items-center gap-2 mb-2">
            <ArrowsPointingOutIcon className="h-5 w-5 text-gray-400 dark:text-gray-300" />
            <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Port-Forwarding</h2>
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Port forwarding works only with NAT networks, not with public/external IPs.
            This VM is on a public network and is reachable directly via its public IP.
          </p>
        </div>
      ) : null}

      {/* Change IP (only when stopped) */}
      {showChangeIP && vm.status === 'stopped' && currentTenant && (
        <VMChangeIPPanel
          tenantId={currentTenant.id}
          vm={vm}
          onChanged={(updatedVm) => { setVm(updatedVm); setShowChangeIP(false); }}
          onCancel={() => setShowChangeIP(false)}
        />
      )}

      {/* VM Resize (only when stopped) */}
      {vm.status === 'stopped' && currentTenant && (
        <VMResizePanel
          tenantId={currentTenant.id}
          vm={vm}
          onResized={(updated) => { setVm(updated); toast.success('VM resources updated'); }}
        />
      )}

      {/* VM Password Reset — Linux cloud-init guests only */}
      {currentTenant &&
        !isWindowsGuest &&
        isCloudGuest &&
        vm.status !== 'creating' &&
        vm.status !== 'deleting' &&
        vm.status !== 'deleted' && (
        <VMPasswordResetPanel
          tenantId={currentTenant.id}
          vmId={vm.id}
          vmName={vm.name}
          vmStatus={vm.status}
        />
      )}

      {/* Secondary NICs */}
      {currentTenant && vm.status !== 'creating' && vm.status !== 'deleting' && (
        <VMSecondaryNICsPanel
          tenantId={currentTenant.id}
          vm={vm}
          onUpdated={loadVm}
        />
      )}

      {/* Per-VM Firewall */}
      {currentTenant && vm.status !== 'creating' && vm.status !== 'deleting' && (
        <VMFirewallPanel tenantId={currentTenant.id} vm={vm} />
      )}

      {/* VM Resource Usage */}
      {vm.status === 'running' && currentTenant && (
        <VMStatsPanel
          tenantId={currentTenant.id}
          vmId={vm.id}
          osProfile={vm.os_profile ?? 'linux'}
        />
      )}

      {/* Additional Disks */}
      {currentTenant && vm.status !== 'creating' && vm.status !== 'deleting' && (
        <VMDisksPanel tenantId={currentTenant.id} vmId={vm.id} vmName={vm.name} vmStatus={vm.status} />
      )}

      {/* Snapshots */}
      {currentTenant && vm.status !== 'creating' && vm.status !== 'deleting' && (
        <VMSnapshotsPanel tenantId={currentTenant.id} vmId={vm.id} vmName={vm.name} />
      )}

      {/* Backups */}
      {currentTenant && vm.status !== 'creating' && vm.status !== 'deleting' && (
        <VMBackupsPanel tenantId={currentTenant.id} vmId={vm.id} vmName={vm.name} vmStatus={vm.status} />
      )}

      {/* Console Modal (with crash protection) */}
      {showConsole && vm.status === 'running' && (
        <div className="fixed inset-0 z-[100] bg-black/70 backdrop-blur-sm p-3 md:p-6">
          <div className="h-full w-full max-w-7xl mx-auto">
            <ConsoleErrorBoundary onClose={() => setShowConsole(false)}>
              <EmbeddedConsole
                vmId={vm.id}
                vmName={vm.name}
                tenantId={currentTenant.id}
                onClose={() => setShowConsole(false)}
                modal
              />
            </ConsoleErrorBoundary>
          </div>
        </div>
      )}

      {/* Footer timestamps */}
      <div className="mt-8 flex items-center gap-4 text-xs text-gray-400 dark:text-gray-300 pb-4">
        <div className="flex items-center gap-1">
          <ClockIcon className="h-3.5 w-3.5" />
          Created: {new Date(vm.created_at).toLocaleString('en-US')}
        </div>
        <div>|</div>
        <div>Updated: {new Date(vm.updated_at).toLocaleString('en-US')}</div>
      </div>
    </div>
  );
}

/* ── Console Error Boundary ────────────────────────────────────── */

class ConsoleErrorBoundary extends Component<
  { children: ReactNode; onClose: () => void },
  { hasError: boolean; errorMsg: string }
> {
  constructor(props: { children: ReactNode; onClose: () => void }) {
    super(props);
    this.state = { hasError: false, errorMsg: '' };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, errorMsg: error.message || 'Unknown error' };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[Console Error]', error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="card overflow-hidden mb-8">
          <div className="flex items-center justify-between bg-gray-900 px-4 py-2">
            <span className="text-sm text-white font-medium">Console – Error</span>
            <button onClick={this.props.onClose} className="text-gray-400 hover:text-white transition-colors p-1">
              <XMarkIcon className="h-5 w-5" />
            </button>
          </div>
          <div className="bg-black p-8 text-center" style={{ height: '300px' }}>
            <ExclamationTriangleIcon className="h-12 w-12 text-red-500 mx-auto mb-3" />
            <p className="text-sm text-red-400 font-medium mb-2">Console error</p>
            <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">{this.state.errorMsg}</p>
            <button onClick={this.props.onClose} className="text-xs bg-gray-700 hover:bg-gray-600 text-white px-4 py-2 rounded transition-colors">
              Close
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

/* ── Embedded VNC Console ──────────────────────────────────────── */

type ConsoleState = 'connecting' | 'connected' | 'disconnected' | 'error';

function EmbeddedConsole({
  vmId,
  vmName,
  tenantId,
  onClose,
  modal = false,
}: {
  vmId: string;
  vmName: string;
  tenantId: string;
  onClose: () => void;
  modal?: boolean;
}) {
  // IMPORTANT: We use two separate refs to prevent React/noVNC DOM conflicts.
  // - wrapperRef: the outer container (React manages this)
  // - vncTargetRef: a raw DOM element created imperatively for noVNC (React never touches this)
  const wrapperRef = useRef<HTMLDivElement>(null);
  const vncTargetRef = useRef<HTMLDivElement | null>(null);
  const rfbRef = useRef<unknown>(null);
  const [state, setState] = useState<ConsoleState>('connecting');
  const [error, setError] = useState<string | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isConsoleCaptured, setIsConsoleCaptured] = useState(false);

  // Create the VNC target div imperatively (once) on mount
  useEffect(() => {
    if (wrapperRef.current && !vncTargetRef.current) {
      const el = document.createElement('div');
      el.style.width = '100%';
      el.style.height = '100%';
      wrapperRef.current.appendChild(el);
      vncTargetRef.current = el;
    }
    return () => {
      // Cleanup: remove the VNC target from DOM
      if (vncTargetRef.current && vncTargetRef.current.parentNode) {
        try { vncTargetRef.current.parentNode.removeChild(vncTargetRef.current); } catch { /* ignore */ }
      }
      vncTargetRef.current = null;
    };
  }, []);

  const connectVNC = useCallback(async () => {
    const target = vncTargetRef.current;
    if (!target) return;

    setState('connecting');
    setError(null);

    let websocketPath: string;
    try {
      const consoleData = await vmApi.getConsoleToken(tenantId, vmId);
      websocketPath = consoleData.websocket_url || `/websockify?token=${consoleData.token}`;
    } catch (err: unknown) {
      setState('error');
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Could not retrieve console token. VM must be running.';
      setError(msg);
      return;
    }

    try {
      const { default: RFB } = await import('@novnc/novnc/lib/rfb');

      // Disconnect previous
      if (rfbRef.current) {
        try { (rfbRef.current as { disconnect: () => void }).disconnect(); } catch { /* ignore */ }
        rfbRef.current = null;
      }

      // Clear only the VNC target (not React-managed nodes)
      while (target.firstChild) {
        target.removeChild(target.firstChild);
      }

      const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = websocketPath.startsWith('ws://') || websocketPath.startsWith('wss://')
        ? websocketPath
        : `${wsProtocol}//${window.location.host}${websocketPath.startsWith('/') ? '' : '/'}${websocketPath}`;

      const rfb = new RFB(target, wsUrl, {
        wsProtocols: ['binary'],
      });

      rfb.scaleViewport = true;
      rfb.resizeSession = true;
      rfb.focusOnClick = true;

      rfb.addEventListener('connect', () => setState('connected'));
      rfb.addEventListener('disconnect', (e: CustomEvent) => {
        setState('disconnected');
        setIsConsoleCaptured(false);
        if (!e.detail?.clean) setError('Connection to VNC server lost');
        rfbRef.current = null;
      });
      rfb.addEventListener('credentialsrequired', () => {
        try { rfb.sendCredentials({ password: '' }); } catch { /* ignore */ }
      });
      rfb.addEventListener('securityfailure', (e: CustomEvent) => {
        setState('error');
        setIsConsoleCaptured(false);
        setError(`Security error: ${e.detail?.reason ?? 'Unknown'}`);
      });

      rfbRef.current = rfb;
    } catch (err: unknown) {
      setState('error');
      setError(`Could not load noVNC: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, [tenantId, vmId]);

  useEffect(() => {
    const timer = setTimeout(connectVNC, 200);
    return () => {
      clearTimeout(timer);
      if (rfbRef.current) {
        try { (rfbRef.current as { disconnect: () => void }).disconnect(); } catch { /* ignore */ }
        rfbRef.current = null;
      }
    };
  }, [connectVNC]);

  const handleFullscreen = () => {
    const el = document.getElementById('embedded-console');
    if (!el) return;
    if (!document.fullscreenElement) {
      el.requestFullscreen().then(() => setIsFullscreen(true)).catch(() => {});
    } else {
      document.exitFullscreen().then(() => setIsFullscreen(false)).catch(() => {});
    }
  };

  const handleCtrlAltDel = () => {
    try {
      if (rfbRef.current && typeof (rfbRef.current as { sendCtrlAltDel?: () => void }).sendCtrlAltDel === 'function') {
        (rfbRef.current as { sendCtrlAltDel: () => void }).sendCtrlAltDel();
      }
    } catch { /* ignore */ }
  };

  const stateColors: Record<ConsoleState, string> = {
    connecting: 'text-yellow-400',
    connected: 'text-green-400',
    disconnected: 'text-gray-400',
    error: 'text-red-400',
  };

  const stateLabels: Record<ConsoleState, string> = {
    connecting: 'Connecting...',
    connected: 'Connected',
    disconnected: 'Disconnected',
    error: 'Error',
  };

  const handleCaptureConsole = () => {
    setIsConsoleCaptured(true);
    try {
      if (rfbRef.current && typeof (rfbRef.current as { focus?: () => void }).focus === 'function') {
        (rfbRef.current as { focus: () => void }).focus();
      }
    } catch {
      // ignore
    }
  };

  const handleReleaseConsole = () => {
    setIsConsoleCaptured(false);
    try {
      if (rfbRef.current && typeof (rfbRef.current as { blur?: () => void }).blur === 'function') {
        (rfbRef.current as { blur: () => void }).blur();
      }
    } catch {
      // ignore
    }
  };

  return (
    <div id="embedded-console" className={`card overflow-hidden ${modal ? 'h-full flex flex-col' : 'mb-8'}`}>
      {/* Toolbar */}
      <div className="flex items-center justify-between bg-gray-900 px-4 py-2">
        <div className="flex items-center gap-3">
          <ComputerDesktopIcon className="h-5 w-5 text-primary-400" />
          <span className="text-sm text-white font-medium">{vmName} – Console</span>
          <span className={`flex items-center gap-1 text-xs ${stateColors[state]}`}>
            {state === 'connected' ? <SignalIcon className="h-3.5 w-3.5" /> : <SignalSlashIcon className="h-3.5 w-3.5" />}
            {state === 'connecting' ? <span className="animate-pulse">{stateLabels[state]}</span> : stateLabels[state]}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {state === 'connected' && (
            <button onClick={handleCtrlAltDel} className="text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-white px-2 py-1 rounded transition-colors">
              Ctrl+Alt+Del
            </button>
          )}
          {(state === 'disconnected' || state === 'error') && (
            <button onClick={() => connectVNC()} className="text-xs bg-primary-600 hover:bg-primary-700 text-white px-3 py-1 rounded transition-colors">
              Reconnect
            </button>
          )}
          <button onClick={handleFullscreen} className="text-gray-400 hover:text-white transition-colors p-1" title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}>
            {isFullscreen ? <ArrowsPointingInIcon className="h-5 w-5" /> : <ArrowsPointingOutIcon className="h-5 w-5" />}
          </button>
          <button onClick={onClose} className="text-gray-400 hover:text-white transition-colors p-1" title="Close console">
            <XMarkIcon className="h-5 w-5" />
          </button>
        </div>
      </div>

      {/* VNC Canvas Area: wrapper is position:relative so overlays sit on top of the raw VNC div */}
      <div
        className={`bg-black relative ${modal ? 'flex-1 min-h-[520px]' : ''}`}
        style={{ height: modal ? undefined : '500px' }}
        onMouseLeave={handleReleaseConsole}
      >
        {/* This div is exclusively for noVNC – React never renders children here */}
        <div ref={wrapperRef} className="absolute inset-0" />

        {/* State overlays (React-managed, sits on top of the VNC canvas) */}
        {state === 'connected' && !isConsoleCaptured && (
          <div
            className="absolute inset-0 z-20 cursor-default"
            onClick={handleCaptureConsole}
            title="Click to control console"
          >
            <div className="absolute bottom-3 right-3 rounded bg-black/60 px-3 py-1.5 text-xs text-gray-200">
              Click to control console
            </div>
          </div>
        )}
        {state === 'connecting' && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/80 z-10">
            <div className="text-center">
              <svg className="animate-spin h-8 w-8 text-primary-500 mx-auto mb-3" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              <p className="text-sm text-gray-400 dark:text-gray-300">Connecting to VNC...</p>
            </div>
          </div>
        )}
        {state === 'error' && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/80 z-10">
            <div className="text-center max-w-md">
              <SignalSlashIcon className="h-12 w-12 text-red-500 mx-auto mb-3" />
              <p className="text-sm text-red-400 font-medium mb-2">Connection error</p>
              <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">{error}</p>
              <div className="flex gap-2 justify-center">
                <button onClick={() => connectVNC()} className="text-xs bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded transition-colors">
                  Retry connection
                </button>
                <button onClick={onClose} className="text-xs bg-gray-700 hover:bg-gray-600 text-white px-4 py-2 rounded transition-colors">
                  Close
                </button>
              </div>
            </div>
          </div>
        )}
        {state === 'disconnected' && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/80 z-10">
            <div className="text-center">
              <ComputerDesktopIcon className="h-12 w-12 text-gray-600 dark:text-gray-400 mx-auto mb-3" />
              <p className="text-sm text-gray-400 dark:text-gray-300 mb-4">Connection disconnected</p>
              <div className="flex gap-2 justify-center">
                <button onClick={() => connectVNC()} className="text-xs bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded transition-colors">
                  Reconnect
                </button>
                <button onClick={onClose} className="text-xs bg-gray-700 hover:bg-gray-600 text-white px-4 py-2 rounded transition-colors">
                  Close
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Helper Components ─────────────────────────────────────────── */

const btnColors = {
  green:      'bg-green-600 text-white hover:bg-green-700',
  yellow:     'bg-yellow-600 text-white hover:bg-yellow-700',
  red:        'bg-red-600 text-white hover:bg-red-700',
  primary:    'bg-primary-600 text-white hover:bg-primary-700',
  outline:    'border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700',
  redOutline: 'border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30',
};

function ActionBtn({
  onClick,
  disabled,
  color,
  icon,
  label,
  small,
}: {
  onClick: () => void;
  disabled?: boolean;
  color: keyof typeof btnColors;
  icon: React.ReactNode;
  label: string;
  small?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-1.5 rounded-lg ${small ? 'px-3 py-2 text-xs' : 'px-4 py-2 text-sm'} font-medium transition-colors disabled:opacity-50 ${btnColors[color]}`}
    >
      {icon}
      {label}
    </button>
  );
}

function InfoRow({ label, value, mono, title }: { label: string; value: string; mono?: boolean; title?: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-gray-500 dark:text-gray-400">{label}</span>
      <span className={`text-sm font-medium text-gray-900 dark:text-white ${mono ? 'font-mono text-xs' : ''}`} title={title}>
        {value}
      </span>
    </div>
  );
}

function NetworkInfo({ config }: { config: Record<string, unknown> }) {
  const ethernets = config.ethernets as Record<string, Record<string, unknown>> | undefined;
  if (!ethernets) return null;

  return (
    <>
      {Object.entries(ethernets).map(([iface, cfg]) => (
        <div key={iface}>
          {cfg.dhcp4 === true ? (
            <InfoRow label="IP Mode" value="DHCP" />
          ) : (
            <>
              {Array.isArray(cfg.addresses) && cfg.addresses.length > 0 && (
                <InfoRow label="IP" value={String(cfg.addresses[0])} mono />
              )}
              {cfg.gateway4 && <InfoRow label="Gateway" value={String(cfg.gateway4)} mono />}
              {cfg.nameservers && typeof cfg.nameservers === 'object' && (
                <InfoRow
                  label="DNS"
                  value={
                    Array.isArray((cfg.nameservers as Record<string, unknown>).addresses)
                      ? ((cfg.nameservers as Record<string, unknown>).addresses as string[]).join(', ')
                      : '-'
                  }
                  mono
                />
              )}
            </>
          )}
        </div>
      ))}
    </>
  );
}

function formatVmDistro(vm: VirtualMachine): string {
  // Prefer cloud-image name (e.g. "Ubuntu 24.04 LTS") — os_type is often just "linux".
  if (vm.image_name?.trim()) return vm.image_name.trim();
  const type = (vm.os_type || '').trim();
  const version = (vm.os_version || '').trim();
  if (type && version && !['linux', 'windows', 'other'].includes(type.toLowerCase())) {
    const pretty = type.charAt(0).toUpperCase() + type.slice(1);
    return `${pretty} ${version}`;
  }
  if (type && version) return `${type.charAt(0).toUpperCase() + type.slice(1)} ${version}`;
  if (vm.os_profile === 'windows') return 'Windows';
  if (vm.os_profile === 'other') return 'Other';
  return '—';
}

function formatRam(mb: number): string {
  if (mb >= 1024) {
    const gb = mb / 1024;
    return gb % 1 === 0 ? `${gb} GB` : `${gb.toFixed(1)} GB`;
  }
  return `${mb} MB`;
}

function formatBytes(bytes: number): string {
  if (bytes >= 1073741824) return `${(bytes / 1073741824).toFixed(1)} GB`;
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

/* ── VM Secondary NICs Panel ──────────────────────────────────── */

function VMSecondaryNICsPanel({
  tenantId,
  vm,
  onUpdated,
}: {
  tenantId: string;
  vm: VirtualMachine;
  onUpdated: () => void;
}) {
  const isSuperadmin = useAuthStore((s) => s.user?.is_superadmin === true);
  const [internalPools, setInternalPools] = useState<IPPool[]>([]);
  const [selectedPool, setSelectedPool] = useState('');
  const [isAdding, setIsAdding] = useState(false);
  const [isRemoving, setIsRemoving] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  useEffect(() => {
    ipamApi.listPools(vm?.tenant_id ? { tenant_id: vm.tenant_id } : undefined).then((pools) => {
      setInternalPools(pools.filter((p) => p.pool_type === 'internal' && p.is_active));
    }).catch(() => {});
  }, [vm?.tenant_id]);

  const secondaryNics = vm.secondary_interfaces || [];
  const isStopped = vm.status === 'stopped';

  const handleAdd = async () => {
    if (!selectedPool) return;
    setIsAdding(true);
    try {
      await vmApi.addSecondaryNIC(tenantId, vm.id, selectedPool);
      toast.success('Network interface added');
      setSelectedPool('');
      setShowForm(false);
      onUpdated();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    } finally {
      setIsAdding(false);
    }
  };

  const handleRemove = async (macAddress: string) => {
    if (!window.confirm('Really remove network interface?')) return;
    setIsRemoving(macAddress);
    try {
      await vmApi.removeSecondaryNIC(tenantId, vm.id, macAddress);
      toast.success('Network interface removed');
      onUpdated();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    } finally {
      setIsRemoving(null);
    }
  };

  // Filter out pools the VM is already connected to
  const availablePools = internalPools.filter(
    (p) => !secondaryNics.some((n) => n.pool_id === p.id)
  );

  return (
    <div className="card p-5 mb-8">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <GlobeAltIcon className="h-5 w-5 text-gray-400 dark:text-gray-300" />
          <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Network Interfaces</h2>
          <span className="text-xs bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 px-2 py-0.5 rounded-full">
            {1 + secondaryNics.length}
          </span>
        </div>
        {isStopped && availablePools.length > 0 && (
          <button
            onClick={() => setShowForm(!showForm)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-700"
          >
            + Add interface
          </button>
        )}
      </div>

      {/* Primary NIC (always shown) */}
      <div className="space-y-2 mb-3">
        <div className="flex items-center justify-between p-3 rounded-lg border border-primary-200 dark:border-primary-800 bg-primary-50/50 dark:bg-primary-900/10">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-primary-700 dark:text-primary-400 bg-primary-100 dark:bg-primary-900/30 px-2 py-0.5 rounded">
                eth0 – Primary
              </span>
              {isSuperadmin && (
                <span className="text-xs text-gray-500 dark:text-gray-400">{vm.bridge}</span>
              )}
            </div>
            <div className="flex items-center gap-3 mt-1.5">
              {isSuperadmin && (
                <span className="text-xs font-mono text-gray-600 dark:text-gray-400">{vm.mac_address}</span>
              )}
              {vm.cloud_init_network && (() => {
                const ethernets = vm.cloud_init_network?.ethernets as Record<string, Record<string, unknown>> | undefined;
                if (!ethernets) return null;
                const firstCfg = Object.values(ethernets)[0];
                if (!firstCfg || !Array.isArray(firstCfg.addresses)) return null;
                return <span className="text-xs font-mono text-gray-900 dark:text-white">{String(firstCfg.addresses[0])}</span>;
              })()}
            </div>
          </div>
          <span className="text-xs text-gray-400 dark:text-gray-300">
            {(() => {
              const kind = resolveVmNetworkKind(vm);
              if (kind === 'nat') return 'NAT / Port-Forwarding';
              if (kind === 'internal') return 'Internal';
              if (kind === 'external') return 'Public / direct IP';
              return 'Network';
            })()}
          </span>
        </div>
      </div>

      {/* Secondary NICs */}
      {secondaryNics.length > 0 && (
        <div className="space-y-2 mb-3">
          {secondaryNics.map((nic, idx) => (
            <div key={nic.mac_address} className="flex items-center justify-between p-3 rounded-lg border border-gray-200 dark:border-gray-700">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-gray-600 dark:text-gray-400 bg-gray-100 dark:bg-gray-800 px-2 py-0.5 rounded">
                    eth{idx + 1} – {nic.pool_name}
                  </span>
                  <span className="text-xs text-gray-400 dark:text-gray-300">Internal</span>
                </div>
                <div className="flex items-center gap-3 mt-1.5">
                  {isSuperadmin && (
                    <span className="text-xs font-mono text-gray-600 dark:text-gray-400">{nic.mac_address}</span>
                  )}
                  <span className="text-xs text-gray-500 dark:text-gray-400">{nic.subnet}</span>
                  <span className={`text-xs ${nic.dhcp_enabled === false ? "text-orange-600 dark:text-orange-400" : "text-green-600 dark:text-green-400"}`}>
                    {nic.dhcp_enabled === false ? "No DHCP" : "DHCP"}
                  </span>
                </div>
              </div>
              {isStopped && (
                <button
                  onClick={() => handleRemove(nic.mac_address)}
                  disabled={!!isRemoving}
                  className="text-xs text-red-600 hover:text-red-800 dark:text-red-400 px-2 py-1 rounded hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                  title="Remove interface"
                >
                  {isRemoving === nic.mac_address ? '...' : <TrashIcon className="h-3.5 w-3.5" />}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Add Form */}
      {showForm && (
        <div className="bg-gray-50 dark:bg-gray-800/50 rounded-lg p-4 space-y-3">
          <div>
            <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
              Select internal network
            </label>
            <select
              value={selectedPool}
              onChange={(e) => setSelectedPool(e.target.value)}
              className="input w-full"
            >
              <option value="">— Select network —</option>
              {availablePools.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.network})
                </option>
              ))}
            </select>
            <p className="mt-1 text-xs text-gray-400 dark:text-gray-300">
              If DHCP is enabled on the selected internal network, the VM receives an IP automatically.
              With DHCP disabled, configure static addressing inside the guest.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleAdd}
              disabled={!selectedPool || isAdding}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-2 text-xs font-medium text-white hover:bg-primary-700 disabled:opacity-50"
            >
              {isAdding ? 'Adding...' : 'Add'}
            </button>
            <button
              onClick={() => setShowForm(false)}
              className="text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 px-3 py-2"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Hint when no internal networks exist */}
      {internalPools.length === 0 && secondaryNics.length === 0 && (
        <p className="text-xs text-gray-400 dark:text-gray-300 text-center py-2">
          No internal networks configured. Create an internal network under Networks
          to enable VM-to-VM communication on a separate subnet.
        </p>
      )}

      {!isStopped && secondaryNics.length === 0 && internalPools.length > 0 && (
        <p className="text-xs text-gray-400 dark:text-gray-300 text-center py-2">
          Stop the VM to add an internal network interface.
        </p>
      )}
    </div>
  );
}

function VMFirewallPanel({
  tenantId,
  vm,
}: {
  tenantId: string;
  vm: VirtualMachine;
}) {
  const [profile, setProfile] = useState<VMFirewallProfile | null>(null);
  const [vmIp, setVmIp] = useState<string>('');
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [newInPort, setNewInPort] = useState('');
  const [newOutPort, setNewOutPort] = useState('');
  const [fwLog, setFwLog] = useState<string[]>([]);
  const [fwLogLoading, setFwLogLoading] = useState(false);
  const [showFwLog, setShowFwLog] = useState(false);

  const extractDetail = (err: unknown, fallback: string) => {
    return (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || fallback;
  };

  const loadProfile = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await vmApi.getFirewallProfile(tenantId, vm.id);
      setProfile(data.profile);
      setVmIp(data.vm_ip || '');
    } catch (err: unknown) {
      toast.error(extractDetail(err, 'Firewall profile could not be loaded'));
    } finally {
      setIsLoading(false);
    }
  }, [tenantId, vm.id]);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  const loadFirewallLog = useCallback(async () => {
    setFwLogLoading(true);
    try {
      const data = await vmApi.getFirewallLog(tenantId, vm.id, 80);
      setFwLog(data.blocked_entries || []);
    } catch (err: unknown) {
      toast.error(extractDetail(err, 'Firewall log could not be loaded'));
    } finally {
      setFwLogLoading(false);
    }
  }, [tenantId, vm.id]);

  const saveProfile = async (next: VMFirewallProfile) => {
    setIsSaving(true);
    try {
      await vmApi.updateFirewallProfile(tenantId, vm.id, {
        bridge: vm.bridge,
        inbound: next.inbound,
        outbound: next.outbound,
      });
      setProfile(next);
      toast.success('VM firewall updated');
    } catch (err: unknown) {
      toast.error(extractDetail(err, 'Update failed'));
    } finally {
      setIsSaving(false);
    }
  };

  const toggleInbound = async () => {
    if (!profile) return;
    await saveProfile({ ...profile, inbound: { ...profile.inbound, enabled: !profile.inbound.enabled } });
  };

  const toggleOutbound = async () => {
    if (!profile) return;
    await saveProfile({ ...profile, outbound: { ...profile.outbound, enabled: !profile.outbound.enabled } });
  };

  const allowAllTest = async () => {
    if (!profile) return;
    if (!window.confirm('Enable test mode for this VM (allow all inbound + outbound)?')) return;
    await saveProfile({
      ...profile,
      inbound: { ...profile.inbound, enabled: false },
      outbound: { ...profile.outbound, enabled: false },
    });
  };

  const addInboundPort = async () => {
    if (!profile) return;
    const port = Number(newInPort);
    if (!port || port < 1 || port > 65535) {
      toast.error('Invalid inbound port');
      return;
    }
    if (profile.inbound.allowed_ports.some((p) => p.port === port && p.protocol === 'tcp')) {
      toast.error('Inbound port already exists');
      return;
    }
    const next = {
      ...profile,
      inbound: {
        ...profile.inbound,
        allowed_ports: [...profile.inbound.allowed_ports, { port, protocol: 'tcp', description: '' }],
      },
    };
    setNewInPort('');
    await saveProfile(next);
  };

  const removeInboundPort = async (port: number) => {
    if (!profile) return;
    const next = {
      ...profile,
      inbound: {
        ...profile.inbound,
        allowed_ports: profile.inbound.allowed_ports.filter((p) => p.port !== port),
      },
    };
    await saveProfile(next);
  };

  const addOutboundPort = async () => {
    if (!profile) return;
    const port = Number(newOutPort);
    if (!port || port < 1 || port > 65535) {
      toast.error('Invalid outbound port');
      return;
    }
    if (profile.outbound.allowed_ports.some((p) => p.port === port && p.protocol === 'tcp')) {
      toast.error('Outbound port already exists');
      return;
    }
    const next = {
      ...profile,
      outbound: {
        ...profile.outbound,
        allowed_ports: [...profile.outbound.allowed_ports, { port, protocol: 'tcp', description: '' }],
      },
    };
    setNewOutPort('');
    await saveProfile(next);
  };

  const removeOutboundPort = async (port: number) => {
    if (!profile) return;
    const next = {
      ...profile,
      outbound: {
        ...profile.outbound,
        allowed_ports: profile.outbound.allowed_ports.filter((p) => p.port !== port),
      },
    };
    await saveProfile(next);
  };

  if (isLoading) {
    return <div className="card p-5 mb-8 text-sm text-gray-500 dark:text-gray-400">Loading VM firewall...</div>;
  }

  if (!profile) return null;

  const inboundFiltered = profile.inbound.enabled;
  const outboundFiltered = profile.outbound.enabled;
  const testModeOpen = !inboundFiltered && !outboundFiltered;

  return (
    <div className="card p-5 mb-8">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="flex items-center gap-2">
          <ShieldCheckIcon className="h-5 w-5 text-gray-400 dark:text-gray-300" />
          <h2 className="text-sm font-semibold text-gray-900 dark:text-white">VM Firewall (per VM)</h2>
          <span className="text-xs text-gray-500 dark:text-gray-400 font-mono">{vmIp || 'no-ip'}</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setShowFwLog((v) => !v);
              if (!showFwLog) loadFirewallLog();
            }}
            className="text-xs px-3 py-1.5 rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800"
          >
            {showFwLog ? 'Hide log' : 'Show blocked log'}
          </button>
          <button
            type="button"
            onClick={allowAllTest}
            disabled={isSaving}
            className={`text-xs px-3 py-1.5 rounded-lg font-medium border transition-colors ${
              testModeOpen
                ? 'bg-amber-500 border-amber-500 text-white shadow-sm'
                : 'bg-transparent border-amber-400 text-amber-700 dark:text-amber-300 hover:bg-amber-50 dark:hover:bg-amber-900/20'
            }`}
          >
            {testModeOpen ? 'Test mode: all open' : 'Allow all (test)'}
          </button>
        </div>
      </div>

      <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
        <span className="inline-flex items-center gap-1 mr-3">
          <span className="w-2 h-2 rounded-full bg-emerald-500" /> Green = filter active (only listed ports)
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-amber-400" /> Amber = open (no per-VM filter)
        </span>
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div
          className={`rounded-lg border-2 p-3 transition-colors ${
            inboundFiltered
              ? 'border-emerald-500/60 bg-emerald-50/50 dark:bg-emerald-900/10'
              : 'border-amber-400/60 bg-amber-50/40 dark:bg-amber-900/10'
          }`}
        >
          <div className="flex items-center justify-between mb-3">
            <div>
              <p className="text-sm font-medium text-gray-900 dark:text-white">Inbound</p>
              <p className="text-xs mt-0.5 flex items-center gap-1">
                {inboundFiltered ? (
                  <>
                    <CheckCircleIcon className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                    <span className="text-emerald-700 dark:text-emerald-300 font-medium">Filter active</span>
                  </>
                ) : (
                  <>
                    <XCircleIcon className="h-4 w-4 text-amber-600 dark:text-amber-400" />
                    <span className="text-amber-700 dark:text-amber-300 font-medium">Open — all ports allowed</span>
                  </>
                )}
              </p>
            </div>
            <button
              type="button"
              onClick={toggleInbound}
              disabled={isSaving}
              className={`text-xs px-3 py-1.5 rounded-lg font-medium transition-colors ${
                inboundFiltered
                  ? 'bg-red-100 text-red-700 hover:bg-red-200 dark:bg-red-900/40 dark:text-red-300'
                  : 'bg-emerald-600 text-white hover:bg-emerald-700'
              }`}
            >
              {inboundFiltered ? 'Open (test)' : 'Enable filter'}
            </button>
          </div>
          <div className={`flex gap-2 mb-2 ${!inboundFiltered ? 'opacity-50 pointer-events-none' : ''}`}>
            <input value={newInPort} onChange={(e) => setNewInPort(e.target.value)} placeholder="22" className="input-field text-xs" disabled={!inboundFiltered} />
            <button type="button" onClick={addInboundPort} disabled={isSaving || !inboundFiltered} className="text-xs bg-primary-600 hover:bg-primary-700 text-white px-3 py-1.5 rounded-lg">+ Add</button>
          </div>
          <div className="flex flex-wrap gap-2">
            {profile.inbound.allowed_ports.map((p) => (
              <button
                key={`in-${p.port}`}
                type="button"
                onClick={() => removeInboundPort(p.port)}
                disabled={!inboundFiltered || isSaving}
                className="text-xs px-2 py-1 rounded bg-white/80 dark:bg-gray-800 border border-gray-200 dark:border-gray-600"
              >
                {p.port} ✕
              </button>
            ))}
            {inboundFiltered && profile.inbound.allowed_ports.length === 0 && (
              <span className="text-xs text-red-600 dark:text-red-400">No ports — all inbound blocked</span>
            )}
          </div>
        </div>

        <div
          className={`rounded-lg border-2 p-3 transition-colors ${
            outboundFiltered
              ? 'border-emerald-500/60 bg-emerald-50/50 dark:bg-emerald-900/10'
              : 'border-amber-400/60 bg-amber-50/40 dark:bg-amber-900/10'
          }`}
        >
          <div className="flex items-center justify-between mb-3">
            <div>
              <p className="text-sm font-medium text-gray-900 dark:text-white">Outbound</p>
              <p className="text-xs mt-0.5 flex items-center gap-1">
                {outboundFiltered ? (
                  <>
                    <CheckCircleIcon className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                    <span className="text-emerald-700 dark:text-emerald-300 font-medium">Filter active — listed ports only</span>
                  </>
                ) : (
                  <>
                    <XCircleIcon className="h-4 w-4 text-amber-600 dark:text-amber-400" />
                    <span className="text-amber-700 dark:text-amber-300 font-medium">Open — global egress rules apply</span>
                  </>
                )}
              </p>
            </div>
            <button
              type="button"
              onClick={toggleOutbound}
              disabled={isSaving}
              className={`text-xs px-3 py-1.5 rounded-lg font-medium transition-colors ${
                outboundFiltered
                  ? 'bg-red-100 text-red-700 hover:bg-red-200 dark:bg-red-900/40 dark:text-red-300'
                  : 'bg-emerald-600 text-white hover:bg-emerald-700'
              }`}
            >
              {outboundFiltered ? 'Open (default)' : 'Enable filter'}
            </button>
          </div>
          <div className={`flex gap-2 mb-2 ${!outboundFiltered ? 'opacity-50 pointer-events-none' : ''}`}>
            <input value={newOutPort} onChange={(e) => setNewOutPort(e.target.value)} placeholder="443" className="input-field text-xs" disabled={!outboundFiltered} />
            <button type="button" onClick={addOutboundPort} disabled={isSaving || !outboundFiltered} className="text-xs bg-primary-600 hover:bg-primary-700 text-white px-3 py-1.5 rounded-lg">+ Add</button>
          </div>
          <div className="flex flex-wrap gap-2">
            {profile.outbound.allowed_ports.map((p) => (
              <button
                key={`out-${p.port}`}
                type="button"
                onClick={() => removeOutboundPort(p.port)}
                disabled={!outboundFiltered || isSaving}
                className="text-xs px-2 py-1 rounded bg-white/80 dark:bg-gray-800 border border-gray-200 dark:border-gray-600"
              >
                {p.port} ✕
              </button>
            ))}
            {outboundFiltered && profile.outbound.allowed_ports.length === 0 && (
              <span className="text-xs text-red-600 dark:text-red-400">No ports — all outbound blocked</span>
            )}
          </div>
        </div>
      </div>

      {showFwLog && (
        <div className="mt-4 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50 p-3">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-medium text-gray-700 dark:text-gray-300">
              Blocked traffic (kernel log, per this VM)
            </p>
            <button
              type="button"
              onClick={loadFirewallLog}
              disabled={fwLogLoading}
              className="text-xs text-primary-600 dark:text-primary-400 flex items-center gap-1"
            >
              <ArrowPathIcon className={`h-3.5 w-3.5 ${fwLogLoading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">
            Entries appear when traffic is blocked by this VM&apos;s filter. On the host:{' '}
            <code className="font-mono">journalctl -k | grep QFW-</code>
          </p>
          <div className="max-h-48 overflow-y-auto font-mono text-xs bg-black/90 text-green-400 rounded p-2">
            {fwLog.length === 0 ? (
              <span className="text-gray-500">No blocked entries yet (or log empty).</span>
            ) : (
              fwLog.map((line, i) => <div key={i} className="whitespace-pre-wrap break-all">{line}</div>)
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ── VM Resize Panel ──────────────────────────────────────────── */

function VMResizePanel({
  tenantId,
  vm,
  onResized,
}: {
  tenantId: string;
  vm: VirtualMachine;
  onResized: (updated: VirtualMachine) => void;
}) {
  const currentTenant = useTenantStore((s) => s.currentTenant);
  const [vcpus, setVcpus] = useState(vm.vcpus);
  const [ramMb, setRamMb] = useState(vm.ram_mb);
  const [diskGb, setDiskGb] = useState(vm.disk_size_gb);
  const [isSaving, setIsSaving] = useState(false);
  const [othersRamMb, setOthersRamMb] = useState(0);
  const [othersVcpus, setOthersVcpus] = useState(0);
  const [othersDiskGb, setOthersDiskGb] = useState(0);
  const [quotaLoaded, setQuotaLoaded] = useState(false);

  useEffect(() => {
    setVcpus(vm.vcpus);
    setRamMb(vm.ram_mb);
    setDiskGb(vm.disk_size_gb);
  }, [vm.vcpus, vm.ram_mb, vm.disk_size_gb]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await vmApi.list(tenantId, { page_size: 100 });
        if (cancelled) return;
        const others = data.items.filter((item) => item.id !== vm.id);
        setOthersRamMb(others.reduce((sum, item) => sum + item.ram_mb, 0));
        setOthersVcpus(others.reduce((sum, item) => sum + item.vcpus, 0));
        setOthersDiskGb(others.reduce((sum, item) => sum + item.disk_size_gb, 0));
      } catch {
        if (!cancelled) toast.error('Tenant quota could not be loaded');
      } finally {
        if (!cancelled) setQuotaLoaded(true);
      }
    })();
    return () => { cancelled = true; };
  }, [tenantId, vm.id]);

  const maxRamMb = currentTenant?.max_ram_mb ?? 524288;
  const maxVcpus = currentTenant?.max_vcpus ?? 128;
  const maxStorageGb = currentTenant?.max_storage_gb ?? 10000;

  // Headroom for this VM = tenant quota minus what other VMs already use
  const ramAvailableMb = Math.max(0, maxRamMb - othersRamMb);
  const vcpuAvailable = Math.max(0, maxVcpus - othersVcpus);
  const diskAvailableGb = Math.max(0, maxStorageGb - othersDiskGb);

  const tenantRamUsedMb = othersRamMb + vm.ram_mb;
  const tenantRamRemainingMb = Math.max(0, maxRamMb - tenantRamUsedMb);

  const hasChanges = vcpus !== vm.vcpus || ramMb !== vm.ram_mb || diskGb !== vm.disk_size_gb;
  const diskShrink = diskGb < vm.disk_size_gb;
  const ramOverQuota = ramMb > ramAvailableMb;
  const vcpuOverQuota = vcpus > vcpuAvailable;
  const diskOverQuota = diskGb > diskAvailableGb;
  const quotaBlocked = ramOverQuota || vcpuOverQuota || diskOverQuota;

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!hasChanges || diskShrink || quotaBlocked) return;

    setIsSaving(true);
    try {
      const payload: Record<string, unknown> = {};
      if (vcpus !== vm.vcpus) payload.vcpus = vcpus;
      if (ramMb !== vm.ram_mb) payload.ram_mb = ramMb;
      if (diskGb !== vm.disk_size_gb) payload.disk_size_gb = diskGb;

      const updated = await vmApi.update(tenantId, vm.id, payload as Partial<VirtualMachine>);
      onResized(updated);
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(`Resize failed: ${detail}`);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="card p-5 mb-8 border-2 border-yellow-200 dark:border-yellow-800 bg-yellow-50/50 dark:bg-yellow-900/10">
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <AdjustmentsHorizontalIcon className="h-5 w-5 text-yellow-600 dark:text-yellow-400" />
        <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Adjust resources</h2>
        <span className="text-xs bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400 px-2 py-0.5 rounded-full">
          VM is stopped
        </span>
      </div>

      {quotaLoaded && currentTenant && (
        <div className="mb-4 rounded-lg border border-gray-200 dark:border-gray-700 bg-white/60 dark:bg-gray-900/40 px-3 py-2.5 text-xs text-gray-600 dark:text-gray-300 space-y-1">
          <p>
            Tenant <span className="font-medium text-gray-900 dark:text-white">{currentTenant.name}</span>
            {' · '}RAM quota {formatRam(maxRamMb)}
            {' · '}used {formatRam(tenantRamUsedMb)}
            {' · '}
            <span className="font-medium text-gray-900 dark:text-white">
              {formatRam(tenantRamRemainingMb)} free
            </span>
          </p>
          <p className="text-gray-500 dark:text-gray-400">
            For this VM you can set up to {formatRam(ramAvailableMb)} RAM
            {' · '}{vcpuAvailable} vCPU
            {' · '}{diskAvailableGb} GB disk (including its current allocation).
          </p>
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-5">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-5">
          {/* vCPUs */}
          <div>
            <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
              vCPUs
            </label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={1}
                max={vcpuAvailable || 1}
                value={vcpus}
                onChange={(e) => setVcpus(Math.max(1, Number(e.target.value) || 1))}
                className="input w-28 text-center font-semibold"
              />
              <span className="text-xs text-gray-400 dark:text-gray-300">cores</span>
            </div>
            <p className="text-xs text-gray-400 dark:text-gray-300 mt-1">
              Max for this VM: {vcpuAvailable}
            </p>
            {vcpus !== vm.vcpus && (
              <p className="text-xs text-yellow-600 dark:text-yellow-400 mt-1">
                {vm.vcpus} → {vcpus}
              </p>
            )}
            {vcpuOverQuota && (
              <p className="text-xs text-red-500 mt-1">Exceeds tenant vCPU quota</p>
            )}
          </div>

          {/* RAM — free entry, no presets/slider */}
          <div>
            <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
              RAM (MB)
            </label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={256}
                max={ramAvailableMb || 256}
                step={1}
                value={ramMb}
                onChange={(e) => setRamMb(Math.max(256, Number(e.target.value) || 256))}
                className="input w-36 text-center font-semibold"
              />
              <span className="text-xs text-gray-400 dark:text-gray-300">MB</span>
            </div>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
              {formatRam(ramMb)}
              {' · '}available for this VM: {formatRam(ramAvailableMb)}
              {' · '}tenant free: {formatRam(tenantRamRemainingMb)}
            </p>
            {ramMb !== vm.ram_mb && (
              <p className="text-xs text-yellow-600 dark:text-yellow-400 mt-1">
                {formatRam(vm.ram_mb)} → {formatRam(ramMb)}
              </p>
            )}
            {ramOverQuota && (
              <p className="text-xs text-red-500 mt-1">
                Exceeds tenant RAM quota (max {formatRam(ramAvailableMb)} for this VM)
              </p>
            )}
          </div>

          {/* Disk */}
          <div>
            <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
              Disk
            </label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={vm.disk_size_gb}
                max={diskAvailableGb || vm.disk_size_gb}
                value={diskGb}
                onChange={(e) => setDiskGb(Math.max(vm.disk_size_gb, Number(e.target.value) || vm.disk_size_gb))}
                className="input w-24 text-center font-semibold"
              />
              <span className="text-xs text-gray-400 dark:text-gray-300">GB</span>
            </div>
            <p className="text-xs text-gray-400 dark:text-gray-300 mt-1">
              Min: {vm.disk_size_gb} GB (expand only) · max for this VM: {diskAvailableGb} GB
            </p>
            {diskGb !== vm.disk_size_gb && (
              <p className={`text-xs mt-1 ${diskShrink ? 'text-red-500' : 'text-yellow-600 dark:text-yellow-400'}`}>
                {vm.disk_size_gb} GB → {diskGb} GB
                {diskShrink && ' (not possible!)'}
              </p>
            )}
            {diskOverQuota && (
              <p className="text-xs text-red-500 mt-1">Exceeds tenant storage quota</p>
            )}
          </div>
        </div>

        {/* Save */}
        <div className="flex items-center gap-3 flex-wrap">
          <button
            type="submit"
            disabled={!hasChanges || diskShrink || quotaBlocked || isSaving}
            className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-primary-700 transition-colors disabled:opacity-50"
          >
            {isSaving ? (
              <><ArrowPathIcon className="h-4 w-4 animate-spin" /> Applying...</>
            ) : (
              <><AdjustmentsHorizontalIcon className="h-4 w-4" /> Save resources</>
            )}
          </button>
          {hasChanges && (
            <button
              type="button"
              onClick={() => { setVcpus(vm.vcpus); setRamMb(vm.ram_mb); setDiskGb(vm.disk_size_gb); }}
              className="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
            >
              Reset
            </button>
          )}
          {hasChanges && !diskShrink && !quotaBlocked && (
            <span className="text-xs text-yellow-600 dark:text-yellow-400">
              Changes take effect on next start
            </span>
          )}
        </div>
      </form>
    </div>
  );
}

/* ── VM Password Reset Panel ──────────────────────────────────── */

function VMPasswordResetPanel({
  tenantId,
  vmId,
  vmName,
  vmStatus,
}: {
  tenantId: string;
  vmId: string;
  vmName: string;
  vmStatus: string;
}) {
  const [defaultUser, setDefaultUser] = useState('cloud');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [disablePasswordLogin, setDisablePasswordLogin] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);

  const validatePassword = (pw: string): string | null => {
    if (pw.length < 8) return 'Passwort muss mindestens 8 Zeichen haben';
    if (!/[A-Z]/.test(pw)) return 'Passwort braucht mindestens einen Großbuchstaben';
    if (!/[a-z]/.test(pw)) return 'Passwort braucht mindestens einen Kleinbuchstaben';
    if (!/[0-9]/.test(pw)) return 'Passwort braucht mindestens eine Zahl';
    if (!/[!@#$%^&*()_+\-=[\]{}|;:',.<>?/`~]/.test(pw)) return 'Passwort braucht mindestens ein Sonderzeichen';
    return null;
  };

  const handleGeneratePassword = async () => {
    setIsGenerating(true);
    try {
      const res = await systemApi.generatePassword(16);
      setNewPassword(res.password);
      setConfirmPassword(res.password);
      toast.success('Sicheres Passwort generiert');
    } catch {
      toast.error('Passwort konnte nicht generiert werden');
    } finally {
      setIsGenerating(false);
    }
  };

  const handleResetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    const validationError = validatePassword(newPassword);
    if (validationError) {
      toast.error(validationError);
      return;
    }
    if (newPassword !== confirmPassword) {
      toast.error('Passwörter stimmen nicht überein');
      return;
    }
    if (!window.confirm(`Gast-Passwort für VM "${vmName}" wirklich zurücksetzen?`)) return;

    setIsSaving(true);
    try {
      const payload: { default_user: string; new_password: string; disable_password_login: boolean } = {
        default_user: defaultUser.trim() || 'cloud',
        new_password: newPassword,
        disable_password_login: disablePasswordLogin,
      };
      const result = await vmApi.resetGuestPassword(tenantId, vmId, payload);
      toast.success(result.message || 'Passwort-Reset vorbereitet');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(`Passwort-Reset fehlgeschlagen: ${detail}`);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="card p-5 mb-8 border border-indigo-200 dark:border-indigo-800 bg-indigo-50/40 dark:bg-indigo-900/10">
      <div className="flex items-center gap-2 mb-4">
        <KeyIcon className="h-5 w-5 text-indigo-600 dark:text-indigo-400" />
        <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Gast-Passwort zurücksetzen</h2>
      </div>

      <form onSubmit={handleResetPassword} className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
              VM-Benutzer
            </label>
            <input
              value={defaultUser}
              onChange={(e) => setDefaultUser(e.target.value)}
              className="input w-full"
              placeholder="cloud"
            />
            <p className="mt-1 text-[11px] text-gray-500 dark:text-gray-400">
              Dieser Benutzer wird fuer den Passwort-Reset verwendet (Standard: cloud).
            </p>
          </div>
          <div className="flex items-end">
            <button
              type="button"
              onClick={handleGeneratePassword}
              disabled={isGenerating || isSaving}
              className="inline-flex items-center gap-2 rounded-lg border border-indigo-300 dark:border-indigo-700 px-4 py-2 text-sm font-medium text-indigo-700 dark:text-indigo-300 hover:bg-indigo-100 dark:hover:bg-indigo-900/30 transition-colors disabled:opacity-50"
            >
              {isGenerating ? (
                <><ArrowPathIcon className="h-4 w-4 animate-spin" /> Generiere…</>
              ) : (
                <>Sicheres Passwort erzeugen</>
              )}
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
              Neues Passwort
            </label>
            <input
              type="text"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="input w-full"
              autoComplete="new-password"
              required
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
              Passwort bestätigen
            </label>
            <input
              type="text"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="input w-full"
              autoComplete="new-password"
              required
            />
          </div>
        </div>

        <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-400">
          <input
            type="checkbox"
            checked={disablePasswordLogin}
            onChange={(e) => setDisablePasswordLogin(e.target.checked)}
            className="rounded border-gray-300 dark:border-gray-600 text-primary-600 focus:ring-primary-500"
          />
          Passwort-Login per SSH deaktivieren
        </label>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={isSaving || !newPassword || !confirmPassword}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-indigo-700 transition-colors disabled:opacity-50"
          >
            {isSaving ? (
              <><ArrowPathIcon className="h-4 w-4 animate-spin" /> Speichere…</>
            ) : (
              <><KeyIcon className="h-4 w-4" /> Passwort-Reset vorbereiten</>
            )}
          </button>
          <span className="text-xs text-amber-600 dark:text-amber-400">
            {vmStatus === 'running'
              ? 'VM neu starten, damit das neue Passwort aktiv wird.'
              : 'Änderung wird beim nächsten Start angewendet.'}
          </span>
        </div>
      </form>
    </div>
  );
}

/* ── VM Disks Panel ──────────────────────────────────────────── */

function VMDisksPanel({ tenantId, vmId, vmName, vmStatus }: { tenantId: string; vmId: string; vmName: string; vmStatus: string }) {
  const [disks, setDisks] = useState<DiskItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [formLoading, setFormLoading] = useState(false);
  const [form, setForm] = useState({ name: 'data', size_gb: 10 });
  const [resizingId, setResizingId] = useState<string | null>(null);
  const [resizeGb, setResizeGb] = useState(0);

  const loadDisks = useCallback(async () => {
    try {
      const data = await vmApi.listDisks(tenantId, vmId);
      setDisks(data);
    } catch {
      // Ignore
    }
  }, [tenantId, vmId]);

  useEffect(() => { loadDisks(); }, [loadDisks]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormLoading(true);
    try {
      await vmApi.createDisk(tenantId, vmId, { name: form.name, size_gb: form.size_gb });
      toast.success(`Disk '${form.name}' created and attached`);
      setShowForm(false);
      setForm({ name: 'data', size_gb: 10 });
      loadDisks();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    } finally {
      setFormLoading(false);
    }
  };

  const handleDetach = async (d: DiskItem) => {
    if (!window.confirm(`Detach disk "${d.name}" (${d.target_dev}) from this VM?`)) return;
    try {
      await vmApi.detachDisk(tenantId, vmId, d.id);
      toast.success(`Disk '${d.name}' detached${vmStatus === 'running' ? ' (hot-detach)' : ''}`);
      loadDisks();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    }
  };

  const handleAttach = async (d: DiskItem) => {
    try {
      await vmApi.attachDisk(tenantId, vmId, d.id);
      toast.success(`Disk '${d.name}' attached${vmStatus === 'running' ? ' (hot-attach)' : ''}`);
      loadDisks();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    }
  };

  const handleResize = async (d: DiskItem) => {
    if (resizeGb <= d.size_gb) {
      toast.error(`New size must be larger than ${d.size_gb} GB`);
      return;
    }
    try {
      await vmApi.resizeDisk(tenantId, vmId, d.id, resizeGb);
      toast.success(`Disk '${d.name}' resized to ${resizeGb} GB`);
      setResizingId(null);
      loadDisks();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    }
  };

  const handleDelete = async (d: DiskItem) => {
    if (!window.confirm(`Delete disk "${d.name}" permanently? This will destroy all data on it.`)) return;
    try {
      await vmApi.deleteDisk(tenantId, vmId, d.id);
      toast.success(`Disk '${d.name}' deleted`);
      loadDisks();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    }
  };

  const statusColors: Record<string, string> = {
    attached: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
    available: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
    creating: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
    error: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  };

  return (
    <div className="card p-5 mb-8">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <ServerStackIcon className="h-5 w-5 text-gray-400 dark:text-gray-300" />
          <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Additional Disks</h2>
          <span className="text-xs text-gray-400 dark:text-gray-500">({disks.length})</span>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="text-xs text-primary-600 hover:text-primary-700 dark:text-primary-400 font-medium"
        >
          {showForm ? 'Cancel' : '+ Add Disk'}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="mb-4 p-3 rounded-lg bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Name</label>
              <input
                type="text"
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="e.g. database, logs"
                className="input w-full text-sm"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Size (GB)</label>
              <input
                type="number"
                min={1}
                max={10000}
                required
                value={form.size_gb}
                onChange={(e) => setForm({ ...form, size_gb: parseInt(e.target.value) || 10 })}
                className="input w-full text-sm"
              />
            </div>
          </div>
          {vmStatus === 'running' && (
            <p className="text-xs text-green-600 dark:text-green-400 flex items-center gap-1">
              <CheckCircleIcon className="h-3.5 w-3.5" />
              Hot-attach: Disk will be attached to the running VM immediately
            </p>
          )}
          <button type="submit" disabled={formLoading} className="btn btn-primary text-xs">
            {formLoading ? 'Creating...' : 'Create & Attach Disk'}
          </button>
        </form>
      )}

      {disks.length > 0 ? (
        <div className="space-y-2">
          {disks.map((d) => (
            <div key={d.id} className="flex items-center justify-between p-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800/50">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-gray-900 dark:text-white">{d.name}</span>
                  <span className="text-xs font-mono text-gray-500 dark:text-gray-400">{d.target_dev}</span>
                  <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${statusColors[d.status] || 'bg-gray-100 text-gray-600'}`}>
                    {d.status}
                  </span>
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                  {d.size_gb} GB · {d.format} · {d.bus}
                </p>
                {resizingId === d.id && (
                  <div className="flex items-center gap-2 mt-2">
                    <input
                      type="number"
                      min={d.size_gb + 1}
                      value={resizeGb}
                      onChange={(e) => setResizeGb(parseInt(e.target.value) || d.size_gb + 1)}
                      className="input w-24 text-xs"
                    />
                    <span className="text-xs text-gray-400">GB</span>
                    <button onClick={() => handleResize(d)} className="text-xs text-primary-600 hover:text-primary-700 dark:text-primary-400 font-medium">Apply</button>
                    <button onClick={() => setResizingId(null)} className="text-xs text-gray-400 hover:text-gray-600">Cancel</button>
                  </div>
                )}
              </div>
              <div className="flex items-center gap-1 ml-3">
                {d.status === 'attached' && (
                  <>
                    <button onClick={() => { setResizingId(d.id); setResizeGb(d.size_gb + 5); }} className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700" title="Resize">
                      Resize
                    </button>
                    <button onClick={() => handleDetach(d)} className="text-xs px-2 py-1 rounded border border-amber-300 dark:border-amber-600 text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-900/20" title="Detach">
                      Detach
                    </button>
                  </>
                )}
                {d.status === 'available' && (
                  <button onClick={() => handleAttach(d)} className="text-xs px-2 py-1 rounded border border-green-300 dark:border-green-600 text-green-600 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-900/20" title="Re-attach">
                    Attach
                  </button>
                )}
                <button onClick={() => handleDelete(d)} className="text-xs px-2 py-1 rounded border border-red-300 dark:border-red-600 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20" title="Delete">
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : !showForm ? (
        <p className="text-xs text-gray-400 dark:text-gray-500 text-center py-3">
          No additional disks. Add a disk for separate data storage (databases, logs, etc.)
        </p>
      ) : null}
    </div>
  );
}


/* ── VM Stats Panel ───────────────────────────────────────────── */

function VMStatsPanel({
  tenantId,
  vmId,
  osProfile = 'linux',
}: {
  tenantId: string;
  vmId: string;
  osProfile?: 'linux' | 'windows' | 'other';
}) {
  const [stats, setStats] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const data = await vmApi.getStats(tenantId, vmId);
        if (active) setStats(data);
      } catch { /* ignore */ }
    };
    load();
    const interval = setInterval(load, 5000);
    return () => { active = false; clearInterval(interval); };
  }, [tenantId, vmId]);

  if (!stats) return null;

  const memPct = stats.memory_percent as number || 0;
  const memUsed = ((stats.memory_kb as number) || 0) / 1024;
  const memMax = ((stats.max_memory_kb as number) || 0) / 1024;
  const disks = (stats.disks as Array<Record<string, unknown>>) || [];
  const ifaces = (stats.interfaces as Array<Record<string, unknown>>) || [];
  const guestAgentActive = stats.guest_agent_active as boolean;

  return (
    <div className="card p-5 mb-8">
      <div className="flex items-center gap-2 mb-4">
        <CpuChipIcon className="h-5 w-5 text-gray-400 dark:text-gray-300" />
        <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Resource usage</h2>
        <span className="text-xs text-gray-400 dark:text-gray-300">(live)</span>
        {!guestAgentActive && (
          <span className="text-xs bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400 px-2 py-0.5 rounded-full" title="qemu-guest-agent not detected – RAM values are estimated">
            no guest agent
          </span>
        )}
      </div>
      {!guestAgentActive && (
        <div className="mb-3 p-2.5 rounded-lg bg-yellow-50 dark:bg-yellow-900/10 border border-yellow-200 dark:border-yellow-800">
          <p className="text-xs text-yellow-700 dark:text-yellow-400">
            <strong>Note:</strong> Without qemu-guest-agent, RAM always shows 100%.{' '}
            {osProfile === 'windows' ? (
              <>Install the VirtIO guest tools in Windows (include the QEMU Guest Agent service).</>
            ) : (
              <>
                Install in the VM:
                <code className="ml-1 px-1.5 py-0.5 rounded bg-yellow-100 dark:bg-yellow-900/30 font-mono text-[10px]">
                  sudo apt install -y qemu-guest-agent && sudo systemctl enable --now qemu-guest-agent
                </code>
              </>
            )}
          </p>
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Memory */}
        <div>
          <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">RAM {guestAgentActive ? '' : '(estimated)'}</div>
          <div className="text-lg font-semibold text-gray-900 dark:text-white">{memPct}%</div>
          <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2 mt-1">
            <div className={`h-2 rounded-full transition-all ${!guestAgentActive ? 'bg-gray-400' : memPct > 90 ? 'bg-red-500' : memPct > 70 ? 'bg-yellow-500' : 'bg-green-500'}`} style={{ width: `${Math.min(memPct, 100)}%` }} />
          </div>
          <div className="text-xs text-gray-400 dark:text-gray-300 mt-1">{memUsed.toFixed(0)} / {memMax.toFixed(0)} MB</div>
        </div>

        {/* vCPUs */}
        <div>
          <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">vCPUs</div>
          <div className="text-lg font-semibold text-gray-900 dark:text-white">{stats.vcpus as number}</div>
          <div className="text-xs text-gray-400 dark:text-gray-300 mt-1">CPU time: {((stats.cpu_time_ns as number || 0) / 1e9).toFixed(1)}s</div>
        </div>

        {/* Disk I/O */}
        {disks.length > 0 && (
          <div>
            <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">Disk I/O</div>
            {disks.map((d) => (
              <div key={d.device as string} className="text-xs text-gray-600 dark:text-gray-400">
                <span className="font-mono">{d.device as string}</span>:
                R {formatBytes(d.read_bytes as number)} / W {formatBytes(d.write_bytes as number)}
              </div>
            ))}
          </div>
        )}

        {/* Network I/O */}
        {ifaces.length > 0 && (
          <div>
            <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">Network</div>
            {ifaces.map((i) => (
              <div key={i.device as string} className="text-xs text-gray-600 dark:text-gray-400">
                RX {formatBytes(i.rx_bytes as number)} / TX {formatBytes(i.tx_bytes as number)}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ── VM Snapshots Panel ───────────────────────────────────────── */

interface Snapshot {
  name: string;
  description: string;
  created_at: number;
  state: string;
  is_current: boolean;
}

function VMSnapshotsPanel({ tenantId, vmId, vmName }: { tenantId: string; vmId: string; vmName: string }) {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [snapName, setSnapName] = useState('');
  const [snapDesc, setSnapDesc] = useState('');
  const [creating, setCreating] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const loadSnapshots = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await vmApi.listSnapshots(tenantId, vmId);
      setSnapshots(data);
    } catch { /* ignore */ }
    finally { setIsLoading(false); }
  }, [tenantId, vmId]);

  useEffect(() => { loadSnapshots(); }, [loadSnapshots]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!snapName.trim()) return;
    setCreating(true);
    try {
      await vmApi.createSnapshot(tenantId, vmId, { name: snapName.trim(), description: snapDesc.trim() });
      toast.success(`Snapshot "${snapName}" created`);
      setSnapName('');
      setSnapDesc('');
      setShowForm(false);
      loadSnapshots();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    } finally { setCreating(false); }
  };

  const handleRevert = async (name: string) => {
    if (!window.confirm(`Reset VM "${vmName}" to snapshot "${name}"?\n\nAll unsaved changes will be lost.`)) return;
    setActionLoading(name);
    try {
      await vmApi.revertSnapshot(tenantId, vmId, name);
      toast.success(`VM reset to snapshot "${name}"`);
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    } finally { setActionLoading(null); }
  };

  const handleDelete = async (name: string) => {
    if (!window.confirm(`Permanently delete snapshot "${name}"?`)) return;
    setActionLoading(name);
    try {
      await vmApi.deleteSnapshot(tenantId, vmId, name);
      toast.success(`Snapshot "${name}" deleted`);
      loadSnapshots();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    } finally { setActionLoading(null); }
  };

  return (
    <div className="card p-5 mb-8">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <ClockIcon className="h-5 w-5 text-gray-400 dark:text-gray-300" />
          <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Snapshots</h2>
          <span className="text-xs bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 px-2 py-0.5 rounded-full">
            {snapshots.length}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={loadSnapshots} disabled={isLoading} className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300" title="Refresh">
            <ArrowPathIcon className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={() => setShowForm(!showForm)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-700"
          >
            + Create snapshot
          </button>
        </div>
      </div>

      {/* Create Form */}
      {showForm && (
        <form onSubmit={handleCreate} className="bg-gray-50 dark:bg-gray-800/50 rounded-lg p-4 mb-4 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <input
              type="text"
              value={snapName}
              onChange={(e) => setSnapName(e.target.value)}
              placeholder="Snapshot name"
              required
              className="input"
            />
            <input
              type="text"
              value={snapDesc}
              onChange={(e) => setSnapDesc(e.target.value)}
              placeholder="Description (optional)"
              className="input"
            />
          </div>
          <div className="flex gap-2">
            <button type="submit" disabled={creating} className="btn-primary text-xs">
              {creating ? 'Creating...' : 'Create'}
            </button>
            <button type="button" onClick={() => setShowForm(false)} className="btn-secondary text-xs">
              Cancel
            </button>
          </div>
        </form>
      )}

      {/* Snapshot List */}
      {snapshots.length === 0 ? (
        <p className="text-sm text-gray-400 dark:text-gray-300 text-center py-4">
          No snapshots. Create one to save the current VM state.
        </p>
      ) : (
        <div className="space-y-2">
          {snapshots.map((snap) => (
            <div key={snap.name} className={`flex items-center justify-between p-3 rounded-lg border ${snap.is_current ? 'border-primary-300 dark:border-primary-700 bg-primary-50 dark:bg-primary-900/10' : 'border-gray-200 dark:border-gray-700'}`}>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm text-gray-900 dark:text-white">{snap.name}</span>
                  {snap.is_current && (
                    <span className="text-xs bg-primary-100 text-primary-700 dark:bg-primary-900/30 dark:text-primary-400 px-2 py-0.5 rounded-full">
                      Current
                    </span>
                  )}
                  <span className="text-xs text-gray-400 dark:text-gray-300 capitalize">{snap.state}</span>
                </div>
                {snap.description && <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{snap.description}</p>}
                <p className="text-xs text-gray-400 dark:text-gray-300 mt-0.5">
                  {snap.created_at > 0 ? new Date(snap.created_at * 1000).toLocaleString('en-US') : '-'}
                </p>
              </div>
              <div className="flex items-center gap-1 flex-shrink-0 ml-3">
                <button
                  onClick={() => handleRevert(snap.name)}
                  disabled={!!actionLoading}
                  className="text-xs text-primary-600 hover:text-primary-800 dark:text-primary-400 dark:hover:text-primary-300 px-2 py-1 rounded hover:bg-primary-50 dark:hover:bg-primary-900/20"
                  title="Restore"
                >
                  {actionLoading === snap.name ? '...' : 'Restore'}
                </button>
                <button
                  onClick={() => handleDelete(snap.name)}
                  disabled={!!actionLoading}
                  className="text-xs text-red-600 hover:text-red-800 dark:text-red-400 px-2 py-1 rounded hover:bg-red-50 dark:hover:bg-red-900/20"
                  title="Delete"
                >
                  <TrashIcon className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


/* ── VM Backups Panel ────────────────────────────────────────── */

interface BackupItem {
  id: string;
  vm_id: string;
  name: string;
  description: string | null;
  backup_type: string;
  status: string;
  file_path: string | null;
  file_size_bytes: number | null;
  vm_name: string | null;
  error_message: string | null;
  schedule_id?: string | null;
  created_at: string;
  updated_at: string;
}

interface BackupScheduleItem {
  id: string;
  vm_id: string;
  frequency: string;
  time_utc: string;
  day_of_week: number | null;
  day_of_month: number | null;
  retention_count: number;
  is_active: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  last_status: string | null;
  created_at: string;
}

const DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const FREQ_LABELS: Record<string, string> = { daily: 'Daily', weekly: 'Weekly', monthly: 'Monthly' };


const backupStatusConfig: Record<string, { label: string; color: string; icon: React.ElementType }> = {
  creating: { label: 'Creating...', color: 'text-blue-600 bg-blue-100 dark:bg-blue-900/30 dark:text-blue-400', icon: ArrowPathIcon },
  ready: { label: 'Ready', color: 'text-green-700 bg-green-100 dark:bg-green-900/30 dark:text-green-400', icon: CheckCircleIcon },
  restoring: { label: 'Restoring...', color: 'text-yellow-700 bg-yellow-100 dark:bg-yellow-900/30 dark:text-yellow-400', icon: ArrowPathIcon },
  failed: { label: 'Failed', color: 'text-red-700 bg-red-100 dark:bg-red-900/30 dark:text-red-400', icon: ExclamationTriangleIcon },
  deleting: { label: 'Deleting...', color: 'text-gray-600 bg-gray-100 dark:bg-gray-800 dark:text-gray-400', icon: ArrowPathIcon },
};

/* ── VM Change IP Panel ──────────────────────────────────────── */

function VMChangeIPPanel({
  tenantId,
  vm,
  onChanged,
  onCancel,
}: {
  tenantId: string;
  vm: VirtualMachine;
  onChanged: (updated: VirtualMachine) => void;
  onCancel: () => void;
}) {
  const [availableIPs, setAvailableIPs] = useState<IPAllocation[]>([]);
  const [selectedIP, setSelectedIP] = useState('');
  const [customIP, setCustomIP] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [poolId, setPoolId] = useState<string | null>(null);

  // Extract current IP from cloud_init_network
  const currentIP = (() => {
    const net = vm.cloud_init_network;
    if (!net || !net.ethernets) return null;
    const ethernets = net.ethernets as Record<string, Record<string, unknown>>;
    for (const cfg of Object.values(ethernets)) {
      if (Array.isArray(cfg.addresses) && cfg.addresses.length > 0) {
        return String(cfg.addresses[0]);
      }
    }
    return null;
  })();

  useEffect(() => {
    async function load() {
      try {
        // Find the pool for this VM by checking all pools for an allocation matching this VM
        const pools = await ipamApi.listPools();
        for (const pool of pools) {
          const allocs = await ipamApi.listAllocations(pool.id);
          const vmAlloc = allocs.find((a) => a.vm_id === vm.id && a.status === 'allocated');
          if (vmAlloc) {
            setPoolId(pool.id);
            // Get available IPs in this pool
            const available = allocs.filter((a) => a.status === 'available');
            setAvailableIPs(available);
            break;
          }
        }
      } catch {
        toast.error('Failed to load available IPs');
      } finally {
        setIsLoading(false);
      }
    }
    load();
  }, [vm.id]);

  const handleSave = async () => {
    const ip = selectedIP || customIP.trim();
    if (!ip) {
      toast.error('Please select or enter an IP address');
      return;
    }

    setIsSaving(true);
    try {
      const result = await vmApi.changeIP(tenantId, vm.id, ip);
      toast.success(`IP changed: ${result.old_ip} → ${result.new_ip}`);
      // Reload VM to get updated data
      const updated = await vmApi.get(tenantId, vm.id);
      onChanged(updated);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Failed to change IP';
      toast.error(msg);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="card p-5 mb-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <GlobeAltIcon className="h-5 w-5 text-gray-400 dark:text-gray-300" />
          <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Change IP Address</h2>
        </div>
        <button onClick={onCancel} className="text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300">
          Cancel
        </button>
      </div>

      {currentIP && (
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
          Current IP: <span className="font-mono font-medium text-gray-700 dark:text-gray-200">{currentIP}</span>
        </p>
      )}

      {isLoading ? (
        <p className="text-sm text-gray-400">Loading available IPs...</p>
      ) : (
        <div className="space-y-3">
          {availableIPs.length > 0 && (
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                Select from available IPs ({availableIPs.length} free)
              </label>
              <select
                value={selectedIP}
                onChange={(e) => { setSelectedIP(e.target.value); setCustomIP(''); }}
                className="input-field"
              >
                <option value="">Select an IP...</option>
                {availableIPs.map((alloc) => (
                  <option key={alloc.id} value={alloc.ip_address}>
                    {alloc.ip_address}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div>
            <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
              Or enter IP manually
            </label>
            <input
              type="text"
              value={customIP}
              onChange={(e) => { setCustomIP(e.target.value); setSelectedIP(''); }}
              placeholder="e.g. 10.10.1.50"
              className="input-field"
            />
          </div>

          <div className="flex items-center gap-2 pt-2">
            <button
              onClick={handleSave}
              disabled={isSaving || (!selectedIP && !customIP.trim())}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {isSaving ? 'Saving...' : 'Apply new IP'}
            </button>
            <button
              onClick={onCancel}
              className="rounded-lg px-3 py-1.5 text-xs font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300"
            >
              Cancel
            </button>
          </div>

          <p className="text-xs text-amber-600 dark:text-amber-400">
            Note: The VM must be rebooted for the new IP to take effect.
          </p>
        </div>
      )}
    </div>
  );
}


function VMBackupsPanel({ tenantId, vmId, vmName, vmStatus }: { tenantId: string; vmId: string; vmName: string; vmStatus: string }) {
  const [backups, setBackups] = useState<BackupItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [backupName, setBackupName] = useState('');
  const [backupDesc, setBackupDesc] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Schedule state
  const [schedules, setSchedules] = useState<BackupScheduleItem[]>([]);
  const [showScheduleForm, setShowScheduleForm] = useState(false);
  const [scheduleForm, setScheduleForm] = useState({
    frequency: 'daily',
    time_local: '03:00',
    day_of_week: 0,
    day_of_month: 1,
    retention_count: 7,
  });
  const [scheduleLoading, setScheduleLoading] = useState(false);

  const loadBackups = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setIsLoading(true);
    try {
      const data = await vmApi.listBackups(tenantId, vmId);
      setBackups(data.items);
    } catch { /* ignore */ }
    finally {
      if (!opts?.silent) setIsLoading(false);
    }
  }, [tenantId, vmId]);

  const loadSchedules = useCallback(async () => {
    try {
      const data = await vmApi.listBackupSchedules(tenantId, vmId);
      setSchedules(data);
    } catch { /* ignore */ }
  }, [tenantId, vmId]);

  useEffect(() => { loadBackups(); loadSchedules(); }, [loadBackups, loadSchedules]);

  // Keep Backup History in sync with scheduled runs (otherwise a quiet page never sees new autos).
  useEffect(() => {
    const hasInProgress = backups.some((b) => b.status === 'creating' || b.status === 'restoring');
    const hasActiveSchedule = schedules.some((s) => s.is_active);
    const intervalMs = hasInProgress ? 5000 : hasActiveSchedule ? 15000 : 60000;
    const interval = setInterval(() => {
      loadBackups({ silent: true });
      loadSchedules();
    }, intervalMs);
    return () => clearInterval(interval);
  }, [backups, schedules, loadBackups, loadSchedules]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!backupName.trim()) return;
    setIsCreating(true);
    try {
      await vmApi.createBackup(tenantId, vmId, {
        name: backupName.trim(),
        description: backupDesc.trim() || undefined,
      });
      toast.success('Backup started');
      setBackupName('');
      setBackupDesc('');
      setShowForm(false);
      loadBackups();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    } finally {
      setIsCreating(false);
    }
  };

  const handleRestore = async (backup: BackupItem) => {
    if (!window.confirm(
      `Restore VM "${vmName}" from backup "${backup.name}"?\n\n` +
      'WARNING: The current disk will be overwritten!\n' +
      'The VM must be stopped.'
    )) return;

    setActionLoading(`restore-${backup.id}`);
    try {
      await vmApi.restoreBackup(tenantId, vmId, backup.id);
      toast.success('Restore started');
      loadBackups();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async (backup: BackupItem) => {
    if (!window.confirm(`Really delete backup "${backup.name}"? This action cannot be undone.`)) return;

    setActionLoading(`delete-${backup.id}`);
    try {
      await vmApi.deleteBackup(tenantId, vmId, backup.id);
      toast.success('Backup deleted');
      loadBackups();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    } finally {
      setActionLoading(null);
    }
  };

  const handleCreateSchedule = async (e: React.FormEvent) => {
    e.preventDefault();
    setScheduleLoading(true);
    try {
      const payload: Record<string, unknown> = {
        frequency: scheduleForm.frequency,
        time_utc: localTimeToUtc(scheduleForm.time_local),
        retention_count: scheduleForm.retention_count,
      };
      if (scheduleForm.frequency === 'weekly') payload.day_of_week = scheduleForm.day_of_week;
      if (scheduleForm.frequency === 'monthly') payload.day_of_month = scheduleForm.day_of_month;

      await vmApi.createBackupSchedule(tenantId, vmId, payload as { frequency: string; time_utc: string; retention_count: number; day_of_week?: number; day_of_month?: number });
      toast.success('Backup schedule created');
      setShowScheduleForm(false);
      loadSchedules();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    } finally {
      setScheduleLoading(false);
    }
  };

  const handleToggleSchedule = async (s: BackupScheduleItem) => {
    try {
      await vmApi.updateBackupSchedule(tenantId, vmId, s.id, { is_active: !s.is_active });
      loadSchedules();
    } catch { toast.error('Failed to update schedule'); }
  };

  const handleDeleteSchedule = async (s: BackupScheduleItem) => {
    if (!window.confirm('Delete this backup schedule?')) return;
    try {
      await vmApi.deleteBackupSchedule(tenantId, vmId, s.id);
      toast.success('Schedule deleted');
      loadSchedules();
    } catch { toast.error('Failed to delete schedule'); }
  };

  const formatScheduleDesc = (s: BackupScheduleItem) => {
    const freq = FREQ_LABELS[s.frequency] || s.frequency;
    const localTime = utcTimeToLocal(s.time_utc);
    if (s.frequency === 'weekly' && s.day_of_week !== null) {
      return `${freq} on ${DAYS_OF_WEEK[s.day_of_week]} at ${localTime}`;
    }
    if (s.frequency === 'monthly' && s.day_of_month !== null) {
      return `${freq} on day ${s.day_of_month} at ${localTime}`;
    }
    return `${freq} at ${localTime}`;
  };

  const canCreateBackup = vmStatus === 'stopped' || vmStatus === 'running';
  const canRestore = vmStatus === 'stopped';

  return (
    <div className="card p-5 mb-8">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <ArchiveBoxIcon className="h-5 w-5 text-gray-400 dark:text-gray-300" />
          <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Backups</h2>
          <span className="text-xs bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 px-2 py-0.5 rounded-full">
            {backups.length}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => { loadBackups(); loadSchedules(); }} disabled={isLoading} className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300" title="Refresh">
            <ArrowPathIcon className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
          {canCreateBackup ? (
            <button
              onClick={() => setShowForm(!showForm)}
              className="inline-flex items-center gap-1 rounded-lg bg-primary-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-primary-700"
            >
              + Create backup
            </button>
          ) : (
            <span className="text-xs text-amber-500 dark:text-amber-400" title="VM must be stopped or running for backup">
              Backup unavailable in current VM state
            </span>
          )}
        </div>
      </div>

      {/* Scheduled Backups Section */}
      <div className="mb-4">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider flex items-center gap-1.5">
            <ClockIcon className="h-3.5 w-3.5" />
            Scheduled Backups
          </h3>
          <button
            onClick={() => setShowScheduleForm(!showScheduleForm)}
            className="text-xs text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300 font-medium"
          >
            {showScheduleForm ? 'Cancel' : '+ Add Schedule'}
          </button>
        </div>

        {/* Schedule Create Form */}
        {showScheduleForm && (
          <form onSubmit={handleCreateSchedule} className="p-3 rounded-lg bg-indigo-50/50 dark:bg-indigo-900/10 border border-indigo-200 dark:border-indigo-800/30 mb-3 space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Frequency</label>
                <select
                  value={scheduleForm.frequency}
                  onChange={(e) => setScheduleForm({ ...scheduleForm, frequency: e.target.value })}
                  className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-2 py-1.5 text-xs text-gray-900 dark:text-white"
                >
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Time ({localTzLabel()})
                </label>
                <input
                  type="time"
                  value={scheduleForm.time_local}
                  onChange={(e) => setScheduleForm({ ...scheduleForm, time_local: e.target.value })}
                  className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-2 py-1.5 text-xs text-gray-900 dark:text-white"
                />
              </div>
              {scheduleForm.frequency === 'weekly' && (
                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Day</label>
                  <select
                    value={scheduleForm.day_of_week}
                    onChange={(e) => setScheduleForm({ ...scheduleForm, day_of_week: parseInt(e.target.value) })}
                    className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-2 py-1.5 text-xs text-gray-900 dark:text-white"
                  >
                    {DAYS_OF_WEEK.map((d, i) => <option key={i} value={i}>{d}</option>)}
                  </select>
                </div>
              )}
              {scheduleForm.frequency === 'monthly' && (
                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Day of Month</label>
                  <input
                    type="number"
                    min={1}
                    max={28}
                    value={scheduleForm.day_of_month}
                    onChange={(e) => setScheduleForm({ ...scheduleForm, day_of_month: parseInt(e.target.value) || 1 })}
                    className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-2 py-1.5 text-xs text-gray-900 dark:text-white"
                  />
                </div>
              )}
              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Keep (max)</label>
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={scheduleForm.retention_count}
                  onChange={(e) => setScheduleForm({ ...scheduleForm, retention_count: parseInt(e.target.value) || 7 })}
                  className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-2 py-1.5 text-xs text-gray-900 dark:text-white"
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button type="submit" disabled={scheduleLoading} className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                {scheduleLoading ? <><ArrowPathIcon className="h-3.5 w-3.5 animate-spin" /> Creating...</> : 'Create Schedule'}
              </button>
              <span className="text-xs text-gray-500 dark:text-gray-400">
                Runs even while the VM is online (live disk snapshot). Restore still needs a stopped VM.
              </span>
            </div>
          </form>
        )}

        {/* Schedule List */}
        {schedules.length > 0 ? (
          <div className="space-y-2">
            {schedules.map((s) => (
              <div key={s.id} className={`flex items-center justify-between p-2.5 rounded-lg border ${s.is_active ? 'border-indigo-200 dark:border-indigo-800/40 bg-indigo-50/30 dark:bg-indigo-900/10' : 'border-gray-200 dark:border-gray-700 opacity-60'}`}>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <ClockIcon className="h-3.5 w-3.5 text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
                    <span className="text-xs font-medium text-gray-900 dark:text-white">{formatScheduleDesc(s)}</span>
                    <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${
                      s.is_active ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'
                    }`}>
                      {s.is_active ? 'Active' : 'Paused'}
                    </span>
                    {s.last_status && (
                      <span className={`text-xs ${
                        s.last_status === 'triggered' ? 'text-green-600 dark:text-green-400'
                        : s.last_status === 'skipped_vm_running' ? 'text-amber-500 dark:text-amber-400'
                        : s.last_status === 'error' ? 'text-red-500 dark:text-red-400'
                        : 'text-gray-500 dark:text-gray-400'
                      }`}>
                        {s.last_status === 'triggered' ? 'Last: OK' : s.last_status === 'skipped_vm_running' ? 'Last: Skipped (VM running)' : s.last_status === 'error' ? 'Last: Error' : `Last: ${s.last_status}`}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    <span>Keep {s.retention_count} backups</span>
                    {s.next_run_at && <span>Next: {new Date(s.next_run_at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>}
                    {s.last_run_at && <span>Last run: {new Date(s.last_run_at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>}
                  </div>
                </div>
                <div className="flex items-center gap-1 flex-shrink-0 ml-2">
                  <button
                    onClick={() => handleToggleSchedule(s)}
                    className={`rounded px-2 py-1 text-xs font-medium transition-colors ${s.is_active ? 'text-amber-600 hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-900/20' : 'text-green-600 hover:bg-green-50 dark:text-green-400 dark:hover:bg-green-900/20'}`}
                  >
                    {s.is_active ? 'Pause' : 'Resume'}
                  </button>
                  <button
                    onClick={() => handleDeleteSchedule(s)}
                    className="rounded px-1.5 py-1 text-xs text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-900/20 transition-colors"
                  >
                    <TrashIcon className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : !showScheduleForm ? (
          <p className="text-xs text-gray-400 dark:text-gray-500 text-center py-2">
            No scheduled backups. Add a schedule to automate backups.
          </p>
        ) : null}
      </div>

      <div className="border-t border-gray-200 dark:border-gray-700 pt-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">
            Backup History
          </h3>
        </div>

        {/* Create Form */}
        {showForm && (
          <form onSubmit={handleCreate} className="mb-4 p-4 rounded-lg bg-gray-50 dark:bg-gray-800/50 space-y-3">
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Name *</label>
              <input
                value={backupName}
                onChange={(e) => setBackupName(e.target.value)}
                required
                placeholder="e.g. Before update, Daily backup"
                className="input w-full text-sm"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Description (optional)</label>
              <input
                value={backupDesc}
                onChange={(e) => setBackupDesc(e.target.value)}
                placeholder="Optional description..."
                className="input w-full text-sm"
              />
            </div>
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={isCreating || !backupName.trim()}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-700 disabled:opacity-50"
              >
                {isCreating ? (
                  <><ArrowPathIcon className="h-3.5 w-3.5 animate-spin" /> Creating...</>
                ) : (
                  <><ArchiveBoxIcon className="h-3.5 w-3.5" /> Start backup</>
                )}
              </button>
              <button type="button" onClick={() => setShowForm(false)} className="text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 px-2 py-1.5">
                Cancel
              </button>
            </div>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              A full disk backup will be created. This may take several minutes depending on disk size.
            </p>
          </form>
        )}

        {/* Backup List */}
        {backups.length === 0 ? (
          <p className="text-sm text-gray-400 dark:text-gray-300 text-center py-4">
            No backups. Create a backup to save the VM state.
          </p>
        ) : (
          <div className="space-y-2">
            {backups.map((backup) => {
              const cfg = backupStatusConfig[backup.status] || backupStatusConfig.failed;
              const StatusIcon = cfg.icon;
              const isInProgress = backup.status === 'creating' || backup.status === 'restoring';

              return (
                <div
                  key={backup.id}
                  className="flex items-center justify-between p-3 rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-sm font-medium text-gray-900 dark:text-white truncate">
                        {backup.name}
                      </span>
                      <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${cfg.color}`}>
                        <StatusIcon className={`h-3 w-3 ${isInProgress ? 'animate-spin' : ''}`} />
                        {cfg.label}
                      </span>
                      {backup.schedule_id && (
                        <span className="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-xs bg-indigo-100 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400">
                          <ClockIcon className="h-2.5 w-2.5" /> auto
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400">
                      <span>{new Date(backup.created_at).toLocaleString('en-US')}</span>
                      {backup.file_size_bytes && (
                        <span>{formatBytes(backup.file_size_bytes)}</span>
                      )}
                      {backup.description && (
                        <span className="truncate max-w-[200px]">{backup.description}</span>
                      )}
                    </div>
                    {backup.error_message && (
                      <p className="text-xs text-red-500 dark:text-red-400 mt-1 truncate">{backup.error_message}</p>
                    )}
                  </div>

                  {/* Actions */}
                  {(backup.status === 'ready' || backup.status === 'failed') && (
                    <div className="flex items-center gap-1 flex-shrink-0 ml-3">
                      {backup.status === 'ready' && (
                        <button
                          onClick={() => handleRestore(backup)}
                          disabled={!canRestore || actionLoading === `restore-${backup.id}`}
                          className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs font-medium text-primary-600 hover:text-primary-800 hover:bg-primary-50 dark:text-primary-400 dark:hover:text-primary-300 dark:hover:bg-primary-900/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                          title={canRestore ? 'Restore backup' : 'VM must be stopped'}
                        >
                          {actionLoading === `restore-${backup.id}` ? (
                            <ArrowPathIcon className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <ArrowUturnLeftIcon className="h-3.5 w-3.5" />
                          )}
                          Restore
                        </button>
                      )}
                      <button
                        onClick={() => handleDelete(backup)}
                        disabled={actionLoading === `delete-${backup.id}`}
                        className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-900/20 transition-colors"
                        title="Delete backup"
                      >
                        {actionLoading === `delete-${backup.id}` ? (
                          <ArrowPathIcon className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <TrashIcon className="h-3.5 w-3.5" />
                        )}
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Info for restore */}
        {backups.length > 0 && !canRestore && (
          <p className="text-xs text-gray-400 dark:text-gray-300 mt-3 text-center">
            The VM must be stopped first to restore.
          </p>
        )}
      </div>
    </div>
  );
}
