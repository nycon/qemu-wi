import { useEffect, useState } from 'react';
import { MagnifyingGlassIcon, ArrowDownTrayIcon } from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { auditApi } from '@/services/api';
import type { AuditLog } from '@/types';
import { formatAuditDescription } from '@/lib/scheduleTime';

const actionColors: Record<string, string> = {
  login_success: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  login_failed: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  user_created: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  user_registered: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  user_updated: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  user_deactivated: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  password_changed: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  vm_created: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  vm_deleted: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  vm_action: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400',
};

export function AuditLogsPage() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [filterAction, setFilterAction] = useState('');
  const [filterResource, setFilterResource] = useState('');
  const [expandedLog, setExpandedLog] = useState<string | null>(null);

  const loadData = async () => {
    setIsLoading(true);
    try {
      const params: Record<string, string | number> = { page };
      if (filterAction) params.action = filterAction;
      if (filterResource) params.resource_type = filterResource;
      const data = await auditApi.list(params);
      setLogs(data.items);
      setTotal(data.total);
    } catch {
      // handled
    } finally {
      setIsLoading(false);
    }
  };

  const [exporting, setExporting] = useState(false);

  const handleExport = async (format: 'csv' | 'json') => {
    setExporting(true);
    try {
      const params: Record<string, string> = {};
      if (filterAction) params.action = filterAction;
      if (filterResource) params.resource_type = filterResource;
      const blob = await auditApi.export(format, params);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `audit_logs_${new Date().toISOString().slice(0, 10)}.${format}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast.success(`Exported as ${format.toUpperCase()}`);
    } catch {
      toast.error('Export failed');
    } finally {
      setExporting(false);
    }
  };

  useEffect(() => { loadData(); }, [page, filterAction, filterResource]);

  return (
    <div>
      <div className="sm:flex sm:items-center sm:justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Audit Logs</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{total} entries</p>
        </div>
        <div className="flex flex-wrap gap-3 mt-4 sm:mt-0">
          <div className="relative">
            <MagnifyingGlassIcon className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              value={filterAction}
              onChange={(e) => { setFilterAction(e.target.value); setPage(1); }}
              className="input-field pl-9 w-48"
              placeholder="Filter action..."
            />
          </div>
          <select
            value={filterResource}
            onChange={(e) => { setFilterResource(e.target.value); setPage(1); }}
            className="input-field w-auto"
          >
            <option value="">All resources</option>
            <option value="auth">Auth</option>
            <option value="user">User</option>
            <option value="vm">VM</option>
            <option value="tenant">Tenant</option>
            <option value="storage">Storage</option>
            <option value="network">Network</option>
            <option value="reverse_proxy">Reverse Proxy</option>
          </select>
          <div className="flex gap-1">
            <button
              onClick={() => handleExport('csv')}
              disabled={exporting || total === 0}
              className="btn-secondary inline-flex items-center gap-1.5 text-sm disabled:opacity-50"
              title="Export as CSV"
            >
              <ArrowDownTrayIcon className="h-4 w-4" />
              CSV
            </button>
            <button
              onClick={() => handleExport('json')}
              disabled={exporting || total === 0}
              className="btn-secondary inline-flex items-center gap-1.5 text-sm disabled:opacity-50"
              title="Export as JSON"
            >
              <ArrowDownTrayIcon className="h-4 w-4" />
              JSON
            </button>
          </div>
        </div>
      </div>

      <div className="card overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          <thead className="bg-gray-50 dark:bg-gray-800/50">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Timestamp</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Action</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Actor</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Resource</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Description</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
            {isLoading ? (
              <tr><td colSpan={6} className="px-6 py-12 text-center text-gray-500 dark:text-gray-400">Loading...</td></tr>
            ) : logs.length === 0 ? (
              <tr><td colSpan={6} className="px-6 py-12 text-center text-gray-500 dark:text-gray-400">No audit entries</td></tr>
            ) : logs.map((log) => (
              <tr
                key={log.id}
                className="hover:bg-gray-50 dark:hover:bg-gray-800/50 cursor-pointer"
                onClick={() => setExpandedLog(expandedLog === log.id ? null : log.id)}
              >
                <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                  {new Date(log.timestamp).toLocaleString()}
                </td>
                <td className="px-6 py-3 whitespace-nowrap">
                  <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${actionColors[log.action] ?? 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-400'}`}>
                    {log.action.replace(/_/g, ' ')}
                  </span>
                </td>
                <td className="px-6 py-3 whitespace-nowrap text-sm">
                  <div className="text-gray-900 dark:text-white">{log.actor_email ?? '-'}</div>
                  {log.actor_ip && <div className="text-xs text-gray-400">{log.actor_ip}</div>}
                </td>
                <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                  <div>{log.resource_type}</div>
                  {log.resource_id && <div className="text-xs text-gray-400 font-mono">{String(log.resource_id).slice(0, 8)}...</div>}
                </td>
                <td className="px-6 py-3 text-sm text-gray-500 dark:text-gray-400 max-w-xs truncate" title={log.description ?? undefined}>
                  {formatAuditDescription(log.description)}
                </td>
                <td className="px-6 py-3 whitespace-nowrap">
                  <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${log.status === 'success' ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' : 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400'}`}>
                    {log.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > 25 && (
        <div className="mt-4 flex items-center justify-between">
          <p className="text-sm text-gray-500 dark:text-gray-400">Page {page} of {Math.ceil(total / 25)}</p>
          <div className="flex gap-2">
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className="btn-secondary disabled:opacity-50">Previous</button>
            <button onClick={() => setPage((p) => p + 1)} disabled={page * 25 >= total} className="btn-secondary disabled:opacity-50">Next</button>
          </div>
        </div>
      )}
    </div>
  );
}
