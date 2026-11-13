import { useEffect, useRef, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeftIcon,
  ArrowsPointingOutIcon,
  ArrowsPointingInIcon,
  ComputerDesktopIcon,
  SignalIcon,
  SignalSlashIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { useTenantStore } from '@/store/tenantStore';
import { vmApi } from '@/services/api';
import type { VirtualMachine } from '@/types';

type ConnectionState = 'connecting' | 'connected' | 'disconnected' | 'error';

export function ConsolePage() {
  const { vmId } = useParams<{ vmId: string }>();
  const { currentTenant } = useTenantStore();
  const navigate = useNavigate();
  const canvasRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<InstanceType<typeof import('@novnc/novnc/lib/rfb').default> | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>('connecting');
  const [vmName, setVmName] = useState<string>('');
  const [desktopName, setDesktopName] = useState<string>('');
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load VM info
  useEffect(() => {
    if (!currentTenant || !vmId) return;
    vmApi.get(currentTenant.id, vmId).then((vm: VirtualMachine) => {
      setVmName(vm.name);
    }).catch(() => {});
  }, [currentTenant, vmId]);

  // Connect to VNC via noVNC
  const connectVNC = useCallback(async () => {
    if (!currentTenant || !vmId || !canvasRef.current) return;

    setConnectionState('connecting');
    setError(null);

    try {
      // Get console token from backend (validates permissions + returns signed JWT)
      const consoleData = await vmApi.getConsoleToken(currentTenant.id, vmId);

      // Dynamic import of noVNC
      const { default: RFB } = await import('@novnc/novnc/lib/rfb');

      // Build WebSocket URL with backend-provided path for compatibility.
      const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const websocketPath = consoleData.websocket_url || `/websockify?token=${consoleData.token}`;
      const wsUrl = websocketPath.startsWith('ws://') || websocketPath.startsWith('wss://')
        ? websocketPath
        : `${wsProtocol}//${window.location.host}${websocketPath.startsWith('/') ? '' : '/'}${websocketPath}`;

      // Clean up previous connection
      if (rfbRef.current) {
        rfbRef.current.disconnect();
        rfbRef.current = null;
      }

      // Clear the target element
      while (canvasRef.current.firstChild) {
        canvasRef.current.removeChild(canvasRef.current.firstChild);
      }

      // Create RFB connection
      const rfb = new RFB(canvasRef.current, wsUrl, {
        wsProtocols: ['binary'],
      });

      rfb.scaleViewport = true;
      rfb.resizeSession = true;
      rfb.focusOnClick = true;

      // Event handlers
      rfb.addEventListener('connect', () => {
        setConnectionState('connected');
        console.log('[noVNC] Connected');
      });

      rfb.addEventListener('disconnect', (e: CustomEvent) => {
        setConnectionState('disconnected');
        if (e.detail?.clean) {
          console.log('[noVNC] Disconnected cleanly');
        } else {
          console.warn('[noVNC] Disconnected unexpectedly');
          setError('Connection to VNC server lost');
        }
        rfbRef.current = null;
      });

      rfb.addEventListener('credentialsrequired', () => {
        // VNC password required - for now, send empty
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
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Console connection failed. VM must be running.';
      setError(msg);
    }
  }, [currentTenant, vmId]);

  useEffect(() => {
    connectVNC();

    return () => {
      if (rfbRef.current) {
        rfbRef.current.disconnect();
        rfbRef.current = null;
      }
    };
  }, [connectVNC]);

  const handleFullscreen = () => {
    const container = document.getElementById('console-container');
    if (!container) return;

    if (!document.fullscreenElement) {
      container.requestFullscreen().then(() => setIsFullscreen(true)).catch(() => {});
    } else {
      document.exitFullscreen().then(() => setIsFullscreen(false)).catch(() => {});
    }
  };

  const handleCtrlAltDel = () => {
    rfbRef.current?.sendCtrlAltDel();
  };

  const handleReconnect = () => {
    connectVNC();
  };

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
    <div id="console-container" className="flex flex-col h-[calc(100vh-8rem)]">
      {/* Console toolbar */}
      <div className="flex items-center justify-between bg-gray-900 px-4 py-2 rounded-t-xl">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/vms')}
            className="text-gray-400 hover:text-white transition-colors"
            title="Back to VMs"
          >
            <ArrowLeftIcon className="h-5 w-5" />
          </button>
          <ComputerDesktopIcon className="h-5 w-5 text-primary-400" />
          <span className="text-sm text-white font-medium">
            {vmName || (vmId ? `VM ${vmId.slice(0, 8)}...` : 'Console')}
          </span>
          {desktopName && (
            <span className="text-xs text-gray-500 dark:text-gray-400">({desktopName})</span>
          )}
          <span className={`flex items-center gap-1 text-xs ${connectionColors[connectionState]}`}>
            {connectionState === 'connected' ? (
              <SignalIcon className="h-3.5 w-3.5" />
            ) : (
              <SignalSlashIcon className="h-3.5 w-3.5" />
            )}
            {connectionState === 'connecting' && (
              <span className="animate-pulse">{connectionLabels[connectionState]}</span>
            )}
            {connectionState !== 'connecting' && connectionLabels[connectionState]}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {connectionState === 'connected' && (
            <button
              onClick={handleCtrlAltDel}
              className="text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-white px-2 py-1 rounded transition-colors"
              title="Send Ctrl+Alt+Del"
            >
              Ctrl+Alt+Del
            </button>
          )}
          {(connectionState === 'disconnected' || connectionState === 'error') && (
            <button
              onClick={handleReconnect}
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
            {isFullscreen ? (
              <ArrowsPointingInIcon className="h-5 w-5" />
            ) : (
              <ArrowsPointingOutIcon className="h-5 w-5" />
            )}
          </button>
        </div>
      </div>

      {/* Console canvas */}
      <div
        ref={canvasRef}
        className="flex-1 bg-black rounded-b-xl overflow-hidden relative"
        style={{ minHeight: '400px' }}
      >
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
            <div className="text-center max-w-md">
              <SignalSlashIcon className="h-12 w-12 text-red-500 mx-auto mb-3" />
              <p className="text-sm text-red-400 font-medium mb-2">Connection error</p>
              <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">{error}</p>
              <button
                onClick={handleReconnect}
                className="btn-primary text-sm"
              >
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
              <button
                onClick={handleReconnect}
                className="btn-primary text-sm"
              >
                Reconnect
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
