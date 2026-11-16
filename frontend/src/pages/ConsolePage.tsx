import { useEffect, useRef, useState, useCallback } from 'react';
import { Navigate, useParams, useSearchParams } from 'react-router-dom';
import {
  ArrowsPointingOutIcon,
  ArrowsPointingInIcon,
  ComputerDesktopIcon,
  SignalIcon,
  SignalSlashIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import { useAuthStore } from '@/store/authStore';
import { useTenantStore } from '@/store/tenantStore';
import { vmApi } from '@/services/api';
import type { VirtualMachine } from '@/types';

type ConnectionState = 'connecting' | 'connected' | 'disconnected' | 'error';

/** Full-window VNC console (opened via window.open from the panel). */
export function ConsolePage() {
  const { vmId } = useParams<{ vmId: string }>();
  const [searchParams] = useSearchParams();
  const tenantFromQuery = searchParams.get('tenant') || '';
  const nameFromQuery = searchParams.get('name') || '';

  const { isAuthenticated, isLoading: authLoading, loadUser } = useAuthStore();
  const { currentTenant, loadTenants, tenants, setCurrentTenant } = useTenantStore();

  const canvasRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<InstanceType<typeof import('@novnc/novnc/lib/rfb').default> | null>(null);
  const [bootstrapped, setBootstrapped] = useState(false);
  const [connectionState, setConnectionState] = useState<ConnectionState>('connecting');
  const [vmName, setVmName] = useState<string>(nameFromQuery);
  const [desktopName, setDesktopName] = useState<string>('');
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tenantId, setTenantId] = useState<string>(tenantFromQuery);

  // Auth + tenant bootstrap (popup has its own JS heap; refresh via httpOnly cookie)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await loadUser();
        await loadTenants();
      } finally {
        if (!cancelled) setBootstrapped(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadUser, loadTenants]);

  useEffect(() => {
    if (!bootstrapped || !tenants.length) return;
    if (tenantFromQuery) {
      const match = tenants.find((t) => t.id === tenantFromQuery);
      if (match) {
        setCurrentTenant(match);
        setTenantId(match.id);
        return;
      }
    }
    if (currentTenant) {
      setTenantId(currentTenant.id);
    } else if (tenants[0]) {
      setCurrentTenant(tenants[0]);
      setTenantId(tenants[0].id);
    }
  }, [bootstrapped, tenants, tenantFromQuery, currentTenant, setCurrentTenant]);

  useEffect(() => {
    if (!tenantId || !vmId) return;
    vmApi
      .get(tenantId, vmId)
      .then((vm: VirtualMachine) => {
        setVmName(vm.name);
        document.title = `${vm.name} – Console`;
      })
      .catch(() => {
        document.title = nameFromQuery ? `${nameFromQuery} – Console` : 'Console';
      });
  }, [tenantId, vmId, nameFromQuery]);

  const connectVNC = useCallback(async () => {
    if (!tenantId || !vmId || !canvasRef.current) return;

    setConnectionState('connecting');
    setError(null);

    try {
      const consoleData = await vmApi.getConsoleToken(tenantId, vmId);
      const { default: RFB } = await import('@novnc/novnc/lib/rfb');

      const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const websocketPath = consoleData.websocket_url || `/websockify?token=${consoleData.token}`;
      const wsUrl =
        websocketPath.startsWith('ws://') || websocketPath.startsWith('wss://')
          ? websocketPath
          : `${wsProtocol}//${window.location.host}${websocketPath.startsWith('/') ? '' : '/'}${websocketPath}`;

      if (rfbRef.current) {
        rfbRef.current.disconnect();
        rfbRef.current = null;
      }

      while (canvasRef.current.firstChild) {
        canvasRef.current.removeChild(canvasRef.current.firstChild);
      }

      const rfb = new RFB(canvasRef.current, wsUrl, {
        wsProtocols: ['binary'],
      });

      rfb.scaleViewport = true;
      rfb.resizeSession = true;
      rfb.focusOnClick = true;

      rfb.addEventListener('connect', () => setConnectionState('connected'));
      rfb.addEventListener('disconnect', (e: CustomEvent) => {
        setConnectionState('disconnected');
        if (!e.detail?.clean) setError('Connection to VNC server lost');
        rfbRef.current = null;
      });
      rfb.addEventListener('credentialsrequired', () => {
        rfb.sendCredentials({ password: '' });
      });
      rfb.addEventListener('securityfailure', (e: CustomEvent) => {
        setConnectionState('error');
        setError(`Security error: ${e.detail?.reason ?? 'Unknown'}`);
      });
      rfb.addEventListener('desktopname', (e: CustomEvent) => {
        setDesktopName(e.detail?.name ?? '');
      });

      rfbRef.current = rfb;
    } catch (err: unknown) {
      setConnectionState('error');
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Console connection failed. VM must be running.';
      setError(msg);
    }
  }, [tenantId, vmId]);

  useEffect(() => {
    if (!bootstrapped || !isAuthenticated || !tenantId || !vmId) return;
    const t = setTimeout(connectVNC, 150);
    return () => {
      clearTimeout(t);
      if (rfbRef.current) {
        rfbRef.current.disconnect();
        rfbRef.current = null;
      }
    };
  }, [bootstrapped, isAuthenticated, tenantId, vmId, connectVNC]);

  useEffect(() => {
    const onFs = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener('fullscreenchange', onFs);
    return () => document.removeEventListener('fullscreenchange', onFs);
  }, []);

  const handleFullscreen = () => {
    const container = document.getElementById('console-container');
    if (!container) return;
    if (!document.fullscreenElement) {
      container.requestFullscreen().then(() => setIsFullscreen(true)).catch(() => {});
    } else {
      document.exitFullscreen().then(() => setIsFullscreen(false)).catch(() => {});
    }
  };

  const handleClose = () => {
    if (rfbRef.current) {
      try {
        rfbRef.current.disconnect();
      } catch {
        /* ignore */
      }
      rfbRef.current = null;
    }
    window.close();
  };

  if (!bootstrapped || authLoading) {
    return (
      <div className="h-screen w-screen bg-black flex items-center justify-center">
        <p className="text-sm text-gray-400 animate-pulse">Loading console…</p>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  const connectionColors: Record<ConnectionState, string> = {
    connecting: 'text-yellow-400',
    connected: 'text-green-400',
    disconnected: 'text-gray-400',
    error: 'text-red-400',
  };

  const connectionLabels: Record<ConnectionState, string> = {
    connecting: 'Connecting...',
    connected: 'Connected',
    disconnected: 'Disconnected',
    error: 'Error',
  };

  return (
    <div id="console-container" className="flex flex-col h-screen w-screen bg-black overflow-hidden">
      <div className="flex items-center justify-between bg-gray-900 px-4 py-2 shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <ComputerDesktopIcon className="h-5 w-5 text-primary-400 shrink-0" />
          <span className="text-sm text-white font-medium truncate">
            {vmName || (vmId ? `VM ${vmId.slice(0, 8)}…` : 'Console')}
          </span>
          {desktopName && <span className="text-xs text-gray-500 truncate">({desktopName})</span>}
          <span className={`flex items-center gap-1 text-xs shrink-0 ${connectionColors[connectionState]}`}>
            {connectionState === 'connected' ? (
              <SignalIcon className="h-3.5 w-3.5" />
            ) : (
              <SignalSlashIcon className="h-3.5 w-3.5" />
            )}
            {connectionState === 'connecting' ? (
              <span className="animate-pulse">{connectionLabels[connectionState]}</span>
            ) : (
              connectionLabels[connectionState]
            )}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {connectionState === 'connected' && (
            <button
              onClick={() => rfbRef.current?.sendCtrlAltDel()}
              className="text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-white px-2 py-1 rounded transition-colors"
              title="Send Ctrl+Alt+Del"
            >
              Ctrl+Alt+Del
            </button>
          )}
          {(connectionState === 'disconnected' || connectionState === 'error') && (
            <button
              onClick={connectVNC}
              className="text-xs bg-primary-600 hover:bg-primary-700 text-white px-3 py-1 rounded transition-colors"
            >
              Reconnect
            </button>
          )}
          <button
            onClick={handleFullscreen}
            className="text-gray-400 hover:text-white transition-colors p-1"
            title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
          >
            {isFullscreen ? <ArrowsPointingInIcon className="h-5 w-5" /> : <ArrowsPointingOutIcon className="h-5 w-5" />}
          </button>
          <button
            onClick={handleClose}
            className="text-gray-400 hover:text-white transition-colors p-1"
            title="Close window"
          >
            <XMarkIcon className="h-5 w-5" />
          </button>
        </div>
      </div>

      <div ref={canvasRef} className="flex-1 bg-black overflow-hidden relative min-h-0">
        {connectionState === 'connecting' && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/80 z-10">
            <div className="text-center">
              <svg className="animate-spin h-8 w-8 text-primary-500 mx-auto mb-3" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              <p className="text-sm text-gray-400">Connecting to VNC server...</p>
            </div>
          </div>
        )}

        {connectionState === 'error' && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/80 z-10">
            <div className="text-center max-w-md px-4">
              <SignalSlashIcon className="h-12 w-12 text-red-500 mx-auto mb-3" />
              <p className="text-sm text-red-400 font-medium mb-2">Connection error</p>
              <p className="text-xs text-gray-500 mb-4">{error}</p>
              <button onClick={connectVNC} className="btn-primary text-sm">
                Reconnect
              </button>
            </div>
          </div>
        )}

        {connectionState === 'disconnected' && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/80 z-10">
            <div className="text-center">
              <ComputerDesktopIcon className="h-12 w-12 text-gray-600 mx-auto mb-3" />
              <p className="text-sm text-gray-400 mb-4">Connection disconnected</p>
              <button onClick={connectVNC} className="btn-primary text-sm">
                Reconnect
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
