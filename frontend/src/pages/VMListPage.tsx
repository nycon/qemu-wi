import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  PlusIcon,
  PlayIcon,
  StopIcon,
  ArrowPathIcon,
  ArrowUpTrayIcon,
  TrashIcon,
  ComputerDesktopIcon,
  EllipsisVerticalIcon,
} from '@heroicons/react/24/outline';
import { Menu, Transition } from '@headlessui/react';
import { Fragment } from 'react';
import { clsx } from 'clsx';
import toast from 'react-hot-toast';
import { useTenantStore } from '@/store/tenantStore';
import { projectApi, vmApi } from '@/services/api';
import type { Project, VirtualMachine, VMStatus } from '@/types';
import { openConsoleWindow } from '@/lib/openConsoleWindow';

const statusConfig: Record<string, { label: string; color: string; dot: string }> = {
  running: { label: 'Running', color: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400', dot: 'bg-green-500' },
  stopped: { label: 'Stopped', color: 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-400', dot: 'bg-gray-400' },
  creating: { label: 'Creating...', color: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400', dot: 'bg-blue-500 animate-pulse' },
  starting: { label: 'Starting...', color: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400', dot: 'bg-yellow-500 animate-pulse' },
  stopping: { label: 'Stopping...', color: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400', dot: 'bg-yellow-500 animate-pulse' },
  rebooting: { label: 'Rebooting...', color: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400', dot: 'bg-yellow-500 animate-pulse' },
  error: { label: 'Error', color: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400', dot: 'bg-red-500' },
  deleting: { label: 'Deleting...', color: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400', dot: 'bg-red-500 animate-pulse' },
};

export function VMListPage() {
  const { currentTenant } = useTenantStore();
  const navigate = useNavigate();
  const [vms, setVms] = useState<VirtualMachine[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [filter, setFilter] = useState<VMStatus | ''>('');
  const [projectFilter, setProjectFilter] = useState('');

  const showProjectUi = projects.length > 1;
  const projectNameById = Object.fromEntries(projects.map((p) => [p.id, p.name]));

  const loadVms = useCallback(async () => {
    if (!currentTenant) return;
    setIsLoading(true);
    try {
      const params: Record<string, string | number> = { page, page_size: 25 };
      if (filter) params.status = filter;
      if (projectFilter) params.project_id = projectFilter;
      const data = await vmApi.list(currentTenant.id, params);
      setVms(data.items);
      setTotal(data.total);
    } catch {
      // Error handled by interceptor
    } finally {
      setIsLoading(false);
    }
  }, [currentTenant, page, filter, projectFilter]);

  useEffect(() => {
    if (!currentTenant) return;
    projectApi.list(currentTenant.id)
      .then((list) => {
        setProjects(list);
        // Drop filter if the selected project disappeared or multi-project UI is gone
        setProjectFilter((prev) => (list.length > 1 && list.some((p) => p.id === prev) ? prev : ''));
      })
      .catch(() => setProjects([]));
  }, [currentTenant]);

  useEffect(() => {
    loadVms();
    // Poll every 10s for status updates
    const interval = setInterval(loadVms, 10000);
    return () => clearInterval(interval);
  }, [loadVms]);

  const handleAction = async (vmId: string, action: string) => {
    if (!currentTenant) return;
    try {
      await vmApi.action(currentTenant.id, vmId, action);
      toast.success(`Action '${action}' executed`);
      loadVms();
    } catch {
      toast.error(`Action '${action}' failed`);
    }
  };

  const handleDelete = async (vm: VirtualMachine, force = false) => {
    if (!currentTenant) return;
    const msg = force
      ? `VM "${vm.name}" FORCE-DELETE? The VM will be deleted immediately, even if it is still running!`
      : `VM "${vm.name}" - really delete?`;
    if (!confirm(msg)) return;
    try {
      await vmApi.delete(currentTenant.id, vm.id, force);
      toast.success(force ? 'VM will be force-deleted' : 'VM will be deleted');
      loadVms();
    } catch {
      toast.error('Delete failed');
    }
  };

  if (!currentTenant) {
    return (
      <div className="text-center py-12 text-gray-500 dark:text-gray-400">
        Please select a tenant.
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="sm:flex sm:items-center sm:justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">
            Virtual Machines
          </h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {total} VM(s) in {currentTenant.name}
          </p>
        </div>
        <div className="mt-4 sm:mt-0 flex flex-wrap gap-3">
          {showProjectUi && (
            <select
              value={projectFilter}
              onChange={(e) => { setPage(1); setProjectFilter(e.target.value); }}
              className="input-field w-auto"
            >
              <option value="">All projects</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          )}
          {/* Status filter */}
          <select
            value={filter}
            onChange={(e) => { setPage(1); setFilter(e.target.value as VMStatus | ''); }}
            className="input-field w-auto"
          >
            <option value="">All statuses</option>
            <option value="running">Running</option>
            <option value="stopped">Stopped</option>
            <option value="creating">Creating</option>
            <option value="error">Error</option>
          </select>
          <Link to="/vms/import" className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-600 transition-colors">
            <ArrowUpTrayIcon className="h-4 w-4" />
            Import
          </Link>
          <Link to="/vms/create" className="btn-primary">
            <PlusIcon className="h-5 w-5 mr-1.5" />
            Create VM
          </Link>
        </div>
      </div>

      {/* VM Cards (responsive) */}
      <div className="space-y-4">
        {isLoading && vms.length === 0 ? (
          <div className="card p-12 text-center text-gray-500 dark:text-gray-400">
            <svg className="animate-spin h-6 w-6 mx-auto mb-2 text-primary-500" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            Loading...
          </div>
        ) : vms.length === 0 ? (
          <div className="card p-12 text-center">
            <ComputerDesktopIcon className="mx-auto h-10 w-10 text-gray-400" />
            <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">No VMs found</p>
            <Link to="/vms/create" className="btn-primary mt-4 inline-flex">
              <PlusIcon className="h-5 w-5 mr-1" />
              Create first VM
            </Link>
          </div>
        ) : (
          vms.map((vm) => {
            const status = statusConfig[vm.status] ?? statusConfig['stopped']!;
            return (
              <div key={vm.id} className="card p-5 hover:ring-2 hover:ring-primary-500/30 transition-all">
                <div className="flex items-start justify-between gap-4">
                  {/* VM Info */}
                  <Link to={`/vms/${vm.id}`} className="flex-1 min-w-0 group">
                    <div className="flex items-center gap-3 mb-3">
                      <div className="flex-shrink-0 p-2 rounded-lg bg-gray-100 dark:bg-gray-800">
                        <ComputerDesktopIcon className="h-6 w-6 text-gray-500 dark:text-gray-400" />
                      </div>
                      <div className="min-w-0">
                        <h3 className="text-sm font-semibold text-gray-900 dark:text-white truncate group-hover:text-primary-600 transition-colors">
                          {vm.name}
                        </h3>
                        <p className="text-xs text-gray-500 dark:text-gray-400 truncate">{vm.hostname}</p>
                      </div>
                      <span className={`flex-shrink-0 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${status.color}`}>
                        <span className={`h-1.5 w-1.5 rounded-full ${status.dot}`} />
                        {status.label}
                      </span>
                    </div>

                    {/* Details Grid */}
                    <div className={`grid grid-cols-2 ${showProjectUi ? 'sm:grid-cols-5' : 'sm:grid-cols-4'} gap-3 text-xs`}>
                      {showProjectUi && (
                        <div>
                          <span className="text-gray-400 dark:text-gray-500">Project</span>
                          <p className="font-medium text-gray-700 dark:text-gray-300 truncate">
                            {projectNameById[vm.project_id] || '—'}
                          </p>
                        </div>
                      )}
                      <div>
                        <span className="text-gray-400 dark:text-gray-500">CPU</span>
                        <p className="font-medium text-gray-700 dark:text-gray-300">{vm.vcpus} vCPU</p>
                      </div>
                      <div>
                        <span className="text-gray-400 dark:text-gray-500">RAM</span>
                        <p className="font-medium text-gray-700 dark:text-gray-300">{vm.ram_mb >= 1024 ? `${(vm.ram_mb / 1024).toFixed(vm.ram_mb % 1024 === 0 ? 0 : 1)} GB` : `${vm.ram_mb} MB`}</p>
                      </div>
                      <div>
                        <span className="text-gray-400 dark:text-gray-500">Disk</span>
                        <p className="font-medium text-gray-700 dark:text-gray-300">{vm.disk_size_gb} GB</p>
                      </div>
                      <div>
                        <span className="text-gray-400 dark:text-gray-500">Created</span>
                        <p className="font-medium text-gray-700 dark:text-gray-300">{new Date(vm.created_at).toLocaleDateString('en-US')}</p>
                      </div>
                    </div>
                  </Link>

                  {/* Actions Menu */}
                  <Menu as="div" className="relative flex-shrink-0">
                    <Menu.Button className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
                      <EllipsisVerticalIcon className="h-5 w-5 text-gray-400" />
                    </Menu.Button>
                    <Transition as={Fragment}
                      enter="transition ease-out duration-100"
                      enterFrom="transform opacity-0 scale-95"
                      enterTo="transform opacity-100 scale-100"
                      leave="transition ease-in duration-75"
                      leaveFrom="transform opacity-100 scale-100"
                      leaveTo="transform opacity-0 scale-95"
                    >
                      <Menu.Items className="absolute right-0 z-50 mt-2 w-48 origin-top-right rounded-lg bg-white shadow-lg ring-1 ring-black/5 dark:ring-white/5 focus:outline-none dark:bg-gray-800 p-1">
                        {vm.status === 'stopped' && (
                          <Menu.Item>
                            {({ active }) => (
                              <button onClick={() => handleAction(vm.id, 'start')} className={clsx(active && 'bg-gray-100 dark:bg-gray-700', 'flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-gray-700 dark:text-gray-200')}>
                                <PlayIcon className="h-4 w-4 text-green-500" /> Start
                              </button>
                            )}
                          </Menu.Item>
                        )}
                        {vm.status === 'running' && (
                          <>
                            <Menu.Item>
                              {({ active }) => (
                                <button onClick={() => handleAction(vm.id, 'stop')} className={clsx(active && 'bg-gray-100 dark:bg-gray-700', 'flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-gray-700 dark:text-gray-200')}>
                                  <StopIcon className="h-4 w-4 text-red-500" /> Stop
                                </button>
                              )}
                            </Menu.Item>
                            <Menu.Item>
                              {({ active }) => (
                                <button onClick={() => handleAction(vm.id, 'reboot')} className={clsx(active && 'bg-gray-100 dark:bg-gray-700', 'flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-gray-700 dark:text-gray-200')}>
                                  <ArrowPathIcon className="h-4 w-4 text-yellow-500" /> Reboot
                                </button>
                              )}
                            </Menu.Item>
                            <Menu.Item>
                              {({ active }) => (
                                <button
                                  type="button"
                                  onClick={() => {
                                    const win = openConsoleWindow(vm.id, {
                                      tenantId: currentTenant?.id,
                                      name: vm.name,
                                    });
                                    if (!win) toast.error('Popup blocked — allow popups for this site to open the console');
                                  }}
                                  className={clsx(active && 'bg-gray-100 dark:bg-gray-700', 'flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-gray-700 dark:text-gray-200')}
                                >
                                  <ComputerDesktopIcon className="h-4 w-4 text-primary-500" /> Console
                                </button>
                              )}
                            </Menu.Item>
                          </>
                        )}
                        {(vm.status === 'stopped' || vm.status === 'error') && (
                          <Menu.Item>
                            {({ active }) => (
                              <button onClick={() => handleDelete(vm)} className={clsx(active && 'bg-gray-100 dark:bg-gray-700', 'flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-red-600 dark:text-red-400')}>
                                <TrashIcon className="h-4 w-4" /> Delete
                              </button>
                            )}
                          </Menu.Item>
                        )}
                        {vm.status !== 'stopped' && vm.status !== 'error' && vm.status !== 'deleting' && (
                          <Menu.Item>
                            {({ active }) => (
                              <button onClick={() => handleDelete(vm, true)} className={clsx(active && 'bg-gray-100 dark:bg-gray-700', 'flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-red-600 dark:text-red-400')}>
                                <TrashIcon className="h-4 w-4" /> Force Delete
                              </button>
                            )}
                          </Menu.Item>
                        )}
                      </Menu.Items>
                    </Transition>
                  </Menu>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Pagination */}
      {total > 25 && (
        <div className="mt-4 flex items-center justify-between">
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Page {page} of {Math.ceil(total / 25)}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="btn-secondary disabled:opacity-50"
            >
              Back
            </button>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={page * 25 >= total}
              className="btn-secondary disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
