import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowPathIcon,
  KeyIcon,
  TrashIcon,
  UserCircleIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { APIKeysCard } from '@/components/APIKeysCard';
import { PasswordChangeCard } from '@/components/PasswordChangeCard';
import { TwoFactorCard } from '@/components/TwoFactorCard';
import { authApi } from '@/services/api';
import { useAuthStore } from '@/store/authStore';
import type { UserSshKey } from '@/types';

export function ProfilePage() {
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const [fullName, setFullName] = useState(user?.full_name || '');
  const [email, setEmail] = useState(user?.email || '');
  const [isSaving, setIsSaving] = useState(false);
  const [sshKeys, setSshKeys] = useState<UserSshKey[]>([]);
  const [keyName, setKeyName] = useState('');
  const [keyBody, setKeyBody] = useState('');
  const [keyLoading, setKeyLoading] = useState(false);
  const [keysLoading, setKeysLoading] = useState(true);

  useEffect(() => {
    if (user) {
      setFullName(user.full_name || '');
      setEmail(user.email || '');
      setSshKeys(user.ssh_keys || []);
    }
  }, [user]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setKeysLoading(true);
      try {
        const me = await authApi.me();
        if (cancelled) return;
        setUser(me);
        setSshKeys(me.ssh_keys || []);
      } catch {
        if (!cancelled) toast.error('Profile could not be loaded');
      } finally {
        if (!cancelled) setKeysLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [setUser]);

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSaving(true);
    try {
      const updated = await authApi.updateProfile({
        full_name: fullName || undefined,
        email: email || undefined,
      });
      setUser(updated);
      setSshKeys(updated.ssh_keys || []);
      toast.success('Profile updated');
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : 'Update failed');
    } finally {
      setIsSaving(false);
    }
  };

  const handleAddKey = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!keyBody.trim()) return;
    setKeyLoading(true);
    try {
      const created = await authApi.addSshKey({
        public_key: keyBody.trim(),
        name: keyName.trim() || undefined,
      });
      setSshKeys((prev) => [...prev, created]);
      setKeyBody('');
      setKeyName('');
      const me = await authApi.me();
      setUser(me);
      toast.success('SSH key saved (encrypted at rest)');
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : 'Could not save SSH key');
    } finally {
      setKeyLoading(false);
    }
  };

  const handleDeleteKey = async (id: string) => {
    if (!confirm('Remove this SSH key from your profile?')) return;
    try {
      await authApi.deleteSshKey(id);
      setSshKeys((prev) => prev.filter((k) => k.id !== id));
      const me = await authApi.me();
      setUser(me);
      toast.success('SSH key removed');
    } catch {
      toast.error('Delete failed');
    }
  };

  return (
    <div className="max-w-3xl mx-auto">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">User Profile</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Account details, password, 2FA, API keys and SSH public keys for VM access.
        </p>
      </div>

      <div className="card p-6 mb-6">
        <div className="flex items-center gap-3 mb-5">
          <UserCircleIcon className="h-6 w-6 text-gray-500 dark:text-gray-400" />
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Account</h2>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {user?.username} · {user?.is_superadmin ? 'Super-Admin' : 'User'}
            </p>
          </div>
        </div>
        <form onSubmit={handleSaveProfile} className="space-y-4 max-w-md">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Username</label>
            <input type="text" value={user?.username || ''} disabled className="input w-full opacity-70" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Display name</label>
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className="input w-full"
              placeholder="Your name"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="input w-full"
            />
          </div>
          <button
            type="submit"
            disabled={isSaving}
            className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
          >
            {isSaving ? <><ArrowPathIcon className="h-4 w-4 animate-spin" /> Saving…</> : 'Save profile'}
          </button>
        </form>
      </div>

      <PasswordChangeCard />

      <TwoFactorCard />

      <APIKeysCard />

      <div className="card p-6 mb-6">
        <div className="flex items-center gap-3 mb-5">
          <KeyIcon className="h-6 w-6 text-gray-500 dark:text-gray-400" />
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">SSH public keys</h2>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Stored encrypted in the database. Used when creating VMs (merged with keys on the create form).
            </p>
          </div>
        </div>

        {keysLoading ? (
          <p className="text-sm text-gray-400">Loading keys…</p>
        ) : sshKeys.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">No SSH keys saved yet.</p>
        ) : (
          <ul className="space-y-2 mb-5">
            {sshKeys.map((k) => (
              <li
                key={k.id}
                className="flex items-start justify-between gap-3 rounded-lg border border-gray-200 dark:border-gray-700 px-3 py-2.5"
              >
                <div className="min-w-0">
                  <div className="text-sm font-medium text-gray-900 dark:text-white">{k.name}</div>
                  <div className="text-xs font-mono text-gray-500 dark:text-gray-400 truncate" title={k.fingerprint}>
                    {k.fingerprint}
                  </div>
                  <div className="text-[11px] font-mono text-gray-400 truncate mt-0.5" title={k.public_key}>
                    {k.public_key.slice(0, 48)}…
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => handleDeleteKey(k.id)}
                  className="text-red-500 hover:text-red-600 p-1 shrink-0"
                  title="Remove key"
                >
                  <TrashIcon className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
        )}

        <form onSubmit={handleAddKey} className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Label (optional)</label>
            <input
              type="text"
              value={keyName}
              onChange={(e) => setKeyName(e.target.value)}
              className="input w-full max-w-md"
              placeholder="Laptop, CI, …"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Public key</label>
            <textarea
              value={keyBody}
              onChange={(e) => setKeyBody(e.target.value)}
              required
              rows={3}
              className="input w-full font-mono text-xs"
              placeholder="ssh-ed25519 AAAA… comment"
            />
          </div>
          <button
            type="submit"
            disabled={keyLoading || !keyBody.trim()}
            className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
          >
            {keyLoading ? <><ArrowPathIcon className="h-4 w-4 animate-spin" /> Saving…</> : 'Add SSH key'}
          </button>
        </form>
      </div>

      <p className="text-xs text-gray-500 dark:text-gray-400 mb-8">
        Notifications and platform options:{' '}
        <Link to="/settings" className="text-primary-600 hover:text-primary-700 dark:text-primary-400">
          Settings
        </Link>
      </p>
    </div>
  );
}
