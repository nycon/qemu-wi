import { useState } from 'react';
import { KeyIcon, ArrowPathIcon } from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { authApi } from '@/services/api';

export function PasswordChangeCard() {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [isChanging, setIsChanging] = useState(false);

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword.length < 8) {
      toast.error('New password must be at least 8 characters long');
      return;
    }
    if (newPassword !== confirmPassword) {
      toast.error('Passwords do not match');
      return;
    }
    setIsChanging(true);
    try {
      await authApi.changePassword({ current_password: currentPassword, new_password: newPassword });
      toast.success('Password changed successfully');
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err: unknown) {
      const detail =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Unknown error'
          : String(err);
      toast.error(detail);
    } finally {
      setIsChanging(false);
    }
  };

  return (
    <div className="card p-6 mb-6">
      <div className="flex items-center gap-3 mb-5">
        <KeyIcon className="h-6 w-6 text-gray-500 dark:text-gray-400" />
        <div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Passwort ändern</h2>
          <p className="text-sm text-gray-500 dark:text-gray-400">Aktuelles Passwort eingeben und neues Passwort setzen</p>
        </div>
      </div>
      <form onSubmit={handleChangePassword} className="space-y-4 max-w-md">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Aktuelles Passwort</label>
          <input
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            required
            className="input w-full"
            autoComplete="current-password"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Neues Passwort</label>
          <input
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            required
            minLength={8}
            className="input w-full"
            autoComplete="new-password"
          />
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Mindestens 8 Zeichen</p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Neues Passwort bestätigen</label>
          <input
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
            minLength={8}
            className="input w-full"
            autoComplete="new-password"
          />
        </div>
        <button
          type="submit"
          disabled={isChanging || !currentPassword || !newPassword || !confirmPassword}
          className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-primary-700 transition-colors disabled:opacity-50"
        >
          {isChanging ? (
            <>
              <ArrowPathIcon className="h-4 w-4 animate-spin" /> Wird geändert…
            </>
          ) : (
            <>
              <KeyIcon className="h-4 w-4" /> Passwort ändern
            </>
          )}
        </button>
      </form>
    </div>
  );
}
