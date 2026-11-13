/** Convert local HH:MM wall-clock to UTC HH:MM for schedule storage. */
export function localTimeToUtc(hhmm: string): string {
  const [h, m] = hhmm.split(':').map((x) => parseInt(x, 10));
  if (Number.isNaN(h) || Number.isNaN(m)) return hhmm;
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}

/** Convert stored UTC HH:MM to local HH:MM for display/input. */
export function utcTimeToLocal(hhmm: string): string {
  const [h, m] = hhmm.split(':').map((x) => parseInt(x, 10));
  if (Number.isNaN(h) || Number.isNaN(m)) return hhmm;
  const d = new Date();
  d.setUTCHours(h, m, 0, 0);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function localTzLabel(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'local';
  } catch {
    return 'local';
  }
}

/**
 * Audit descriptions store schedule times as "at HH:MM UTC".
 * Show the user's local wall-clock instead (same conversion as the schedule UI).
 */
export function formatAuditDescription(description: string | null | undefined): string {
  if (!description) return '-';
  return description.replace(/\bat (\d{2}):(\d{2}) UTC\b/g, (_match, hh: string, mm: string) => {
    const local = utcTimeToLocal(`${hh}:${mm}`);
    return `at ${local}`;
  });
}
