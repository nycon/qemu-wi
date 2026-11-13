import { useState } from 'react';
import { useNavigate, Navigate } from 'react-router-dom';
import { ServerStackIcon, ShieldCheckIcon } from '@heroicons/react/24/outline';
import { useAuthStore } from '@/store/authStore';
import toast from 'react-hot-toast';

export function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [totpCode, setTotpCode] = useState('');
  const { login, verify2fa, isAuthenticated, isLoading, pending2fa, clearPending2fa } = useAuthStore();
  const navigate = useNavigate();

  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await login(username, password);
      // If pending2fa is now true, the form will switch to the TOTP input
      if (!useAuthStore.getState().pending2fa) {
        toast.success('Successfully signed in');
        navigate('/');
      }
    } catch (error: unknown) {
      const axiosError = error as { response?: { status?: number; data?: { detail?: string } }; message?: string };
      if (axiosError.response?.status === 401) {
        toast.error('Invalid credentials');
      } else if (axiosError.response?.data?.detail) {
        toast.error(`Error: ${axiosError.response.data.detail}`);
      } else {
        toast.error(`Login failed: ${axiosError.message ?? 'Unknown error'}`);
      }
    }
  };

  const handleVerify2fa = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await verify2fa(totpCode);
      toast.success('Successfully signed in');
      navigate('/');
    } catch (error: unknown) {
      const axiosError = error as { response?: { data?: { detail?: string } }; message?: string };
      const detail = axiosError.response?.data?.detail ?? axiosError.message ?? 'Invalid code';
      toast.error(detail);
      setTotpCode('');
    }
  };

  const handleBack = () => {
    clearPending2fa();
    setTotpCode('');
  };

  return (
    <div className="flex min-h-full flex-1 flex-col justify-center px-6 py-12 lg:px-8 bg-gray-50 dark:bg-gray-950">
      <div className="sm:mx-auto sm:w-full sm:max-w-sm">
        <div className="flex justify-center">
          <div className="rounded-xl bg-primary-600 p-3">
            <ServerStackIcon className="h-10 w-10 text-white" />
          </div>
        </div>
        <h2 className="mt-6 text-center text-2xl font-bold leading-9 tracking-tight text-gray-900 dark:text-white">
          QEMU Interface
        </h2>
        <p className="mt-2 text-center text-sm text-gray-500 dark:text-gray-400">
          {pending2fa ? 'Two-Factor Authentication' : 'Sign in to virtualization panel'}
        </p>
      </div>

      <div className="mt-10 sm:mx-auto sm:w-full sm:max-w-sm">
        <div className="card p-8">
          {pending2fa ? (
            /* ── 2FA Code Entry ── */
            <form className="space-y-6" onSubmit={handleVerify2fa}>
              <div className="text-center mb-4">
                <ShieldCheckIcon className="h-12 w-12 text-primary-500 mx-auto mb-3" />
                <p className="text-sm text-gray-600 dark:text-gray-400">
                  Enter the 6-digit code from your authenticator app
                </p>
              </div>
              <div>
                <label htmlFor="totp" className="block text-sm font-medium leading-6 text-gray-900 dark:text-gray-200">
                  2FA Code
                </label>
                <div className="mt-2">
                  <input
                    id="totp"
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    autoComplete="one-time-code"
                    required
                    maxLength={6}
                    value={totpCode}
                    onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                    className="input-field text-center text-2xl tracking-[0.5em] font-mono"
                    placeholder="000000"
                    autoFocus
                  />
                </div>
              </div>
              <div>
                <button
                  type="submit"
                  disabled={isLoading || totpCode.length !== 6}
                  className="btn-primary w-full disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isLoading ? (
                    <span className="flex items-center justify-center gap-2">
                      <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      Verifying...
                    </span>
                  ) : (
                    'Verify'
                  )}
                </button>
              </div>
              <button
                type="button"
                onClick={handleBack}
                className="w-full text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
              >
                ← Back to login
              </button>
            </form>
          ) : (
            /* ── Normal Login ── */
            <form className="space-y-6" onSubmit={handleSubmit}>
              <div>
                <label htmlFor="username" className="block text-sm font-medium leading-6 text-gray-900 dark:text-gray-200">
                  Username
                </label>
                <div className="mt-2">
                  <input
                    id="username"
                    name="username"
                    type="text"
                    autoComplete="username"
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    className="input-field"
                    placeholder="admin"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="password" className="block text-sm font-medium leading-6 text-gray-900 dark:text-gray-200">
                  Password
                </label>
                <div className="mt-2">
                  <input
                    id="password"
                    name="password"
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="input-field"
                  />
                </div>
              </div>

              <div>
                <button
                  type="submit"
                  disabled={isLoading}
                  className="btn-primary w-full disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isLoading ? (
                    <span className="flex items-center justify-center gap-2">
                      <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      Signing in...
                    </span>
                  ) : (
                    'Sign In'
                  )}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
