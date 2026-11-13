import { useEffect, useState, useMemo } from 'react';
import {
  ServerStackIcon,
  CpuChipIcon,
  CircleStackIcon,
  ClipboardDocumentListIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  PlayIcon,
} from '@heroicons/react/24/outline';
import { useTenantStore } from '@/store/tenantStore';
import { useAuthStore } from '@/store/authStore';
import { vmApi, taskApi } from '@/services/api';
import type { VirtualMachine, Task } from '@/types';

interface StatCardProps {
  name: string;
  value: string | number;
  icon: React.ElementType;
  color: string;
}

function StatCard({ name, value, icon: Icon, color }: StatCardProps) {
  return (
    <div className="card overflow-hidden">
      <div className="p-5">
        <div className="flex items-center">
          <div className="flex-shrink-0">
            <div className={`rounded-lg p-3 ${color}`}>
              <Icon className="h-6 w-6 text-white" />
            </div>
          </div>
          <div className="ml-5 w-0 flex-1">
            <dl>
              <dt className="truncate text-sm font-medium text-gray-500 dark:text-gray-400">
                {name}
              </dt>
              <dd className="text-2xl font-bold text-gray-900 dark:text-white">
                {value}
              </dd>
            </dl>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    running: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
    stopped: 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-400',
    creating: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
    error: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
    starting: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
    stopping: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  };

  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${colors[status] ?? colors['stopped']}`}>
      {status}
    </span>
  );
}

export function DashboardPage() {
  const { currentTenant } = useTenantStore();
  const user = useAuthStore((s) => s.user);
  const [vms, setVms] = useState<VirtualMachine[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const showQuotaSections = Boolean(user?.is_superadmin);

  useEffect(() => {
    if (!currentTenant) return;

    const loadData = async () => {
      setIsLoading(true);
      try {
        const [vmData, taskData] = await Promise.all([
          vmApi.list(currentTenant.id, { page_size: 100 }),
          taskApi.list({ page: 1, tenant_id: currentTenant.id }),
        ]);
        setVms(vmData.items);
        setTasks(taskData.items);
      } catch {
        // Error handled by interceptor
      } finally {
        setIsLoading(false);
      }
    };

    loadData();
  }, [currentTenant]);

  const runningVms = vms.filter((vm) => vm.status === 'running').length;
  const totalVcpus = vms.reduce((sum, vm) => sum + vm.vcpus, 0);
  const totalRamGb = Math.round(vms.reduce((sum, vm) => sum + vm.ram_mb, 0) / 1024);
  const totalDiskGb = vms.reduce((sum, vm) => sum + vm.disk_size_gb, 0);
  const recentTasks = tasks.slice(0, 5);

  // VM status distribution for donut chart
  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    vms.forEach((vm) => {
      counts[vm.status] = (counts[vm.status] || 0) + 1;
    });
    return counts;
  }, [vms]);

  if (!currentTenant) {
    return (
      <div className="text-center py-12">
        <ServerStackIcon className="mx-auto h-12 w-12 text-gray-400" />
        <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">
          No tenant selected
        </h3>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Please select a tenant from the top navigation.
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">
          Dashboard
        </h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Overview for tenant: {currentTenant.name}
        </p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4 mb-8">
        <StatCard
          name="Total VMs"
          value={vms.length}
          icon={ServerStackIcon}
          color="bg-primary-600"
        />
        <StatCard
          name="Running VMs"
          value={runningVms}
          icon={PlayIcon}
          color="bg-green-600"
        />
        <StatCard
          name="vCPUs / RAM"
          value={`${totalVcpus} / ${totalRamGb} GB`}
          icon={CpuChipIcon}
          color="bg-purple-600"
        />
        <StatCard
          name="Storage"
          value={`${totalDiskGb} GB`}
          icon={CircleStackIcon}
          color="bg-orange-600"
        />
      </div>

      {/* Charts Row */}
      {vms.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-8">
          {/* VM Status Donut Chart */}
          <div className="card">
            <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-800">
              <h2 className="text-base font-semibold text-gray-900 dark:text-white">
                VM Status Distribution
              </h2>
            </div>
            <div className="p-6 flex items-center justify-center gap-8">
              <DonutChart data={statusCounts} total={vms.length} />
              <div className="space-y-2">
                {Object.entries(statusCounts).map(([status, count]) => (
                  <div key={status} className="flex items-center gap-2">
                    <div
                      className="w-3 h-3 rounded-full"
                      style={{ backgroundColor: statusChartColors[status] ?? '#9ca3af' }}
                    />
                    <span className="text-sm text-gray-600 dark:text-gray-400 capitalize">
                      {status}
                    </span>
                    <span className="text-sm font-semibold text-gray-900 dark:text-white ml-auto">
                      {count}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Resource Usage Bar Chart */}
          {showQuotaSections ? (
            <div className="card">
              <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-800">
                <h2 className="text-base font-semibold text-gray-900 dark:text-white">
                  Resource Usage vs Quota
                </h2>
              </div>
              <div className="p-6 space-y-5">
                <ResourceBar
                  label="VMs"
                  used={vms.length}
                  max={currentTenant.max_vms}
                  color="#6366f1"
                />
                <ResourceBar
                  label="vCPUs"
                  used={totalVcpus}
                  max={currentTenant.max_vcpus}
                  color="#8b5cf6"
                />
                <ResourceBar
                  label="RAM"
                  used={vms.reduce((s, v) => s + v.ram_mb, 0)}
                  max={currentTenant.max_ram_mb}
                  unit="MB"
                  color="#ec4899"
                />
                <ResourceBar
                  label="Storage"
                  used={totalDiskGb}
                  max={currentTenant.max_storage_gb}
                  unit="GB"
                  color="#f97316"
                />
              </div>
            </div>
          ) : (
            <div className="card">
              <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-800">
                <h2 className="text-base font-semibold text-gray-900 dark:text-white">
                  Your Assigned Resources
                </h2>
              </div>
              <div className="p-6 grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-4">
                  <p className="text-xs text-gray-500 dark:text-gray-400">vCPUs</p>
                  <p className="text-xl font-semibold text-gray-900 dark:text-white">{totalVcpus}</p>
                </div>
                <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-4">
                  <p className="text-xs text-gray-500 dark:text-gray-400">RAM</p>
                  <p className="text-xl font-semibold text-gray-900 dark:text-white">{totalRamGb} GB</p>
                </div>
                <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-4">
                  <p className="text-xs text-gray-500 dark:text-gray-400">Storage</p>
                  <p className="text-xl font-semibold text-gray-900 dark:text-white">{totalDiskGb} GB</p>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Recent VMs */}
        <div className="card">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-800">
            <h2 className="text-base font-semibold text-gray-900 dark:text-white">
              Virtual Machines
            </h2>
          </div>
          <div className="overflow-hidden">
            {vms.length === 0 ? (
              <div className="py-8 text-center text-sm text-gray-500 dark:text-gray-400">
                No VMs available
              </div>
            ) : (
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-800">
                <thead className="bg-gray-50 dark:bg-gray-800/50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Name</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Status</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Resources</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 dark:divide-gray-800">
                  {vms.slice(0, 8).map((vm) => (
                    <tr key={vm.id} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                      <td className="px-6 py-3 whitespace-nowrap text-sm font-medium text-gray-900 dark:text-white">
                        {vm.name}
                      </td>
                      <td className="px-6 py-3 whitespace-nowrap">
                        <StatusBadge status={vm.status} />
                      </td>
                      <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                        {vm.vcpus} vCPU, {vm.ram_mb} MB, {vm.disk_size_gb} GB
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Recent Tasks */}
        <div className="card">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-800">
            <h2 className="text-base font-semibold text-gray-900 dark:text-white">
              Recent Tasks
            </h2>
          </div>
          <div className="overflow-hidden">
            {recentTasks.length === 0 ? (
              <div className="py-8 text-center text-sm text-gray-500 dark:text-gray-400">
                No tasks available
              </div>
            ) : (
              <ul className="divide-y divide-gray-200 dark:divide-gray-800">
                {recentTasks.map((task) => (
                  <li key={task.id} className="px-6 py-4 flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-gray-900 dark:text-white">
                        {task.task_type.replace(/_/g, ' ')}
                      </p>
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        {new Date(task.created_at).toLocaleString('en-US')}
                      </p>
                    </div>
                    <TaskStatusIcon status={task.status} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {showQuotaSections && (
          <div className="card lg:col-span-2">
            <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-800">
              <h2 className="text-base font-semibold text-gray-900 dark:text-white">
                Quota Usage
              </h2>
            </div>
            <div className="p-6 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
              <QuotaBar
                label="VMs"
                used={vms.length}
                max={currentTenant.max_vms}
              />
              <QuotaBar
                label="vCPUs"
                used={totalVcpus}
                max={currentTenant.max_vcpus}
              />
              <QuotaBar
                label="RAM (MB)"
                used={vms.reduce((s, v) => s + v.ram_mb, 0)}
                max={currentTenant.max_ram_mb}
              />
              <QuotaBar
                label="Storage (GB)"
                used={totalDiskGb}
                max={currentTenant.max_storage_gb}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function TaskStatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'success':
      return <CheckCircleIcon className="h-5 w-5 text-green-500" />;
    case 'failed':
      return <ExclamationTriangleIcon className="h-5 w-5 text-red-500" />;
    case 'running':
      return (
        <svg className="animate-spin h-5 w-5 text-primary-500" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      );
    default:
      return <ClipboardDocumentListIcon className="h-5 w-5 text-gray-400" />;
  }
}

function QuotaBar({ label, used, max }: { label: string; used: number; max: number }) {
  const percent = max > 0 ? Math.min(100, Math.round((used / max) * 100)) : 0;
  const color =
    percent >= 90 ? 'bg-red-500' : percent >= 70 ? 'bg-yellow-500' : 'bg-primary-500';

  return (
    <div>
      <div className="flex justify-between text-sm mb-1">
        <span className="font-medium text-gray-700 dark:text-gray-300">{label}</span>
        <span className="text-gray-500 dark:text-gray-400">
          {used} / {max}
        </span>
      </div>
      <div className="w-full bg-gray-200 rounded-full h-2.5 dark:bg-gray-700">
        <div
          className={`${color} h-2.5 rounded-full transition-all duration-300`}
          style={{ width: `${percent}%` }}
        />
      </div>
      <p className="text-xs text-gray-400 mt-1">{percent}% used</p>
    </div>
  );
}

/* ── Chart Components ── */

const statusChartColors: Record<string, string> = {
  running: '#22c55e',
  stopped: '#6b7280',
  creating: '#3b82f6',
  error: '#ef4444',
  starting: '#eab308',
  stopping: '#f59e0b',
  deleting: '#dc2626',
};

function DonutChart({ data, total }: { data: Record<string, number>; total: number }) {
  const size = 160;
  const strokeWidth = 28;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const cx = size / 2;
  const cy = size / 2;

  let cumulativePercent = 0;
  const segments = Object.entries(data).map(([status, count]) => {
    const percent = total > 0 ? count / total : 0;
    const offset = circumference * (1 - cumulativePercent);
    const length = circumference * percent;
    cumulativePercent += percent;
    return { status, count, offset, length, color: statusChartColors[status] ?? '#9ca3af' };
  });

  return (
    <div className="relative">
      <svg width={size} height={size} className="transform -rotate-90">
        {/* Background ring */}
        <circle
          cx={cx}
          cy={cy}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          className="text-gray-200 dark:text-gray-700"
        />
        {/* Segments */}
        {segments.map((seg) => (
          <circle
            key={seg.status}
            cx={cx}
            cy={cy}
            r={radius}
            fill="none"
            stroke={seg.color}
            strokeWidth={strokeWidth}
            strokeDasharray={`${seg.length} ${circumference - seg.length}`}
            strokeDashoffset={seg.offset}
            strokeLinecap="butt"
            className="transition-all duration-500"
          />
        ))}
      </svg>
      {/* Center label */}
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-bold text-gray-900 dark:text-white">{total}</span>
        <span className="text-xs text-gray-500 dark:text-gray-400">VMs</span>
      </div>
    </div>
  );
}

function ResourceBar({
  label,
  used,
  max,
  unit,
  color,
}: {
  label: string;
  used: number;
  max: number;
  unit?: string;
  color: string;
}) {
  const percent = max > 0 ? Math.min(100, Math.round((used / max) * 100)) : 0;
  const displayUsed = unit ? `${used} ${unit}` : String(used);
  const displayMax = unit ? `${max} ${unit}` : String(max);

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">{label}</span>
        <span className="text-sm text-gray-500 dark:text-gray-400">
          {displayUsed} / {displayMax}
          <span className="ml-2 text-xs font-medium" style={{ color }}>
            {percent}%
          </span>
        </span>
      </div>
      <div className="w-full bg-gray-200 rounded-full h-3 dark:bg-gray-700 overflow-hidden">
        <div
          className="h-3 rounded-full transition-all duration-500 ease-out"
          style={{ width: `${percent}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
}
