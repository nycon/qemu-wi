import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowPathIcon, PauseIcon, PlayIcon } from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { systemApi } from '@/services/api';

const LOG_SOURCES: { id: string; label: string }[] = [
  { id: 'agent', label: 'Host Agent' },
  { id: 'backend', label: 'Backend' },
  { id: 'worker', label: 'Celery Worker' },
  { id: 'nginx', label: 'Nginx' },
  { id: 'firewall', label: 'Firewall (Kernel)' },
  { id: 'fail2ban', label: 'Fail2ban' },
  { id: 'tasks', label: 'Tasks (DB)' },
  { id: 'audit', label: 'Audit (DB)' },
  { id: 'install', label: 'Install Log' },
];

const LINE_OPTIONS = [100, 200, 500, 1000];

export function LiveLogViewer() {
  const [source, setSource] = useState('agent');
  const [lines, setLines] = useState(200);
  const [live, setLive] = useState(true);
  const [tabVisible, setTabVisible] = useState(() => document.visibilityState !== 'hidden');
  const [loading, setLoading] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const containerRef = useRef<HTMLPreElement>(null);
  const liveRef = useRef(live);
  liveRef.current = live;

  const fetchLogs = useCallback(async (quiet = false) => {
    if (!liveRef.current && quiet) return;
    setLoading(true);
    try {
      const data = await systemApi.getLogs({ source, lines });
      setFetchedAt(data.fetched_at);
      setLogLines(data.lines);
    } catch {
      if (!quiet) toast.error('Logs could not be loaded');
    } finally {
      setLoading(false);
    }
  }, [source, lines]);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  useEffect(() => {
    const onVisibility = () => setTabVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, []);

  useEffect(() => {
    if (!live || !tabVisible) return;
    const interval = setInterval(() => fetchLogs(true), 5000);
    return () => clearInterval(interval);
  }, [live, tabVisible, fetchLogs]);

  useEffect(() => {
    if (!autoScroll || !containerRef.current) return;
    containerRef.current.scrollTop = containerRef.current.scrollHeight;
  }, [logLines, autoScroll]);

  const handleScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setAutoScroll(atBottom);
  };

  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Live Logs</h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Host, Docker, Firewall und Anwendungs-Events
            {fetchedAt && (
              <span className="ml-2 font-mono">
                · {new Date(fetchedAt).toLocaleTimeString()}
              </span>
            )}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={source}
            onChange={(e) => setSource(e.target.value)}
            className="input text-sm py-1.5 min-w-[140px]"
          >
            {LOG_SOURCES.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>
          <select
            value={lines}
            onChange={(e) => setLines(Number(e.target.value))}
            className="input text-sm py-1.5"
          >
            {LINE_OPTIONS.map((n) => (
              <option key={n} value={n}>{n} lines</option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setLive((v) => !v)}
            className={`btn-secondary text-sm py-1.5 ${live ? 'text-green-700 dark:text-green-400' : ''}`}
            title={live ? 'Pause live updates' : 'Resume live updates'}
          >
            {live ? (
              <><PauseIcon className="h-4 w-4 mr-1 inline" />Live</>
            ) : (
              <><PlayIcon className="h-4 w-4 mr-1 inline" />Paused</>
            )}
          </button>
          <button
            type="button"
            onClick={() => fetchLogs()}
            disabled={loading}
            className="btn-secondary text-sm py-1.5"
          >
            <ArrowPathIcon className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      <pre
        ref={containerRef}
        onScroll={handleScroll}
        className="bg-gray-900 text-green-400 text-xs font-mono rounded-lg p-3 h-96 overflow-y-auto whitespace-pre-wrap break-all border border-gray-700"
      >
        {logLines.length === 0 ? (
          <span className="text-gray-500">
            {loading ? 'Loading…' : '(no log lines)'}
          </span>
        ) : (
          logLines.join('\n')
        )}
      </pre>

      <div className="flex justify-between mt-2 text-xs text-gray-500 dark:text-gray-400">
        <span>
          {logLines.length} lines · refresh every 5s when live
          {!tabVisible && live ? ' (paused – tab in background)' : ''}
        </span>
        {!autoScroll && (
          <button
            type="button"
            onClick={() => {
              setAutoScroll(true);
              if (containerRef.current) {
                containerRef.current.scrollTop = containerRef.current.scrollHeight;
              }
            }}
            className="text-primary-600 dark:text-primary-400 hover:underline"
          >
            Scroll to bottom
          </button>
        )}
      </div>
    </div>
  );
}
