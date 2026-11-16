/**
 * Open the VM console in a separate browser window so it can be moved
 * independently of the panel.
 */
export function openConsoleWindow(vmId: string, opts?: { tenantId?: string; name?: string }): Window | null {
  const params = new URLSearchParams();
  if (opts?.tenantId) params.set('tenant', opts.tenantId);
  if (opts?.name) params.set('name', opts.name);
  const qs = params.toString();
  const url = `/vms/${encodeURIComponent(vmId)}/console${qs ? `?${qs}` : ''}`;

  const width = Math.min(1280, Math.max(900, window.screen.availWidth - 80));
  const height = Math.min(900, Math.max(640, window.screen.availHeight - 80));
  const left = Math.max(0, Math.round((window.screen.availWidth - width) / 2));
  const top = Math.max(0, Math.round((window.screen.availHeight - height) / 2));

  const features = [
    `width=${width}`,
    `height=${height}`,
    `left=${left}`,
    `top=${top}`,
    'resizable=yes',
    'scrollbars=no',
    'menubar=no',
    'toolbar=no',
    'location=no',
    'status=no',
  ].join(',');

  const win = window.open(url, `qi-console-${vmId}`, features);
  if (win) {
    try {
      win.focus();
    } catch {
      /* ignore */
    }
  }
  return win;
}
