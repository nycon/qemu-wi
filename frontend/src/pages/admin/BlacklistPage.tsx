import { useEffect, useState, useCallback } from 'react';
import {
  NoSymbolIcon,
  PlusIcon,
  TrashIcon,
  ArrowPathIcon,
  MagnifyingGlassIcon,
  CheckCircleIcon,
  XCircleIcon,
  ExclamationTriangleIcon,
  Cog6ToothIcon,
  ClockIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { systemApi } from '@/services/api';
import type { BlacklistEntry, Fail2BanBan, Fail2BanJailStats, Fail2BanJailConfig } from '@/services/api';

function formatDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return '-';
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

const DURATION_PRESETS = [
  { label: '5 min', value: 300 },
  { label: '15 min', value: 900 },
  { label: '30 min', value: 1800 },
  { label: '1 hour', value: 3600 },
  { label: '6 hours', value: 21600 },
  { label: '12 hours', value: 43200 },
  { label: '1 day', value: 86400 },
  { label: '7 days', value: 604800 },
  { label: '30 days', value: 2592000 },
  { label: 'Permanent', value: -1 },
];

export function BlacklistPage() {
  const [manualBans, setManualBans] = useState<BlacklistEntry[]>([]);
  const [f2bBans, setF2bBans] = useState<Fail2BanBan[]>([]);
  const [f2bStats, setF2bStats] = useState<{ running: boolean; jails: Fail2BanJailStats[] }>({ running: false, jails: [] });
  const [totalBans, setTotalBans] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  const [showBanForm, setShowBanForm] = useState(false);
  const [banForm, setBanForm] = useState({ ip: '', reason: '', permanent: true });

  const [checkIp, setCheckIp] = useState('');
  const [checkResult, setCheckResult] = useState<null | { ip: string; banned: boolean; in_iptables: boolean; in_fail2ban: boolean; fail2ban_jail: string | null }>(null);

  const [showF2bLog, setShowF2bLog] = useState(false);
  const [f2bLog, setF2bLog] = useState<string[]>([]);

  const [showJailConfig, setShowJailConfig] = useState(false);
  const [jailConfigs, setJailConfigs] = useState<Record<string, Fail2BanJailConfig>>({});
  const [editingJail, setEditingJail] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<{ bantime: number; findtime: number; maxretry: number }>({ bantime: 600, findtime: 600, maxretry: 5 });
  const [isSavingConfig, setIsSavingConfig] = useState(false);

  const loadData = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await systemApi.getBlacklist();
      setManualBans(data.manual_bans);
      setF2bBans(data.fail2ban_bans);
      setF2bStats(data.fail2ban_stats);
      setTotalBans(data.total);
    } catch {
      toast.error('Failed to load blacklist');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadJailConfig = useCallback(async () => {
    try {
      const data = await systemApi.getFail2banConfig();
      setJailConfigs(data.jails);
    } catch {
      toast.error('Failed to load Fail2Ban configuration');
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleOpenJailConfig = async () => {
    if (!showJailConfig) {
      await loadJailConfig();
    }
    setShowJailConfig(!showJailConfig);
    setEditingJail(null);
  };

  const startEditing = (jailName: string) => {
    const jail = jailConfigs[jailName];
    if (!jail) return;
    setEditingJail(jailName);
    setEditForm({
      bantime: jail.bantime ?? 600,
      findtime: jail.findtime ?? 600,
      maxretry: jail.maxretry ?? 5,
    });
  };

  const handleSaveJailConfig = async () => {
    if (!editingJail) return;
    setIsSavingConfig(true);
    try {
      await systemApi.updateFail2banConfig({
        jail: editingJail,
        bantime: editForm.bantime,
        findtime: editForm.findtime,
        maxretry: editForm.maxretry,
      });
      toast.success(`Jail "${editingJail}" updated successfully`);
      setEditingJail(null);
      await loadJailConfig();
    } catch {
      toast.error('Failed to save configuration');
    } finally {
      setIsSavingConfig(false);
    }
  };

  const handleBan = async () => {
    if (!banForm.ip.trim()) {
      toast.error('IP address required');
      return;
    }
    setIsSaving(true);
    try {
      const result = await systemApi.banIP({ ip: banForm.ip.trim(), reason: banForm.reason, permanent: banForm.permanent });
      toast.success(result.message);
      setBanForm({ ip: '', reason: '', permanent: true });
      setShowBanForm(false);
      await loadData();
    } catch {
      toast.error('Failed to ban IP');
    } finally {
      setIsSaving(false);
    }
  };

  const handleUnban = async (ip: string) => {
    if (!confirm(`Unban ${ip}?`)) return;
    setIsSaving(true);
    try {
      await systemApi.unbanIP(ip);
      toast.success(`${ip} unbanned`);
      await loadData();
    } catch {
      toast.error('Failed to unban IP');
    } finally {
      setIsSaving(false);
    }
  };

  const handleCheck = async () => {
    if (!checkIp.trim()) return;
    try {
      const result = await systemApi.checkIP(checkIp.trim());
      setCheckResult(result);
    } catch {
      toast.error('Failed to check IP');
    }
  };

  const loadF2bLog = async () => {
    try {
      const data = await systemApi.getFail2banLog(100);
      setF2bLog(data.entries);
      setShowF2bLog(true);
    } catch {
      toast.error('Failed to load fail2ban log');
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
            <NoSymbolIcon className="h-7 w-7 text-red-500" />
            IP Blacklist / Fail2Ban
          </h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Manage banned IPs and automatic brute-force protection.
          </p>
        </div>
        <button onClick={loadData} className="btn btn-secondary flex items-center gap-1.5" disabled={isLoading}>
          <ArrowPathIcon className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
          <p className="text-sm text-gray-500 dark:text-gray-400">Total Banned</p>
          <p className="mt-1 text-2xl font-bold text-red-600 dark:text-red-400">{totalBans}</p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
          <p className="text-sm text-gray-500 dark:text-gray-400">Manual Bans</p>
          <p className="mt-1 text-2xl font-bold text-gray-900 dark:text-white">{manualBans.length}</p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
          <p className="text-sm text-gray-500 dark:text-gray-400">Fail2Ban Bans</p>
          <p className="mt-1 text-2xl font-bold text-orange-600 dark:text-orange-400">{f2bBans.length}</p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
          <p className="text-sm text-gray-500 dark:text-gray-400">Fail2Ban</p>
          <p className="mt-1 text-lg font-semibold">
            {f2bStats.running ? (
              <span className="text-green-600 dark:text-green-400 flex items-center gap-1">
                <CheckCircleIcon className="h-5 w-5" /> Running
              </span>
            ) : (
              <span className="text-red-600 dark:text-red-400 flex items-center gap-1">
                <XCircleIcon className="h-5 w-5" /> Stopped
              </span>
            )}
          </p>
        </div>
      </div>

      {/* Fail2Ban Jails */}
      {f2bStats.jails.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-base font-semibold text-gray-900 dark:text-white">Fail2Ban Jails</h3>
            <button
              onClick={handleOpenJailConfig}
              className="btn btn-secondary flex items-center gap-1.5 text-sm"
            >
              <Cog6ToothIcon className="h-4 w-4" />
              {showJailConfig ? 'Close Configuration' : 'Configuration'}
            </button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {f2bStats.jails.map((jail) => (
              <div key={jail.name} className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3">
                <p className="text-sm font-medium text-gray-900 dark:text-white">{jail.name}</p>
                <div className="mt-1 flex gap-4 text-xs text-gray-500 dark:text-gray-400">
                  <span>Banned: <strong className="text-red-600 dark:text-red-400">{jail.currently_banned}</strong></span>
                  <span>Total: <strong>{jail.total_banned}</strong></span>
                  <span>Failed: <strong className="text-yellow-600 dark:text-yellow-400">{jail.currently_failed}</strong></span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Fail2Ban Jail Configuration */}
      {showJailConfig && (
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
          <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
            <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
              <ClockIcon className="h-5 w-5 text-blue-500" />
              Fail2Ban Configuration
            </h3>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              Configure ban duration, detection window, and max retries per jail. Changes are applied immediately.
            </p>
          </div>

          <div className="divide-y divide-gray-200 dark:divide-gray-700">
            {Object.entries(jailConfigs).map(([name, jail]) => (
              <div key={name} className="px-5 py-4">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <p className="text-sm font-semibold text-gray-900 dark:text-white">{name}</p>
                    {editingJail !== name && (
                      <div className="mt-1 flex gap-4 text-xs text-gray-500 dark:text-gray-400">
                        <span>Ban Duration: <strong className="text-gray-700 dark:text-gray-300">{jail.bantime === -1 ? 'Permanent' : formatDuration(jail.bantime)}</strong></span>
                        <span>Find Time: <strong className="text-gray-700 dark:text-gray-300">{formatDuration(jail.findtime)}</strong></span>
                        <span>Max Retries: <strong className="text-gray-700 dark:text-gray-300">{jail.maxretry}</strong></span>
                      </div>
                    )}
                  </div>
                  {editingJail !== name && (
                    <button
                      onClick={() => startEditing(name)}
                      className="btn btn-secondary text-sm"
                    >
                      Edit
                    </button>
                  )}
                </div>

                {editingJail === name && (
                  <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-4 space-y-4">
                    {/* Ban-Dauer */}
                    <div>
                      <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                        Ban Duration (how long an IP stays banned)
                      </label>
                      <div className="flex flex-wrap gap-1.5 mb-2">
                        {DURATION_PRESETS.map((preset) => (
                          <button
                            key={preset.value}
                            onClick={() => setEditForm({ ...editForm, bantime: preset.value })}
                            className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                              editForm.bantime === preset.value
                                ? 'bg-blue-600 text-white'
                                : 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-300 dark:hover:bg-gray-600'
                            }`}
                          >
                            {preset.label}
                          </button>
                        ))}
                      </div>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          value={editForm.bantime}
                          onChange={(e) => setEditForm({ ...editForm, bantime: parseInt(e.target.value) || 0 })}
                          className="input w-32 text-sm"
                          min={-1}
                        />
                        <span className="text-xs text-gray-500 dark:text-gray-400">
                          seconds ({formatDuration(editForm.bantime)}) &middot; -1 = permanent
                        </span>
                      </div>
                    </div>

                    {/* Findtime */}
                    <div>
                      <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                        Find Time (detection window for failed attempts)
                      </label>
                      <div className="flex flex-wrap gap-1.5 mb-2">
                        {DURATION_PRESETS.filter((p) => p.value > 0).slice(0, 7).map((preset) => (
                          <button
                            key={preset.value}
                            onClick={() => setEditForm({ ...editForm, findtime: preset.value })}
                            className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                              editForm.findtime === preset.value
                                ? 'bg-blue-600 text-white'
                                : 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-300 dark:hover:bg-gray-600'
                            }`}
                          >
                            {preset.label}
                          </button>
                        ))}
                      </div>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          value={editForm.findtime}
                          onChange={(e) => setEditForm({ ...editForm, findtime: parseInt(e.target.value) || 0 })}
                          className="input w-32 text-sm"
                          min={1}
                        />
                        <span className="text-xs text-gray-500 dark:text-gray-400">
                          seconds ({formatDuration(editForm.findtime)})
                        </span>
                      </div>
                    </div>

                    {/* Max Retry */}
                    <div>
                      <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                        Max Retries (before IP gets banned)
                      </label>
                      <div className="flex flex-wrap gap-1.5 mb-2">
                        {[3, 5, 10, 15, 20].map((val) => (
                          <button
                            key={val}
                            onClick={() => setEditForm({ ...editForm, maxretry: val })}
                            className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                              editForm.maxretry === val
                                ? 'bg-blue-600 text-white'
                                : 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-300 dark:hover:bg-gray-600'
                            }`}
                          >
                            {val}
                          </button>
                        ))}
                      </div>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          value={editForm.maxretry}
                          onChange={(e) => setEditForm({ ...editForm, maxretry: parseInt(e.target.value) || 1 })}
                          className="input w-32 text-sm"
                          min={1}
                          max={100}
                        />
                        <span className="text-xs text-gray-500 dark:text-gray-400">attempts</span>
                      </div>
                    </div>

                    {/* Action Buttons */}
                    <div className="flex items-center gap-2 pt-2 border-t border-gray-200 dark:border-gray-700">
                      <button
                        onClick={handleSaveJailConfig}
                        disabled={isSavingConfig}
                        className="btn btn-primary text-sm flex items-center gap-1.5"
                      >
                        {isSavingConfig ? (
                          <span className="animate-spin rounded-full h-3.5 w-3.5 border-b-2 border-white" />
                        ) : (
                          <CheckCircleIcon className="h-4 w-4" />
                        )}
                        Save
                      </button>
                      <button
                        onClick={() => setEditingJail(null)}
                        className="btn btn-secondary text-sm"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>

          {Object.keys(jailConfigs).length === 0 && (
            <div className="px-5 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
              No Fail2Ban jails found. Is Fail2Ban running?
            </div>
          )}
        </div>
      )}

      {/* IP Check */}
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
        <h3 className="text-base font-semibold text-gray-900 dark:text-white mb-3">Check IP</h3>
        <div className="flex gap-2">
          <input
            type="text"
            value={checkIp}
            onChange={(e) => { setCheckIp(e.target.value); setCheckResult(null); }}
            placeholder="e.g. 192.168.1.100"
            className="input flex-1"
            onKeyDown={(e) => e.key === 'Enter' && handleCheck()}
          />
          <button onClick={handleCheck} className="btn btn-secondary flex items-center gap-1.5">
            <MagnifyingGlassIcon className="h-4 w-4" /> Check
          </button>
        </div>
        {checkResult && (
          <div className={`mt-3 p-3 rounded-lg text-sm ${checkResult.banned ? 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400' : 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400'}`}>
            <strong>{checkResult.ip}</strong>: {checkResult.banned ? 'BANNED' : 'Not banned'}
            {checkResult.in_iptables && <span className="ml-2">(iptables)</span>}
            {checkResult.in_fail2ban && <span className="ml-2">(fail2ban: {checkResult.fail2ban_jail})</span>}
          </div>
        )}
      </div>

      {/* Manual Blacklist */}
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-900 dark:text-white">Manual Blacklist</h3>
          <button onClick={() => setShowBanForm(!showBanForm)} className="btn btn-primary flex items-center gap-1.5 text-sm">
            <PlusIcon className="h-4 w-4" /> Ban IP
          </button>
        </div>

        {showBanForm && (
          <div className="px-5 py-4 bg-gray-50 dark:bg-gray-900/50 border-b border-gray-200 dark:border-gray-700">
            <div className="flex items-end gap-3 flex-wrap">
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">IP Address</label>
                <input
                  type="text"
                  value={banForm.ip}
                  onChange={(e) => setBanForm({ ...banForm, ip: e.target.value })}
                  placeholder="e.g. 1.2.3.4"
                  className="input w-40"
                />
              </div>
              <div className="flex-1 min-w-[150px]">
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Reason</label>
                <input
                  type="text"
                  value={banForm.reason}
                  onChange={(e) => setBanForm({ ...banForm, reason: e.target.value })}
                  placeholder="e.g. Port scanning"
                  className="input"
                />
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={banForm.permanent}
                  onChange={(e) => setBanForm({ ...banForm, permanent: e.target.checked })}
                  className="rounded border-gray-300"
                />
                <label className="text-xs text-gray-500">Permanent</label>
              </div>
              <button onClick={handleBan} disabled={isSaving} className="btn btn-primary">Ban</button>
              <button onClick={() => setShowBanForm(false)} className="btn btn-secondary">Cancel</button>
            </div>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50">
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">IP</th>
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Reason</th>
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Banned At</th>
                <th className="px-5 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Type</th>
                <th className="px-5 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {manualBans.map((entry, idx) => (
                <tr key={`manual-${idx}`} className="hover:bg-gray-50 dark:hover:bg-gray-900/30">
                  <td className="px-5 py-3 text-sm font-mono text-gray-900 dark:text-white">{entry.ip}</td>
                  <td className="px-5 py-3 text-sm text-gray-700 dark:text-gray-300">{entry.reason || '-'}</td>
                  <td className="px-5 py-3 text-sm text-gray-500 dark:text-gray-400">
                    {entry.banned_at ? new Date(entry.banned_at).toLocaleString() : '-'}
                  </td>
                  <td className="px-5 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded ${entry.permanent ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' : 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400'}`}>
                      {entry.permanent ? 'Permanent' : 'Temporary'}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-right">
                    <button onClick={() => handleUnban(entry.ip)} disabled={isSaving} className="text-green-600 hover:text-green-800 dark:text-green-400 p-1" title="Unban">
                      <TrashIcon className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
              {f2bBans.map((entry, idx) => (
                <tr key={`f2b-${idx}`} className="hover:bg-gray-50 dark:hover:bg-gray-900/30">
                  <td className="px-5 py-3 text-sm font-mono text-gray-900 dark:text-white">{entry.ip}</td>
                  <td className="px-5 py-3 text-sm text-gray-700 dark:text-gray-300">Auto-banned by fail2ban</td>
                  <td className="px-5 py-3 text-sm text-gray-500 dark:text-gray-400">-</td>
                  <td className="px-5 py-3">
                    <span className="text-xs px-2 py-0.5 rounded bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400">
                      Fail2Ban ({entry.jail})
                    </span>
                  </td>
                  <td className="px-5 py-3 text-right">
                    <button onClick={() => handleUnban(entry.ip)} disabled={isSaving} className="text-green-600 hover:text-green-800 dark:text-green-400 p-1" title="Unban">
                      <TrashIcon className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
              {manualBans.length === 0 && f2bBans.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-5 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
                    No banned IPs. That's good!
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Fail2Ban Log */}
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div>
            <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
              <ExclamationTriangleIcon className="h-5 w-5 text-yellow-500" />
              Fail2Ban Log
            </h3>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              Recent fail2ban activity (bans, unbans, detected intrusion attempts)
            </p>
          </div>
          <button onClick={loadF2bLog} className="btn btn-secondary flex items-center gap-1.5 text-sm">
            <ArrowPathIcon className="h-4 w-4" /> Load Log
          </button>
        </div>
        {showF2bLog && (
          <div className="p-4 max-h-96 overflow-y-auto">
            {f2bLog.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">No log entries found.</p>
            ) : (
              <div className="space-y-1">
                {f2bLog.map((entry, idx) => (
                  <div key={idx} className={`text-xs font-mono rounded px-3 py-1.5 ${
                    entry.includes('Ban') ? 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400' :
                    entry.includes('Unban') ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400' :
                    'bg-gray-50 dark:bg-gray-900/50 text-gray-700 dark:text-gray-300'
                  }`}>
                    {entry}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Info */}
      <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-xl p-5">
        <h4 className="text-sm font-semibold text-blue-800 dark:text-blue-300 mb-2">How the IP Blacklist works</h4>
        <ul className="text-sm text-blue-700 dark:text-blue-400 space-y-1 list-disc list-inside">
          <li><strong>Fail2Ban (automatic)</strong>: Monitors SSH and web login attempts. After too many failed attempts, the IP is banned.</li>
          <li><strong>Manual Bans</strong>: IPs can be banned permanently or temporarily.</li>
          <li>Banned IPs are blocked at the iptables level (before they reach the service).</li>
          <li>Fail2Ban bans expire automatically. Manual bans persist across reboots.</li>
          <li><strong>Configuration</strong>: Use the &quot;Configuration&quot; button to adjust ban duration, detection window, and max retries per jail.</li>
          <li>Configuration changes are applied immediately and saved persistently.</li>
        </ul>
      </div>
    </div>
  );
}
