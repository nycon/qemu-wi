import { useCallback, useEffect, useState } from 'react';
import {
  CheckCircleIcon,
  XCircleIcon,
  ClockIcon,
  ArrowPathIcon,
  NoSymbolIcon,
} from '@heroicons/react/24/outline';
import { taskApi } from '@/services/api';
import type { Task, TaskStatus } from '@/types';

const statusIcons: Record<string, { icon: React.ElementType; color: string }> = {
  queued: { icon: ClockIcon, color: 'text-gray-400' },
  running: { icon: ArrowPathIcon, color: 'text-primary-500 animate-spin' },
  success: { icon: CheckCircleIcon, color: 'text-green-500' },
  failed: { icon: XCircleIcon, color: 'text-red-500' },
  cancelled: { icon: NoSymbolIcon, color: 'text-gray-400' },
  retrying: { icon: ArrowPathIcon, color: 'text-yellow-500 animate-spin' },
};

export function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<TaskStatus | ''>('');
  const [isLoading, setIsLoading] = useState(true);

  const loadTasks = useCallback(async () => {
    setIsLoading(true);
    try {
      const params: Record<string, string | number> = { page };
      if (filter) params.status = filter;
      const data = await taskApi.list(params);
      setTasks(data.items);
      setTotal(data.total);
    } catch {
      // handled
    } finally {
      setIsLoading(false);
    }
  }, [page, filter]);

  useEffect(() => {
    loadTasks();
    const interval = setInterval(loadTasks, 5000);
    return () => clearInterval(interval);
  }, [loadTasks]);

  return (
    <div>
      <div className="sm:flex sm:items-center sm:justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Tasks</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{total} Task(s)</p>
        </div>
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value as TaskStatus | '')}
          className="input-field w-auto mt-4 sm:mt-0"
        >
          <option value="">All Status</option>
          <option value="queued">Queued</option>
          <option value="running">Running</option>
          <option value="success">Success</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>

      <div className="card overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-800">
          <thead className="bg-gray-50 dark:bg-gray-800/50">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Status</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Type</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Progress</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Created</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Duration</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Error</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 dark:divide-gray-800">
            {tasks.map((task) => {
              const statusInfo = statusIcons[task.status] ?? statusIcons['queued']!;
              const Icon = statusInfo.icon;
              const duration =
                task.started_at && task.completed_at
                  ? `${Math.round((new Date(task.completed_at).getTime() - new Date(task.started_at).getTime()) / 1000)}s`
                  : task.started_at
                    ? 'running...'
                    : '-';

              return (
                <tr key={task.id} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                  <td className="px-6 py-4 whitespace-nowrap">
                    <Icon className={`h-5 w-5 ${statusInfo.color}`} />
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900 dark:text-white">
                    {task.task_type.replace(/_/g, ' ')}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    {task.progress_percent != null ? (
                      <div className="w-24">
                        <div className="bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                          <div
                            className="bg-primary-500 h-2 rounded-full transition-all"
                            style={{ width: `${task.progress_percent}%` }}
                          />
                        </div>
                        <span className="text-xs text-gray-500 dark:text-gray-400">{task.progress_percent}%</span>
                      </div>
                    ) : (
                      <span className="text-xs text-gray-400">-</span>
                    )}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                    {new Date(task.created_at).toLocaleString()}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">{duration}</td>
                  <td className="px-6 py-4 text-sm text-red-500 max-w-xs truncate">
                    {task.error_message ?? '-'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
