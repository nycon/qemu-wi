// --- Auth ---
export interface LoginRequest {
  username: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  requires_2fa?: boolean;
  temp_token?: string;
}

// --- User ---
export interface UserSshKey {
  id: string;
  name: string;
  public_key: string;
  fingerprint: string;
  created_at: string;
}

export interface User {
  id: string;
  email: string;
  username: string;
  full_name: string | null;
  is_active: boolean;
  is_superadmin: boolean;
  totp_enabled?: boolean;
  ssh_keys?: UserSshKey[];
  created_at: string;
  updated_at: string;
}

// --- Tenant ---
export interface Tenant {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  is_active: boolean;
  max_vms: number;
  max_vcpus: number;
  max_ram_mb: number;
  max_storage_gb: number;
  max_ips: number;
  notification_email: string | null;
  created_at: string;
  updated_at: string;
}

// --- Project ---
export interface Project {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

// --- VM ---
export type VMStatus =
  | 'creating'
  | 'stopped'
  | 'starting'
  | 'running'
  | 'stopping'
  | 'rebooting'
  | 'error'
  | 'deleting'
  | 'deleted';

export interface VirtualMachine {
  id: string;
  project_id: string;
  tenant_id: string;
  created_by: string;
  name: string;
  hostname: string;
  description: string | null;
  status: VMStatus;
  vcpus: number;
  ram_mb: number;
  disk_size_gb: number;
  disk_format: string;
  mac_address: string | null;
  bridge: string;
  vnc_port: number | null;
  vnc_websocket_port: number | null;
  template_id: string | null;
  storage_pool_id: string | null;
  cloud_init_network: Record<string, unknown> | null;
  secondary_interfaces: Array<{
    mac_address: string;
    network_name: string;
    pool_id: string;
    pool_name: string;
    subnet: string;
    dhcp_enabled?: boolean;
  }> | null;
  os_profile?: 'linux' | 'windows' | 'other';
  install_source?: 'cloud' | 'iso';
  image_name?: string | null;
  os_type?: string | null;
  os_version?: string | null;
  created_at: string;
  updated_at: string;
}

export interface VMListResponse {
  items: VirtualMachine[];
  total: number;
  page: number;
  page_size: number;
}

// --- Cloud Image ---
export interface CloudImage {
  id: string;
  name: string;
  os_type: string;
  os_version: string;
  architecture: string;
  format: string;
  file_path: string;
  file_size_bytes: number;
  checksum_sha256: string | null;
  is_active: boolean;
  is_public: boolean;
  description: string | null;
  min_vcpus: number;
  min_ram_mb: number;
  min_disk_gb: number;
  storage_pool_id: string | null;
  download_url: string | null;
  default_userdata_yaml: string | null;
  download_status: string;
  download_progress: number;
  download_error: string | null;
  created_at: string;
  updated_at: string;
}

// --- Storage Pool ---
export interface StoragePool {
  id: string;
  name: string;
  pool_type: string;
  path: string;
  total_size_gb: number;
  used_size_gb: number;
  free_size_gb: number;
  is_active: boolean;
  is_default: boolean;
  description: string | null;
  /** null = shared/global; set = exclusive to that tenant */
  tenant_id: string | null;
  created_at: string;
  updated_at: string;
}

// --- IP Pool / Network ---
export type PoolType = 'nat' | 'external' | 'internal';

export interface IPPool {
  id: string;
  name: string;
  description: string | null;
  pool_type: PoolType;
  ip_version: string;
  network: string;
  gateway: string;
  dns_primary: string;
  dns_secondary: string | null;
  vlan_id: number | null;
  bridge: string;
  internal_dhcp_enabled: boolean;
  dhcp_start: string | null;
  dhcp_end: string | null;
  libvirt_network_name: string | null;
  /** null = shared/global; set = tenant-owned */
  tenant_id: string | null;
  is_active: boolean;
  total_ips: number;
  allocated_ips: number;
  available_ips: number;
  created_at: string;
  updated_at: string;
}

// --- Port Forwarding ---
export interface PortForward {
  id: string;
  pool_id: string;
  vm_id: string;
  protocol: string;
  external_port: number;
  internal_ip: string;
  internal_port: number;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

// --- Task ---
export type TaskStatus = 'queued' | 'running' | 'success' | 'failed' | 'cancelled' | 'retrying';

export interface Task {
  id: string;
  task_type: string;
  status: TaskStatus;
  user_id: string | null;
  tenant_id: string | null;
  resource_type: string | null;
  resource_id: string | null;
  progress_percent: number | null;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  logs: string | null;
  retry_count: number;
  max_retries: number;
  created_at: string;
  updated_at: string;
}

export interface TaskListResponse {
  items: Task[];
  total: number;
  page: number;
  page_size: number;
}

// --- Audit ---
export interface AuditLog {
  id: string;
  timestamp: string;
  actor_id: string | null;
  actor_email: string | null;
  actor_ip: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  tenant_id: string | null;
  description: string | null;
  metadata_json: Record<string, unknown> | null;
  status: string;
}

export interface AuditLogListResponse {
  items: AuditLog[];
  total: number;
  page: number;
  page_size: number;
}

// --- IP Allocation ---
export interface IPAllocation {
  id: string;
  pool_id: string;
  ip_address: string;
  status: string;
  vm_id: string | null;
  tenant_id: string | null;
  description: string | null;
  created_at: string;
  updated_at: string;
}

// --- Reverse Proxy ---
export interface ReverseProxyRule {
  id: string;
  tenant_id: string;
  vm_id: string | null;
  domain: string;
  target_ip: string;
  target_port: number;
  ssl_mode: string;
  ssl_enabled: boolean;
  websocket_support: boolean;
  is_active: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
}

// --- Console ---
export interface ConsoleToken {
  token: string;
  websocket_url: string;
  expires_in: number;
}
