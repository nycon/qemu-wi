import { useEffect, useState } from 'react';
import {
  ArrowPathIcon,
  CheckCircleIcon,
  DevicePhoneMobileIcon,
  ShieldCheckIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { authApi } from '@/services/api';
import { useAuthStore } from '@/store/authStore';

/** TOTP 2FA setup / disable — available to every signed-in user (profile + settings). */
export function TwoFactorCard() {
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const [isEnabled, setIsEnabled] = useState(user?.totp_enabled ?? false);
  const [setupData, setSetupData] = useState<{
    secret: string;
    otpauth_uri: string;
    qr_code_base64: string;
  } | null>(null);
  const [verifyCode, setVerifyCode] = useState('');
  const [disablePassword, setDisablePassword] = useState('');
  const [disableTotp, setDisableTotp] = useState('');
  const [isSettingUp, setIsSettingUp] = useState(false);
  const [isVerifying, setIsVerifying] = useState(false);
  const [isDisabling, setIsDisabling] = useState(false);
  const [showDisable, setShowDisable] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setIsEnabled(user?.totp_enabled ?? false);
  }, [user?.totp_enabled]);

  const handleSetup = async () => {
    setIsSettingUp(true);
    try {
      const data = await authApi.setup2fa();
      setSetupData(data);
    } catch (err: unknown) {
      const detail =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
          : String(err);
      toast.error(detail);
    } finally {
      setIsSettingUp(false);
    }
  };

  const handleEnable = async (e: React.FormEvent) => {
    e.preventDefault();
    if (verifyCode.length !== 6) return;
    setIsVerifying(true);
    try {
      await authApi.enable2fa(verifyCode);
      setIsEnabled(true);
      setSetupData(null);
      setVerifyCode('');
      if (user) setUser({ ...user, totp_enabled: true });
      toast.success('2FA enabled successfully!');
    } catch (err: unknown) {
      const detail =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail ||
            'Invalid code'
          : String(err);
      toast.error(detail);
      setVerifyCode('');
    } finally {
      setIsVerifying(false);
    }
  };

  const handleDisable = async (e: React.FormEvent) => {
    e.preventDefault();
    if (disableTotp.length !== 6) return;
    setIsDisabling(true);
    try {
      await authApi.disable2fa(disablePassword, disableTotp);
      setIsEnabled(false);
      setShowDisable(false);
      setDisablePassword('');
      setDisableTotp('');
      if (user) setUser({ ...user, totp_enabled: false });
      toast.success('2FA disabled');
    } catch (err: unknown) {
      const detail =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
          : String(err);
      toast.error(detail);
    } finally {
      setIsDisabling(false);
    }
  };

  const handleCopySecret = () => {
    if (setupData?.secret) {
      navigator.clipboard.writeText(setupData.secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="card p-6 mb-6">
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
          <DevicePhoneMobileIcon className="h-6 w-6 text-gray-500 dark:text-gray-400" />
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              Two-Factor Authentication (2FA)
            </h2>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Additional security with TOTP (Google Authenticator, Authy, etc.)
            </p>
          </div>
        </div>
        {isEnabled && (
          <span className="inline-flex items-center gap-1 rounded-full bg-green-100 dark:bg-green-900/30 px-2.5 py-0.5 text-xs font-medium text-green-800 dark:text-green-400">
            <CheckCircleIcon className="h-3.5 w-3.5" />
            Active
          </span>
        )}
      </div>

      {isEnabled ? (
        <div>
          <div className="flex items-center gap-3 p-4 rounded-lg bg-green-50 dark:bg-green-900/10 border border-green-200 dark:border-green-800 mb-4">
            <ShieldCheckIcon className="h-8 w-8 text-green-500 flex-shrink-0" />
            <div>
              <p className="text-sm font-medium text-green-800 dark:text-green-300">2FA is enabled</p>
              <p className="text-xs text-green-600 dark:text-green-400">
                Your account is protected by a second factor.
              </p>
            </div>
          </div>

          {!showDisable ? (
            <button
              onClick={() => setShowDisable(true)}
              className="text-sm text-red-600 hover:text-red-800 dark:text-red-400 dark:hover:text-red-300 underline"
            >
              Disable 2FA...
            </button>
          ) : (
            <form
              onSubmit={handleDisable}
              className="space-y-3 p-4 rounded-lg bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800"
            >
              <p className="text-sm text-red-700 dark:text-red-400 font-medium">
                Confirm deactivation with your password and a current 2FA code:
              </p>
              <input
                type="password"
                value={disablePassword}
                onChange={(e) => setDisablePassword(e.target.value)}
                placeholder="Current Password"
                required
                className="input w-full max-w-sm"
                autoComplete="current-password"
              />
              <input
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                value={disableTotp}
                onChange={(e) => setDisableTotp(e.target.value.replace(/\D/g, '').slice(0, 6))}
                placeholder="6-digit 2FA code"
                required
                className="input w-full max-w-sm font-mono tracking-widest"
                autoComplete="one-time-code"
              />
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={isDisabling || !disablePassword || disableTotp.length !== 6}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-4 py-2 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
                >
                  {isDisabling ? 'Disabling...' : 'Disable 2FA'}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowDisable(false);
                    setDisablePassword('');
                    setDisableTotp('');
                  }}
                  className="text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 px-3 py-2"
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      ) : setupData ? (
        <div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="text-center">
              <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">
                1. Scan the QR code with your authenticator app
              </p>
              <div className="inline-block p-3 bg-white dark:bg-gray-900 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
                <img src={setupData.qr_code_base64} alt="2FA QR Code" className="w-48 h-48" />
              </div>
              <div className="mt-3">
                <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">Or enter manually:</p>
                <div className="flex items-center justify-center gap-2">
                  <code className="text-xs bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 px-2 py-1 rounded font-mono">
                    {setupData.secret}
                  </code>
                  <button
                    onClick={handleCopySecret}
                    className="text-xs text-primary-600 hover:text-primary-700 dark:text-primary-400"
                  >
                    {copied ? 'Copied!' : 'Copy'}
                  </button>
                </div>
              </div>
            </div>

            <div>
              <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">
                2. Enter the 6-digit code
              </p>
              <form onSubmit={handleEnable} className="space-y-3">
                <input
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  maxLength={6}
                  value={verifyCode}
                  onChange={(e) => setVerifyCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  placeholder="000000"
                  className="input w-full text-center text-2xl tracking-[0.5em] font-mono"
                  autoFocus
                />
                <button
                  type="submit"
                  disabled={isVerifying || verifyCode.length !== 6}
                  className="w-full inline-flex items-center justify-center gap-2 rounded-lg bg-primary-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-primary-700 transition-colors disabled:opacity-50"
                >
                  {isVerifying ? (
                    <>
                      <ArrowPathIcon className="h-4 w-4 animate-spin" /> Verifying...
                    </>
                  ) : (
                    <>
                      <ShieldCheckIcon className="h-4 w-4" /> Enable 2FA
                    </>
                  )}
                </button>
              </form>
              <button
                onClick={() => {
                  setSetupData(null);
                  setVerifyCode('');
                }}
                className="mt-3 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div>
          <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
            Protect your account with an additional factor. You need an authenticator app like{' '}
            <strong>Google Authenticator</strong>, <strong>Authy</strong> or <strong>1Password</strong>.
          </p>
          <button
            onClick={handleSetup}
            disabled={isSettingUp}
            className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-primary-700 transition-colors disabled:opacity-50"
          >
            {isSettingUp ? (
              <>
                <ArrowPathIcon className="h-4 w-4 animate-spin" /> Preparing...
              </>
            ) : (
              <>
                <ShieldCheckIcon className="h-4 w-4" /> Set up 2FA
              </>
            )}
          </button>
        </div>
      )}
    </div>
  );
}
