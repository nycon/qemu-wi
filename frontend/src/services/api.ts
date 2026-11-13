import axios from 'axios';
import type {
  AuditLogListResponse,
  CloudImage,
  ConsoleToken,
  IPAllocation,
  IPPool,
  LoginRequest,
  PortForward,
  Project,
  ReverseProxyRule,
  StoragePool,
  Task,
  TaskListResponse,
  Tenant,
  TokenResponse,
  User,
  UserSshKey,
  VirtualMachine,
  VMListResponse,
} from '@/types';
import { clearAccessToken, getAccessToken, setAccessToken } from '@/services/tokenMemory';

export interface DiskItem {
  id: string;
  vm_id: string;
  name: string;
  disk_path: string;
  size_gb: number;
  format: string;
  bus: string;
  target_dev: string;
  status: 'creating' | 'available' | 'attached' | 'detaching' | 'error' | 'deleting';
  storage_pool_id: string | null;
  is_readonly: boolean;
  created_at: string;
  updated_at: string;
}

const api = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

const refreshClient = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
});

let refreshInFlight: Promise<string> | null = null;

async function refreshAccessToken(): Promise<string> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    // Refresh token is read from httpOnly cookie by the backend
    const { data } = await refreshClient.post<TokenResponse>('/auth/refresh', {});
    if (data.access_token) {
      setAccessToken(data.access_token);
      return data.access_token;
    }
    throw new Error('No access token from refresh');
  })().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

// Request interceptor: attach in-memory JWT when available (cookies still sent)
api.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor: refresh token on 401 before logout
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    const requestUrl = originalRequest?.url ?? '';
    const isAuthRequest =
      requestUrl.includes('/auth/login') ||
      requestUrl.includes('/auth/refresh') ||
      requestUrl.includes('/auth/verify-2fa') ||
      requestUrl.includes('/auth/logout');

    if (
      error.response?.status === 401 &&
      !isAuthRequest &&
      originalRequest &&
      !originalRequest._retry
    ) {
      try {
        const newToken = await refreshAccessToken();
        originalRequest._retry = true;
        originalRequest.headers.Authorization = `Bearer ${newToken}`;
        return api(originalRequest);
      } catch {
        clearAccessToken();
        if (!window.location.pathname.includes('/login')) {
          window.location.href = '/login';
        }
      }
    }
    return Promise.reject(error);
  }
);

// --- Auth ---
export const authApi = {
  login: (data: LoginRequest & { totp_code?: string }) =>
    api.post<TokenResponse>('/auth/login', data).then((r) => r.data),
  verify2fa: (data: { temp_token: string; totp_code: string }) =>
    api.post<TokenResponse>('/auth/verify-2fa', data).then((r) => r.data),
  register: (data: { email: string; username: string; password: string; full_name?: string }) =>
    api.post<User>('/auth/register', data).then((r) => r.data),
  me: () => api.get<User>('/auth/me').then((r) => r.data),
  refresh: () =>
    refreshClient.post<TokenResponse>('/auth/refresh', {}).then((r) => r.data),
  logout: () =>
    api.post('/auth/logout', {}).then((r) => r.data),
  changePassword: (data: { current_password: string; new_password: string }) =>
    api.post('/auth/change-password', data),
  updateProfile: (data: { full_name?: string; email?: string }) =>
    api.patch<User>('/auth/me', data).then((r) => r.data),
  listSshKeys: () =>
    api.get<UserSshKey[]>('/auth/me/ssh-keys').then((r) => r.data),
  addSshKey: (data: { public_key: string; name?: string }) =>
    api.post<UserSshKey>('/auth/me/ssh-keys', data).then((r) => r.data),
  deleteSshKey: (keyId: string) =>
    api.delete(`/auth/me/ssh-keys/${keyId}`),
  // 2FA
  setup2fa: () =>
    api.post<{ secret: string; otpauth_uri: string; qr_code_base64: string }>('/auth/2fa/setup').then((r) => r.data),
  enable2fa: (totp_code: string) =>
    api.post<{ enabled: boolean }>('/auth/2fa/enable', { totp_code }).then((r) => r.data),
  disable2fa: (current_password: string, totp_code: string) =>
    api.post<{ enabled: boolean }>('/auth/2fa/disable', { current_password, totp_code }).then((r) => r.data),
  get2faStatus: () =>
    api.get<{ enabled: boolean }>('/auth/2fa/status').then((r) => r.data),
};

// --- Tenants ---
export type TenantMember = {
  user_id: string;
  username: string;
  email: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
};

export type PublicIpAssignment = {
  id: string;
  pool_id: string;
  ip_address: string;
  status: string;
  vm_id?: string | null;
};

export const tenantApi = {
  list: () => api.get<Tenant[]>('/tenants').then((r) => r.data),
  get: (id: string) => api.get<Tenant>(`/tenants/${id}`).then((r) => r.data),
  create: (
    data: Partial<Tenant> & {
      admin_user_id?: string | null;
      public_ip_allocation_ids?: string[];
    },
  ) => api.post<Tenant>('/tenants', data).then((r) => r.data),
  update: (
    id: string,
    data: Partial<Tenant> & {
      admin_user_id?: string | null;
      public_ip_allocation_ids?: string[];
    },
  ) => api.patch<Tenant>(`/tenants/${id}`, data).then((r) => r.data),
  updateNotificationEmail: (id: string, notification_email: string | null) =>
    api
      .patch<Tenant>(`/tenants/${id}/notification-email`, { notification_email })
      .then((r) => r.data),
  delete: (id: string) => api.delete(`/tenants/${id}`),
  listUsers: (id: string) =>
    api.get<TenantMember[]>(`/tenants/${id}/users`).then((r) => r.data),
  listPublicIps: (id: string) =>
    api.get<PublicIpAssignment[]>(`/tenants/${id}/public-ips`).then((r) => r.data),
};

// --- Projects ---
export const projectApi = {
  list: (tenantId: string) =>
    api.get<Project[]>(`/tenants/${tenantId}/projects`).then((r) => r.data),
  get: (tenantId: string, id: string) =>
    api.get<Project>(`/tenants/${tenantId}/projects/${id}`).then((r) => r.data),
  create: (tenantId: string, data: Partial<Project>) =>
    api.post<Project>(`/tenants/${tenantId}/projects`, data).then((r) => r.data),
  update: (tenantId: string, id: string, data: Partial<Project>) =>
    api.patch<Project>(`/tenants/${tenantId}/projects/${id}`, data).then((r) => r.data),
  delete: (tenantId: string, id: string) =>
    api.delete(`/tenants/${tenantId}/projects/${id}`),
};

// --- VMs ---
export const vmApi = {
  list: (tenantId: string, params?: { page?: number; page_size?: number; status?: string; project_id?: string }) =>
    api.get<VMListResponse>(`/tenants/${tenantId}/vms`, { params }).then((r) => r.data),
  get: (tenantId: string, id: string) =>
    api.get<VirtualMachine>(`/tenants/${tenantId}/vms/${id}`).then((r) => r.data),
  create: (tenantId: string, data: Record<string, unknown>) =>
    api.post<VirtualMachine>(`/tenants/${tenantId}/vms`, data).then((r) => r.data),
  update: (tenantId: string, id: string, data: Partial<VirtualMachine>) =>
    api.patch<VirtualMachine>(`/tenants/${tenantId}/vms/${id}`, data).then((r) => r.data),
  action: (tenantId: string, id: string, action: string) =>
    api.post(`/tenants/${tenantId}/vms/${id}/action`, { action }).then((r) => r.data),
  ejectInstaller: (tenantId: string, id: string) =>
    api.post<{ status: string; message: string }>(`/tenants/${tenantId}/vms/${id}/eject-installer`).then((r) => r.data),
  attachVirtio: (tenantId: string, id: string) =>
    api.post<{ status: string; message: string; data?: { iso_path?: string; target_dev?: string } }>(`/tenants/${tenantId}/vms/${id}/attach-virtio`).then((r) => r.data),
  delete: (tenantId: string, id: string, force = false) =>
    api.delete(`/tenants/${tenantId}/vms/${id}`, { params: force ? { force: true } : undefined }).then((r) => r.data),
  getConsoleToken: (tenantId: string, id: string) =>
    api.post<ConsoleToken>(`/tenants/${tenantId}/vms/${id}/console`).then((r) => r.data),
  // VM Stats (per-VM resource usage)
  getStats: (tenantId: string, id: string) =>
    api.get<Record<string, unknown>>(`/tenants/${tenantId}/vms/${id}/stats`).then((r) => r.data),
  // Snapshots
  listSnapshots: (tenantId: string, id: string) =>
    api.get<Array<{ name: string; description: string; created_at: number; state: string; is_current: boolean }>>(`/tenants/${tenantId}/vms/${id}/snapshots`).then((r) => r.data),
  createSnapshot: (tenantId: string, id: string, data: { name: string; description?: string }) =>
    api.post(`/tenants/${tenantId}/vms/${id}/snapshots`, data).then((r) => r.data),
  revertSnapshot: (tenantId: string, id: string, snapshotName: string) =>
    api.post(`/tenants/${tenantId}/vms/${id}/snapshots/${snapshotName}/revert`).then((r) => r.data),
  deleteSnapshot: (tenantId: string, id: string, snapshotName: string) =>
    api.delete(`/tenants/${tenantId}/vms/${id}/snapshots/${snapshotName}`).then((r) => r.data),
  clone: (tenantId: string, id: string, data: { name: string; project_id: string; ip_pool_id?: string; cloud_init_identity: { hostname: string; default_user: string; password?: string; ssh_authorized_keys?: string[]; disable_password_login: boolean } }) =>
    api.post<VirtualMachine>(`/tenants/${tenantId}/vms/${id}/clone`, data).then((r) => r.data),
  // Secondary NICs
  addSecondaryNIC: (tenantId: string, vmId: string, poolId: string) =>
    api.post(`/tenants/${tenantId}/vms/${vmId}/secondary-nics`, { pool_id: poolId }).then((r) => r.data),
  removeSecondaryNIC: (tenantId: string, vmId: string, macAddress: string) =>
    api.delete(`/tenants/${tenantId}/vms/${vmId}/secondary-nics/${encodeURIComponent(macAddress)}`).then((r) => r.data),
  // Per-VM Firewall
  getFirewallProfile: (tenantId: string, vmId: string) =>
    api.get<{ vm_id: string; vm_ip: string; bridge: string; exists: boolean; profile: VMFirewallProfile }>(`/tenants/${tenantId}/vms/${vmId}/firewall-profile`).then((r) => r.data),
  updateFirewallProfile: (tenantId: string, vmId: string, data: VMFirewallProfileUpdate) =>
    api.put<{ status: string; vm_id: string; profile: VMFirewallProfile }>(`/tenants/${tenantId}/vms/${vmId}/firewall-profile`, data).then((r) => r.data),
  resetFirewallProfile: (tenantId: string, vmId: string) =>
    api.delete<{ status: string; vm_id: string }>(`/tenants/${tenantId}/vms/${vmId}/firewall-profile`).then((r) => r.data),
  getFirewallLog: (tenantId: string, vmId: string, lines = 50) =>
    api.get<{ vm_id: string; vm_ip: string; blocked_entries: string[]; count: number; log_hint?: string }>(
      `/tenants/${tenantId}/vms/${vmId}/firewall-log`,
      { params: { lines } },
    ).then((r) => r.data),
  // Backups
  listBackups: (tenantId: string, vmId: string) =>
    api.get<{ items: Array<{
      id: string; vm_id: string; name: string; description: string | null;
      backup_type: string; status: string; file_path: string | null;
      file_size_bytes: number | null; vm_name: string | null;
      vm_vcpus: number | null; vm_ram_mb: number | null;
      vm_disk_size_gb: number | null; error_message: string | null;
      created_at: string; updated_at: string;
    }>; total: number }>(`/tenants/${tenantId}/vms/${vmId}/backups`).then((r) => r.data),
  createBackup: (tenantId: string, vmId: string, data: { name: string; description?: string }) =>
    api.post(`/tenants/${tenantId}/vms/${vmId}/backups`, data).then((r) => r.data),
  restoreBackup: (tenantId: string, vmId: string, backupId: string) =>
    api.post(`/tenants/${tenantId}/vms/${vmId}/backups/${backupId}/restore`).then((r) => r.data),
  deleteBackup: (tenantId: string, vmId: string, backupId: string) =>
    api.delete(`/tenants/${tenantId}/vms/${vmId}/backups/${backupId}`).then((r) => r.data),
  // Change IP
  changeIP: (tenantId: string, vmId: string, ipAddress: string) =>
    api.post<{ old_ip: string; new_ip: string; gateway: string; dns: string[] }>(
      `/tenants/${tenantId}/vms/${vmId}/change-ip`,
      { ip_address: ipAddress }
    ).then((r) => r.data),
  // Reset VM guest password (Cloud-Init)
  resetGuestPassword: (
    tenantId: string,
    vmId: string,
    data: { default_user?: string; new_password: string; disable_password_login?: boolean },
  ) =>
    api.post<{ status: string; message: string; reboot_required: boolean }>(
      `/tenants/${tenantId}/vms/${vmId}/reset-password`,
      data,
    ).then((r) => r.data),
  // Disk Management
  listDisks: (tenantId: string, vmId: string) =>
    api.get<DiskItem[]>(`/tenants/${tenantId}/vms/${vmId}/disks`).then((r) => r.data),
  createDisk: (tenantId: string, vmId: string, data: { name: string; size_gb: number; format?: string; storage_pool_id?: string }) =>
    api.post<DiskItem>(`/tenants/${tenantId}/vms/${vmId}/disks`, data).then((r) => r.data),
  detachDisk: (tenantId: string, vmId: string, diskId: string) =>
    api.post<DiskItem>(`/tenants/${tenantId}/vms/${vmId}/disks/${diskId}/detach`).then((r) => r.data),
  attachDisk: (tenantId: string, vmId: string, diskId: string) =>
    api.post<DiskItem>(`/tenants/${tenantId}/vms/${vmId}/disks/${diskId}/attach`).then((r) => r.data),
  resizeDisk: (tenantId: string, vmId: string, diskId: string, sizeGb: number) =>
    api.patch<DiskItem>(`/tenants/${tenantId}/vms/${vmId}/disks/${diskId}/resize`, { size_gb: sizeGb }).then((r) => r.data),
  deleteDisk: (tenantId: string, vmId: string, diskId: string) =>
    api.delete(`/tenants/${tenantId}/vms/${vmId}/disks/${diskId}`).then((r) => r.data),
  // Export / Import
  exportVM: (tenantId: string, vmId: string) =>
    api.post<{ task_id: string; status: string; message: string }>(`/tenants/${tenantId}/vms/${vmId}/export`).then((r) => r.data),
  getExportStatus: (tenantId: string, vmId: string) =>
    api.get<{ task_id: string; status: string; file_name?: string; file_size_bytes?: number; error?: string; created_at?: string; completed_at?: string }>(`/tenants/${tenantId}/vms/${vmId}/export/status`).then((r) => r.data),
  downloadExport: (tenantId: string, vmId: string) =>
    api.get(`/tenants/${tenantId}/vms/${vmId}/export/download`, { responseType: 'blob' }).then((r) => r.data),
  importVM: (tenantId: string, data: { name: string; vcpus?: number; ram_mb?: number; disk_size_gb?: number; project_id?: string; storage_pool_id?: string; bridge?: string }) =>
    api.post<{ vm_id: string; domain_name: string; status: string; upload_url: string }>(`/tenants/${tenantId}/vms/import`, data).then((r) => r.data),
  finalizeImport: (tenantId: string, vmId: string) =>
    api.post<{ task_id: string; status: string; message: string }>(`/tenants/${tenantId}/vms/${vmId}/import/finalize`).then((r) => r.data),
  // Backup Schedules
  listBackupSchedules: (tenantId: string, vmId: string) =>
    api.get<Array<{
      id: string; vm_id: string; tenant_id: string; created_by: string;
      frequency: string; time_utc: string; day_of_week: number | null;
      day_of_month: number | null; retention_count: number; is_active: boolean;
      last_run_at: string | null; next_run_at: string | null; last_status: string | null;
      created_at: string; updated_at: string;
    }>>(`/tenants/${tenantId}/vms/${vmId}/backup-schedules`).then((r) => r.data),
  createBackupSchedule: (tenantId: string, vmId: string, data: {
    frequency: string; time_utc?: string; day_of_week?: number;
    day_of_month?: number; retention_count?: number;
  }) =>
    api.post(`/tenants/${tenantId}/vms/${vmId}/backup-schedules`, data).then((r) => r.data),
  updateBackupSchedule: (tenantId: string, vmId: string, scheduleId: string, data: Record<string, unknown>) =>
    api.patch(`/tenants/${tenantId}/vms/${vmId}/backup-schedules/${scheduleId}`, data).then((r) => r.data),
  deleteBackupSchedule: (tenantId: string, vmId: string, scheduleId: string) =>
    api.delete(`/tenants/${tenantId}/vms/${vmId}/backup-schedules/${scheduleId}`).then((r) => r.data),
};

// --- Storage ---
export const storageApi = {
  listPools: (params?: { tenant_id?: string }) =>
    api.get<StoragePool[]>('/storage-pools', { params }).then((r) => r.data),
  createPool: (data: Partial<StoragePool>) =>
    api.post<StoragePool>('/storage-pools', data).then((r) => r.data),
  updatePool: (id: string, data: Partial<StoragePool>) =>
    api.patch<StoragePool>(`/storage-pools/${id}`, data).then((r) => r.data),
  deletePool: (id: string) => api.delete(`/storage-pools/${id}`),
  listImages: () => api.get<CloudImage[]>('/cloud-images').then((r) => r.data),
  getImage: (id: string) => api.get<CloudImage>(`/cloud-images/${id}`).then((r) => r.data),
  createImage: (data: Partial<CloudImage>) =>
    api.post<CloudImage>('/cloud-images', data).then((r) => r.data),
  deleteImage: (id: string) => api.delete(`/cloud-images/${id}`),
  retryDownload: (id: string) =>
    api.post<CloudImage>(`/cloud-images/${id}/retry-download`).then((r) => r.data),
  uploadImage: (
    formData: FormData,
    onProgress?: (loaded: number, total: number) => void,
  ) =>
    api.post<CloudImage>('/cloud-images/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      // Large ISO uploads can take much longer on slower uplinks.
      timeout: 7200000,  // 2 hours
      onUploadProgress: (event) => {
        onProgress?.(event.loaded, event.total ?? 0);
      },
    }).then((r) => r.data),
};

// --- IPAM / Networks ---
export const ipamApi = {
  listPools: (params?: { tenant_id?: string }) =>
    api.get<IPPool[]>('/ip-pools', { params }).then((r) => r.data),
  listAvailablePublicIps: () =>
    api.get<PublicIpAssignment[]>('/ip-pools/public/available').then((r) => r.data),
  createPool: (data: Record<string, unknown>) =>
    api.post<IPPool>('/ip-pools', data).then((r) => r.data),
  getPool: (id: string) => api.get<IPPool>(`/ip-pools/${id}`).then((r) => r.data),
  updatePool: (id: string, data: Partial<IPPool>) =>
    api.patch<IPPool>(`/ip-pools/${id}`, data).then((r) => r.data),
  deletePool: (id: string) => api.delete(`/ip-pools/${id}`).then((r) => r.data),
  listAllocations: (poolId: string, status?: string) =>
    api.get<IPAllocation[]>(`/ip-pools/${poolId}/allocations`, { params: { allocation_status: status } }).then((r) => r.data),
  allocateIp: (poolId: string, vmId?: string, tenantId?: string) =>
    api.post<IPAllocation>(`/ip-pools/${poolId}/allocate`, null, { params: { vm_id: vmId, tenant_id: tenantId } }).then((r) => r.data),
  releaseIp: (poolId: string, allocationId: string) =>
    api.post<IPAllocation>(`/ip-pools/${poolId}/release/${allocationId}`).then((r) => r.data),
};

/** Tenant self-service NAT / Internal networks */
export const tenantNetworkApi = {
  list: (tenantId: string) =>
    api.get<IPPool[]>(`/tenants/${tenantId}/networks`).then((r) => r.data),
  create: (
    tenantId: string,
    data: {
      name: string;
      pool_type: 'nat' | 'internal';
      description?: string;
      network?: string;
      gateway?: string;
      dns_primary?: string;
      dns_secondary?: string;
      internal_dhcp_enabled?: boolean;
      dhcp_start?: string;
      dhcp_end?: string;
    },
  ) => api.post<IPPool>(`/tenants/${tenantId}/networks`, data).then((r) => r.data),
  update: (
    tenantId: string,
    poolId: string,
    data: {
      name?: string;
      description?: string;
      dns_primary?: string;
      dns_secondary?: string;
      internal_dhcp_enabled?: boolean;
      dhcp_start?: string | null;
      dhcp_end?: string | null;
    },
  ) => api.patch<IPPool>(`/tenants/${tenantId}/networks/${poolId}`, data).then((r) => r.data),
  delete: (tenantId: string, poolId: string) =>
    api.delete(`/tenants/${tenantId}/networks/${poolId}`).then((r) => r.data),
};

// --- Port Forwarding ---
export const portForwardApi = {
  list: (params?: { vm_id?: string; pool_id?: string }) =>
    api.get<PortForward[]>('/port-forwards', { params }).then((r) => r.data),
  create: (data: { vm_id: string; protocol: string; external_port: number; internal_port: number; description?: string }) =>
    api.post<PortForward>('/port-forwards', data).then((r) => r.data),
  delete: (id: string) => api.delete(`/port-forwards/${id}`).then((r) => r.data),
};

// --- Users (Admin) ---
export const userApi = {
  list: () => api.get<User[]>('/users').then((r) => r.data),
  get: (id: string) => api.get<User>(`/users/${id}`).then((r) => r.data),
  listAssignableVMs: () =>
    api.get<Array<{ id: string; name: string; tenant_id: string; assigned_user_id: string | null }>>('/users/assignable-vms').then((r) => r.data),
  listVMAssignments: () =>
    api.get<Array<{ user_id: string; vm_id: string; vm_name: string; tenant_id: string }>>('/users/vm-assignments').then((r) => r.data),
  listAssignableProjects: () =>
    api
      .get<Array<{ id: string; name: string; tenant_id: string; tenant_name: string }>>(
        '/users/assignable-projects',
      )
      .then((r) => r.data),
  listProjectAssignments: () =>
    api
      .get<
        Array<{
          user_id: string;
          project_id: string;
          project_name: string;
          tenant_id: string;
          tenant_name: string;
        }>
      >('/users/project-assignments')
      .then((r) => r.data),
  assignSingleVM: (userId: string, vmId: string | null) =>
    api.put(`/users/${userId}/assigned-vm`, { vm_id: vmId }).then((r) => r.data),
  assignProjects: (userId: string, projectIds: string[]) =>
    api.put(`/users/${userId}/assigned-projects`, { project_ids: projectIds }).then((r) => r.data),
  create: (data: { email: string; username: string; password: string; full_name?: string; is_superadmin?: boolean; tenant_id?: string; tenant_role?: string; max_vms?: number; assigned_vm_ids?: string[] }) =>
    api.post<User>('/users', data).then((r) => r.data),
  update: (id: string, data: Partial<User & { password?: string }>) =>
    api.patch<User>(`/users/${id}`, data).then((r) => r.data),
  deactivate: (id: string) => api.delete(`/users/${id}`),
  delete: (id: string) => api.delete(`/users/${id}`, { params: { permanent: true } }),
};

// --- Tasks ---
export const taskApi = {
  list: (params?: { page?: number; status?: string; tenant_id?: string }) =>
    api.get<TaskListResponse>('/tasks', { params }).then((r) => r.data),
  get: (id: string) => api.get<Task>(`/tasks/${id}`).then((r) => r.data),
  cancel: (id: string) => api.post<Task>(`/tasks/${id}/cancel`).then((r) => r.data),
};

// --- Audit ---
export const auditApi = {
  list: (params?: { page?: number; action?: string; resource_type?: string; tenant_id?: string }) =>
    api.get<AuditLogListResponse>('/audit-logs', { params }).then((r) => r.data),
  export: (format: 'csv' | 'json', params?: { action?: string; resource_type?: string }) =>
    api.get('/audit-logs/export', { params: { format, ...params }, responseType: 'blob' }).then((r) => r.data),
};

// --- API Keys ---
export const apiKeyApi = {
  list: () => api.get<{ items: Array<{ id: string; name: string; description: string | null; key_prefix: string; tenant_id: string | null; is_active: boolean; expires_at: string | null; last_used_at: string | null; created_at: string }>; total: number }>('/api-keys').then((r) => r.data),
  create: (data: { name: string; description?: string; tenant_id?: string; expires_in_days?: number }) =>
    api.post<{ id: string; name: string; description: string | null; key_prefix: string; tenant_id: string | null; is_active: boolean; expires_at: string | null; last_used_at: string | null; created_at: string; api_key: string }>('/api-keys', data).then((r) => r.data),
  revoke: (id: string) => api.delete(`/api-keys/${id}`),
  deletePermanent: (id: string) => api.delete(`/api-keys/${id}/permanent`),
};

// --- System / Debug ---
export const systemApi = {
  generatePassword: (length?: number) => api.get<{ password: string }>('/system/generate-password', { params: { length: length || 16 } }).then((r) => r.data),
  getDebugInfo: () => api.get<Record<string, unknown>>('/system/debug').then((r) => r.data),
  getLogs: (params: { source: string; lines?: number; since_seconds?: number }) =>
    api.get<{
      source: string;
      lines: string[];
      count: number;
      available_sources: string[];
      fetched_at: string;
    }>('/system/logs', { params }).then((r) => r.data),
  retryStuckTasks: () => api.post<{ reset_count: number; message: string }>('/system/retry-stuck-tasks').then((r) => r.data),
  cleanup: () => api.post<{ vms_reset: number; vms_purged: number; tasks_failed: number; images_reset: number; message: string; errors?: string[] }>('/system/cleanup').then((r) => r.data),
  cleanupLibvirt: () => api.post<{ domains_removed: number; domains_kept: number; errors: string[] }>('/system/cleanup-libvirt').then((r) => r.data),
  getNetworks: () => api.get<{ status: string; data: { bridges: string[]; libvirt_networks: { name: string; active: boolean; autostart: boolean }[] } }>('/system/networks').then((r) => r.data),
  // SSL / Domain Management
  getSSLStatus: () => api.get<{ domain: string; ssl_enabled: boolean; cert_exists: boolean; cert_expiry: string | null; cert_path: string }>('/system/ssl/status').then((r) => r.data),
  testSSL: (data: { domain: string; email: string }) => api.post<{ ready: boolean; checks: Array<{ name: string; status: string; detail: string }> }>('/system/ssl/test', data).then((r) => r.data),
  setupSSL: (data: { domain: string; email: string }) => api.post<{ status: string; domain: string; ssl_enabled: boolean; cert_path: string; steps: string[] }>('/system/ssl/setup', data, { timeout: 180000 }).then((r) => r.data),
  regenerateSSLConfig: () => api.post<{ status: string; message: string }>('/system/ssl/regenerate-config').then((r) => r.data),
  disableSSL: () => api.post<{ status: string; ssl_enabled: boolean }>('/system/ssl/disable').then((r) => r.data),
  // Egress Filtering / Firewall
  getEgressRules: () => api.get<{ config: EgressConfig; chain_active: boolean }>('/system/security/egress-rules').then((r) => r.data),
  updateEgressRules: (data: Partial<EgressConfigUpdate>) => api.put<{ status: string; message: string }>('/system/security/egress-rules', data).then((r) => r.data),
  addEgressPort: (data: { port: number; protocol: string; description: string }) => api.post<{ status: string; message: string }>('/system/security/egress-rules/add-port', data).then((r) => r.data),
  removeEgressPort: (port: number, protocol: string) => api.delete<{ status: string; message: string }>('/system/security/egress-rules/remove-port', { params: { port, protocol } }).then((r) => r.data),
  getEgressLog: (lines?: number) => api.get<{ blocked_entries: string[]; count: number }>('/system/security/egress-log', { params: { lines: lines || 50 } }).then((r) => r.data),
  // Ingress Filtering / Public VM protection
  getIngressRules: () => api.get<{ config: IngressConfig; chain_active: boolean }>('/system/security/ingress-rules').then((r) => r.data),
  updateIngressRules: (data: Partial<IngressConfigUpdate>) => api.put<{ status: string; message: string }>('/system/security/ingress-rules', data).then((r) => r.data),
  addIngressPort: (data: { port: number; protocol: string; description: string }) => api.post<{ status: string; message: string }>('/system/security/ingress-rules/add-port', data).then((r) => r.data),
  removeIngressPort: (port: number, protocol: string) => api.delete<{ status: string; message: string }>('/system/security/ingress-rules/remove-port', { params: { port, protocol } }).then((r) => r.data),
  // IP Blacklist / Fail2Ban
  getBlacklist: () => api.get<BlacklistResponse>('/system/security/blacklist').then((r) => r.data),
  banIP: (data: { ip: string; reason: string; permanent?: boolean }) => api.post<{ status: string; message: string }>('/system/security/blacklist/ban', data).then((r) => r.data),
  unbanIP: (ip: string) => api.post<{ status: string; message: string }>('/system/security/blacklist/unban', { ip }).then((r) => r.data),
  checkIP: (ip: string) => api.get<IPCheckResult>(`/system/security/blacklist/check/${ip}`).then((r) => r.data),
  getFail2banLog: (lines?: number) => api.get<{ entries: string[]; count: number }>('/system/security/fail2ban/log', { params: { lines: lines || 50 } }).then((r) => r.data),
  getFail2banConfig: () => api.get<Fail2BanConfigResponse>('/system/security/fail2ban/config').then((r) => r.data),
  updateFail2banConfig: (data: Fail2BanJailUpdate) => api.put<{ status: string; message: string }>('/system/security/fail2ban/config', data).then((r) => r.data),
  // Database backups
  listDbBackups: () =>
    api.get<Array<{
      id: string; name: string; status: string; file_path: string | null;
      file_size_bytes: number | null; notes: string | null; error_message: string | null;
      created_at: string; updated_at: string;
    }>>('/system/db-backups').then((r) => r.data),
  createDbBackup: (data: { name: string; notes?: string }) =>
    api.post('/system/db-backups', data, { timeout: 600000 }).then((r) => r.data),
  downloadDbBackup: (id: string) =>
    api.get(`/system/db-backups/${id}/download`, { responseType: 'blob', timeout: 600000 }).then((r) => r.data as Blob),
  importDbBackup: (file: File, opts: { confirm?: boolean; validate_only?: boolean }) => {
    const form = new FormData();
    form.append('file', file);
    return api.post<{ ok: boolean; validated?: boolean; restored?: boolean; toc_lines?: number; backup_id?: string; detail?: string }>(
      '/system/db-backups/import',
      form,
      {
        params: { confirm: opts.confirm || false, validate_only: opts.validate_only || false },
        timeout: 1800000,
        headers: { 'Content-Type': 'multipart/form-data' },
      },
    ).then((r) => r.data);
  },
  deleteDbBackup: (id: string) => api.delete(`/system/db-backups/${id}`),
  // Global VM backup inventory
  listVmBackupsAdmin: (params?: { backend?: string; status?: string }) =>
    api.get<Array<{
      id: string;
      name: string;
      status: string;
      vm_id: string;
      vm_name: string | null;
      tenant_id: string;
      tenant_name: string | null;
      storage_backend: string;
      file_path: string | null;
      remote_path: string | null;
      has_local: boolean;
      has_remote: boolean;
      locations: string[];
      file_size_bytes: number | null;
      error_message: string | null;
      schedule_id: string | null;
      created_at: string;
      updated_at: string;
    }>>('/system/vm-backups', { params }).then((r) => r.data),
  deleteVmBackupAdmin: (backupId: string) =>
    api.delete<{ status: string; message: string }>(`/system/vm-backups/${backupId}`).then((r) => r.data),
  vmBackupsSummary: () =>
    api.get<{
      total: number;
      by_backend: { internal: number; ftp: number; s3: number; other: number };
      with_local: number;
      with_remote: number;
      ready: number;
      failed: number;
      creating: number;
    }>('/system/vm-backups/summary').then((r) => r.data),
  // VM backup storage backends
  getBackupStorage: () =>
    api.get<{
      backend: 'internal' | 'ftp' | 's3';
      keep_local_copy: boolean;
      ftp: Record<string, unknown>;
      s3: Record<string, unknown>;
    }>('/system/backup-storage').then((r) => r.data),
  updateBackupStorage: (data: Record<string, unknown>) =>
    api.put('/system/backup-storage', data).then((r) => r.data),
  testBackupStorage: (data?: Record<string, unknown>) =>
    api.post<{ ok: boolean; backend: string; message: string }>('/system/backup-storage/test', data || null).then((r) => r.data),
};

export interface Fail2BanJailConfig {
  name: string;
  bantime: number | null;
  findtime: number | null;
  maxretry: number | null;
  currently_banned?: number;
  total_banned?: number;
  currently_failed?: number;
}

export interface Fail2BanConfigResponse {
  jails: Record<string, Fail2BanJailConfig>;
}

export interface Fail2BanJailUpdate {
  jail: string;
  bantime?: number;
  findtime?: number;
  maxretry?: number;
}

export interface EgressPortRule {
  port: number;
  protocol: string;
  description: string;
}

export interface VMFirewallPortRule {
  port: number;
  protocol: string;
  description: string;
}

export interface VMFirewallDirection {
  enabled: boolean;
  allowed_ports: VMFirewallPortRule[];
}

export interface VMFirewallProfile {
  vm_ip?: string;
  bridge?: string;
  inbound: VMFirewallDirection;
  outbound: VMFirewallDirection;
}

export interface VMFirewallProfileUpdate {
  bridge?: string;
  inbound?: VMFirewallDirection;
  outbound?: VMFirewallDirection;
}

export interface EgressConfig {
  enabled: boolean;
  rate_limit: { connections_per_sec: number; burst: number };
  allowed_ports: EgressPortRule[];
}

export interface EgressConfigUpdate {
  enabled?: boolean;
  allowed_ports?: EgressPortRule[];
  rate_limit_per_sec?: number;
  rate_limit_burst?: number;
}

export interface IngressPortRule {
  port: number;
  protocol: string;
  description: string;
}

export interface IngressConfig {
  enabled: boolean;
  interfaces: string[];
  allowed_ports: IngressPortRule[];
}

export interface IngressConfigUpdate {
  enabled?: boolean;
  allowed_ports?: IngressPortRule[];
  interfaces?: string[];
}

export interface BlacklistEntry {
  ip: string;
  reason?: string;
  permanent?: boolean;
  banned_at?: string;
}

export interface Fail2BanBan {
  ip: string;
  jail: string;
  source: string;
}

export interface Fail2BanJailStats {
  name: string;
  currently_banned: number;
  total_banned: number;
  currently_failed: number;
}

export interface BlacklistResponse {
  manual_bans: BlacklistEntry[];
  fail2ban_bans: Fail2BanBan[];
  fail2ban_stats: { running: boolean; jails: Fail2BanJailStats[] };
  total: number;
}

export interface IPCheckResult {
  ip: string;
  banned: boolean;
  in_iptables: boolean;
  in_fail2ban: boolean;
  fail2ban_jail: string | null;
  manual_entry: BlacklistEntry | null;
}

// --- Load Balancer ---
export interface LBTarget {
  id: string;
  vm_id: string;
  vm_name: string | null;
  vm_ip: string | null;
  ip_mode: 'nat' | 'public';
  preferred_ip?: string | null;
  available_ips?: { nat: string[]; public: string[] } | null;
  target_port: number;
  weight: number;
  is_active: boolean;
  created_at: string;
}

export interface LBIpChoices {
  nat: string[];
  public_on_vm: string[];
  public_free: string[];
  public_choices: string[];
}

export interface LoadBalancerItem {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  protocol: 'http' | 'https' | 'tcp';
  algorithm: 'round_robin' | 'least_conn' | 'ip_hash';
  listen_port: number;
  /** One public VIP in front of all NAT backends */
  public_ip: string | null;
  health_check_path: string | null;
  health_check_interval: number;
  is_active: boolean;
  targets: LBTarget[];
  created_at: string;
  updated_at: string;
}

export const loadBalancerApi = {
  list: () =>
    api.get<LoadBalancerItem[]>('/load-balancers').then((r) => r.data),
  get: (id: string) =>
    api.get<LoadBalancerItem>(`/load-balancers/${id}`).then((r) => r.data),
  create: (data: {
    name: string; description?: string; protocol?: string; algorithm?: string;
    listen_port: number; public_ip?: string; health_check_path?: string; health_check_interval?: number;
    targets?: Array<{ vm_id: string; target_port: number; weight?: number }>;
  }) =>
    api.post<LoadBalancerItem>('/load-balancers', data).then((r) => r.data),
  update: (id: string, data: Partial<{
    name: string; description: string; algorithm: string; public_ip: string;
    health_check_path: string; health_check_interval: number; is_active: boolean;
  }>) =>
    api.patch<LoadBalancerItem>(`/load-balancers/${id}`, data).then((r) => r.data),
  delete: (id: string) =>
    api.delete(`/load-balancers/${id}`).then((r) => r.data),
  addTarget: (lbId: string, data: {
    vm_id: string; target_port: number; weight?: number;
  }) =>
    api.post<LBTarget>(`/load-balancers/${lbId}/targets`, data).then((r) => r.data),
  updateTarget: (lbId: string, targetId: string, data: {
    target_port?: number; weight?: number; is_active?: boolean;
  }) =>
    api.patch<LBTarget>(`/load-balancers/${lbId}/targets/${targetId}`, data).then((r) => r.data),
  removeTarget: (lbId: string, targetId: string) =>
    api.delete(`/load-balancers/${lbId}/targets/${targetId}`).then((r) => r.data),
  ipChoices: (vmId?: string) =>
    api.get<LBIpChoices>('/load-balancers/ip-choices', { params: vmId ? { vm_id: vmId } : {} }).then((r) => r.data),
};

// --- Cluster ---
export interface ClusterNodeItem {
  id: string;
  name: string;
  description: string | null;
  url: string;
  api_key_prefix: string;
  status: 'online' | 'offline' | 'maintenance' | 'unknown';
  is_active: boolean;
  last_seen_at: string | null;
  last_error: string | null;
  total_vms: number;
  running_vms: number;
  total_vcpus: number;
  total_ram_mb: number;
  used_vcpus: number;
  used_ram_mb: number;
  total_storage_gb: number;
  used_storage_gb: number;
  app_version: string | null;
  created_at: string;
  updated_at: string;
}

export interface ClusterVMItem {
  id: string;
  name: string;
  hostname: string;
  status: string;
  vcpus: number;
  ram_mb: number;
  disk_size_gb: number;
  node_name: string;
  node_id: string;
}

export interface ClusterOverview {
  total_nodes: number;
  online_nodes: number;
  total_vms: number;
  running_vms: number;
  total_vcpus: number;
  used_vcpus: number;
  total_ram_mb: number;
  used_ram_mb: number;
  total_storage_gb: number;
  used_storage_gb: number;
  nodes: ClusterNodeItem[];
  vms: ClusterVMItem[];
}

export const clusterApi = {
  listNodes: () =>
    api.get<ClusterNodeItem[]>('/cluster/nodes').then((r) => r.data),
  addNode: (data: { name: string; description?: string; url: string; api_key: string }) =>
    api.post<ClusterNodeItem>('/cluster/nodes', data).then((r) => r.data),
  updateNode: (id: string, data: Partial<{ name: string; description: string; url: string; api_key: string; is_active: boolean }>) =>
    api.patch<ClusterNodeItem>(`/cluster/nodes/${id}`, data).then((r) => r.data),
  removeNode: (id: string) =>
    api.delete(`/cluster/nodes/${id}`).then((r) => r.data),
  syncNode: (id: string) =>
    api.post<ClusterNodeItem>(`/cluster/nodes/${id}/sync`).then((r) => r.data),
  syncAll: () =>
    api.post<{ synced: number; errors: number; total: number }>('/cluster/sync-all').then((r) => r.data),
  overview: () =>
    api.get<ClusterOverview>('/cluster/overview').then((r) => r.data),
};

// --- Reverse Proxy ---
export const proxyApi = {
  list: () =>
    api.get<{ items: ReverseProxyRule[]; total: number }>('/reverse-proxy').then((r) => r.data),
  create: (data: { domain: string; target_ip: string; target_port: number; vm_id?: string; ssl_mode?: string; websocket_support?: boolean }) =>
    api.post<ReverseProxyRule>('/reverse-proxy', data).then((r) => r.data),
  update: (id: string, data: Partial<{ target_ip: string; target_port: number; vm_id: string; ssl_mode: string; websocket_support: boolean; is_active: boolean }>) =>
    api.patch<ReverseProxyRule>(`/reverse-proxy/${id}`, data).then((r) => r.data),
  delete: (id: string) =>
    api.delete(`/reverse-proxy/${id}`).then((r) => r.data),
  requestSSL: (id: string) =>
    api.post<{ status: string; domain: string; message: string }>(`/reverse-proxy/${id}/ssl`).then((r) => r.data),
};

// --- Notifications ---
export const notificationApi = {
  getPreferences: () =>
    api.get<{
      notify_vm_error: boolean;
      notify_vm_created: boolean;
      notify_backup_completed: boolean;
      notify_backup_failed: boolean;
      notify_quota_warning: boolean;
      notify_login_new_ip: boolean;
      notify_2fa_changed: boolean;
    }>('/notifications/preferences').then((r) => r.data),
  updatePreferences: (data: Partial<{
    notify_vm_error: boolean;
    notify_vm_created: boolean;
    notify_backup_completed: boolean;
    notify_backup_failed: boolean;
    notify_quota_warning: boolean;
    notify_login_new_ip: boolean;
    notify_2fa_changed: boolean;
  }>) =>
    api.patch('/notifications/preferences', data).then((r) => r.data),
  getSMTPStatus: () =>
    api.get<{
      enabled: boolean;
      host: string;
      port: number;
      from_email: string;
      from_name: string;
      use_tls: boolean;
      allow_invalid_cert: boolean;
      configured: boolean;
    }>('/notifications/smtp').then((r) => r.data),
  updateSMTP: (data: Partial<{
    smtp_host: string;
    smtp_port: number;
    smtp_user: string;
    smtp_password: string;
    smtp_from_email: string;
    smtp_from_name: string;
    smtp_use_tls: boolean;
    smtp_allow_invalid_cert: boolean;
    smtp_enabled: boolean;
  }>) =>
    api.patch('/notifications/smtp', data).then((r) => r.data),
  testSMTP: (email: string) =>
    api.post<{ status: string; message: string }>('/notifications/smtp/test', { email }).then((r) => r.data),
};

// --- VM Templates ---
export interface VMTemplateItem {
  id: string;
  tenant_id: string | null;
  created_by: string;
  name: string;
  description: string | null;
  icon: string;
  vcpus: number;
  ram_mb: number;
  disk_size_gb: number;
  cloud_image_id: string | null;
  default_user: string;
  packages: string[] | null;
  runcmd: string[] | null;
  timezone: string;
  keyboard_layout: string;
  locale: string | null;
  ssh_keys: string[] | null;
  is_global: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export const vmTemplateApi = {
  list: () =>
    api.get<VMTemplateItem[]>('/vm-templates').then((r) => r.data),
  get: (id: string) =>
    api.get<VMTemplateItem>(`/vm-templates/${id}`).then((r) => r.data),
  create: (data: Partial<VMTemplateItem>) =>
    api.post<VMTemplateItem>('/vm-templates', data).then((r) => r.data),
  update: (id: string, data: Partial<VMTemplateItem>) =>
    api.patch<VMTemplateItem>(`/vm-templates/${id}`, data).then((r) => r.data),
  delete: (id: string) =>
    api.delete(`/vm-templates/${id}`).then((r) => r.data),
};

export default api;
