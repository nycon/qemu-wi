import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useTenantStore } from '@/store/tenantStore';
import {
  vmApi,
  storageApi,
  projectApi,
  ipamApi,
  vmTemplateApi,
  systemApi,
  authApi,
  tenantApi,
  type VMTemplateItem,
  type PublicIpAssignment,
} from '@/services/api';
import type { CloudImage, IPPool, Project, StoragePool, UserSshKey } from '@/types';
import {
  ServerStackIcon,
  CircleStackIcon,
  GlobeAltIcon,
  CpuChipIcon,
  CommandLineIcon,
  CloudIcon,
  KeyIcon,
  EyeIcon,
  EyeSlashIcon,
} from '@heroicons/react/24/outline';

export function VMCreatePage() {
  const { currentTenant } = useTenantStore();
  const navigate = useNavigate();
  const [images, setImages] = useState<CloudImage[]>([]);
  const [isoImages, setIsoImages] = useState<CloudImage[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [ipPools, setIpPools] = useState<IPPool[]>([]);
  const [publicIps, setPublicIps] = useState<PublicIpAssignment[]>([]);
  const [storagePools, setStoragePools] = useState<StoragePool[]>([]);
  const [vmTemplates, setVmTemplates] = useState<VMTemplateItem[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [savedSshKeys, setSavedSshKeys] = useState<UserSshKey[]>([]);
  /** '' = none, '__manual__' = paste new, '__all__' = all profile keys, else key id */
  const [sshKeyChoice, setSshKeyChoice] = useState<string>('');

  const isPasswordValid = (pw: string) =>
    pw.length >= 8 &&
    /[A-Z]/.test(pw) &&
    /[a-z]/.test(pw) &&
    /[0-9]/.test(pw) &&
    /[!@#$%^&*()_+\-=\[\]{}|;:',.<>?/`~]/.test(pw);

  const isVirtioIso = (img: CloudImage) => {
    const hint = `${img.name} ${img.description ?? ''} ${img.os_version ?? ''}`.toLowerCase();
    return hint.includes('virtio');
  };

  const isWindowsHint = (img?: CloudImage | null) => {
    if (!img) return false;
    const os = (img.os_type ?? '').toLowerCase();
    const name = (img.name ?? '').toLowerCase();
    // Avoid bare "win"/"server" matches (e.g. Darwin, Ubuntu Server).
    return (
      os === 'windows' ||
      name.includes('windows') ||
      /\bwin(?:dows)?(?:\s*|-)(?:10|11|server)\b/.test(name) ||
      /\bwindows\s+server\b/.test(name)
    );
  };

  const handleGeneratePassword = async () => {
    try {
      const { password } = await systemApi.generatePassword(16);
      setForm((prev) => ({ ...prev, password }));
      setShowPassword(true);
      toast.success('Password generated');
    } catch {
      toast.error('Failed to generate password');
    }
  };

  const handleCustomUserDataFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      setForm((prev) => ({ ...prev, custom_userdata_yaml: text }));
      toast.success('Custom user-data loaded');
    } catch {
      toast.error('Could not read YAML file');
    } finally {
      e.target.value = '';
    }
  };

  // Form state
  const [form, setForm] = useState({
    install_source: 'cloud' as 'cloud' | 'iso',
    os_profile: 'linux' as 'linux' | 'windows' | 'other',
    name: '',
    description: '',
    project_id: '',
    template_id: '',
    iso_id: '',
    virtio_iso_id: '',
    vcpus: 2,
    ram_mb: 2048,
    disk_size_gb: 20,
    hostname: '',
    default_user: 'cloud',
    ssh_keys: '',
    password: '',
    disable_password_login: false,
    ip_pool_id: '',
    ip_allocation_id: '',
    secondary_pool_ids: [] as string[],
    storage_pool_id: '',
    packages: '',
    runcmd: '',
    timezone: 'Europe/Berlin',
    keyboard_layout: 'de',
    locale: '',
    custom_userdata_yaml: '',
  });

  useEffect(() => {
    const loadData = async () => {
      // Load independently so a missing project/pool never hides shared images
      const [imgResult, projResult, poolResult, storResult, tplResult, publicIpResult] =
        await Promise.allSettled([
          storageApi.listImages(),
          currentTenant ? projectApi.list(currentTenant.id) : Promise.resolve([] as Project[]),
          ipamApi.listPools(currentTenant ? { tenant_id: currentTenant.id } : undefined),
          storageApi.listPools(currentTenant ? { tenant_id: currentTenant.id } : undefined),
          vmTemplateApi.list(),
          currentTenant
            ? tenantApi.listPublicIps(currentTenant.id)
            : Promise.resolve([] as PublicIpAssignment[]),
        ]);

      if (imgResult.status === 'fulfilled') {
        const readyImages = imgResult.value.filter((img) => img.download_status === 'ready');
        setImages(readyImages.filter((img) => String(img.format).toLowerCase() !== 'iso'));
        setIsoImages(readyImages.filter((img) => String(img.format).toLowerCase() === 'iso'));
      } else {
        setImages([]);
        setIsoImages([]);
        toast.error('Cloud images could not be loaded');
      }

      // Load profile SSH keys for dropdown (do not dump raw keys into the textarea)
      try {
        const me = await authApi.me();
        const keys = me.ssh_keys || [];
        setSavedSshKeys(keys);
        if (keys.length === 1) setSshKeyChoice(keys[0].id);
        else if (keys.length > 1) setSshKeyChoice('__all__');
      } catch { /* ignore */ }

      if (projResult.status === 'fulfilled') {
        const projData = projResult.value;
        setProjects(projData);
        setForm((prev) => ({
          ...prev,
          project_id: prev.project_id || (projData.length === 1 ? projData[0].id : ''),
        }));
      } else {
        setProjects([]);
      }

      const assignedPublicIps =
        publicIpResult.status === 'fulfilled' ? publicIpResult.value : [];
      setPublicIps(assignedPublicIps);

      if (poolResult.status === 'fulfilled') {
        const maxPublic = currentTenant?.max_ips ?? 0;
        const freePublicCount = assignedPublicIps.filter((ip) => ip.status === 'reserved').length;
        setIpPools(
          poolResult.value.filter((p) => {
            if (!p.is_active || p.available_ips <= 0) return false;
            // Hide public/external pools when tenant has no free reserved public IPs
            if (p.pool_type === 'external' && (maxPublic <= 0 || freePublicCount <= 0)) return false;
            return true;
          }),
        );
      } else {
        setIpPools([]);
      }

      if (storResult.status === 'fulfilled') {
        const activePools = storResult.value.filter((p) => p.is_active);
        setStoragePools(activePools);
        const defaultPool = activePools.find((p) => p.is_default);
        if (defaultPool) {
          setForm((prev) => ({ ...prev, storage_pool_id: prev.storage_pool_id || defaultPool.id }));
        }
      } else {
        setStoragePools([]);
      }

      if (tplResult.status === 'fulfilled') {
        setVmTemplates(tplResult.value.filter((t) => t.is_active));
      } else {
        setVmTemplates([]);
      }
    };
    loadData();
  }, [currentTenant]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const { name, value, type } = e.target;
    setForm((prev) => {
      if (type === 'checkbox') {
        const checked = (e.target as HTMLInputElement).checked;
        return { ...prev, [name]: checked } as typeof prev;
      }
      if (name === 'iso_id' && prev.install_source === 'iso') {
        const selectedIso = isoImages.find((img) => img.id === value);
        const nextProfile = isWindowsHint(selectedIso) ? 'windows' : prev.os_profile === 'windows' ? 'linux' : prev.os_profile;
        const virtioAuto =
          nextProfile === 'windows'
            ? (isoImages.find((img) => img.id !== value && isVirtioIso(img))?.id || prev.virtio_iso_id)
            : '';
        return {
          ...prev,
          iso_id: value,
          os_profile: nextProfile,
          virtio_iso_id: nextProfile === 'windows' ? virtioAuto : '',
          vcpus: nextProfile === 'windows' ? Math.max(prev.vcpus, 2) : prev.vcpus,
          ram_mb: nextProfile === 'windows' ? Math.max(prev.ram_mb, 4096) : prev.ram_mb,
          disk_size_gb: nextProfile === 'windows' ? Math.max(prev.disk_size_gb, 60) : prev.disk_size_gb,
        };
      }
      if (name === 'os_profile' && prev.install_source === 'iso') {
        const profile = value as 'linux' | 'windows' | 'other';
        if (profile === 'windows') {
          const virtioAuto =
            isoImages.find((img) => img.id !== prev.iso_id && isVirtioIso(img))?.id || prev.virtio_iso_id;
          return {
            ...prev,
            os_profile: profile,
            virtio_iso_id: virtioAuto,
            vcpus: Math.max(prev.vcpus, 2),
            ram_mb: Math.max(prev.ram_mb, 4096),
            disk_size_gb: Math.max(prev.disk_size_gb, 60),
          };
        }
        return { ...prev, os_profile: profile, virtio_iso_id: '' };
      }
      if (name === 'network_choice') {
        if (value.startsWith('alloc:')) {
          const allocId = value.slice('alloc:'.length);
          const ip = publicIps.find((a) => a.id === allocId);
          return {
            ...prev,
            ip_pool_id: ip?.pool_id || '',
            ip_allocation_id: allocId,
            secondary_pool_ids: prev.secondary_pool_ids.filter((id) => id !== ip?.pool_id),
          };
        }
        if (value.startsWith('pool:')) {
          const poolId = value.slice('pool:'.length);
          return {
            ...prev,
            ip_pool_id: poolId,
            ip_allocation_id: '',
            secondary_pool_ids: prev.secondary_pool_ids.filter((id) => id !== poolId),
          };
        }
        return { ...prev, ip_pool_id: '', ip_allocation_id: '' };
      }
      if (name === 'install_source') {
        const source = value as 'cloud' | 'iso';
        if (source === 'cloud') {
          return {
            ...prev,
            install_source: source,
            os_profile: 'linux',
            iso_id: '',
            virtio_iso_id: '',
          };
        }
        if (source === 'iso' && prev.iso_id) {
          const selectedIso = isoImages.find((img) => img.id === prev.iso_id);
          const nextProfile = isWindowsHint(selectedIso) ? 'windows' : 'linux';
          const virtioAuto =
            nextProfile === 'windows'
              ? (isoImages.find((img) => img.id !== prev.iso_id && isVirtioIso(img))?.id || '')
              : '';
          return {
            ...prev,
            install_source: source,
            os_profile: nextProfile,
            virtio_iso_id: virtioAuto,
          };
        }
        return { ...prev, install_source: source, os_profile: 'linux', virtio_iso_id: '' };
      }
      return {
        ...prev,
        [name]: value,
      };
    });
  };

  const toggleSecondaryPool = (poolId: string) => {
    setForm((prev) => {
      const exists = prev.secondary_pool_ids.includes(poolId);
      if (exists) {
        return {
          ...prev,
          secondary_pool_ids: prev.secondary_pool_ids.filter((id) => id !== poolId),
        };
      }
      if (prev.secondary_pool_ids.length >= 4) {
        toast.error('Maximum 4 additional interfaces allowed');
        return prev;
      }
      return {
        ...prev,
        secondary_pool_ids: [...prev.secondary_pool_ids, poolId],
      };
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentTenant) return;

    const isCloudInstall = form.install_source === 'cloud';
    const isWindowsInstall = form.install_source === 'iso' && form.os_profile === 'windows';

    if (isCloudInstall && form.password && !isPasswordValid(form.password)) {
      toast.error('Password does not meet requirements: min. 8 chars, uppercase, lowercase, digit, special character');
      return;
    }
    if (form.install_source === 'iso' && !form.iso_id) {
      toast.error('Please select an installation ISO');
      return;
    }

    setIsSubmitting(true);
    try {
      const hostname = form.hostname || form.name.toLowerCase().replace(/[^a-z0-9-]/g, '-');
      const resolveSshKeys = (): string[] => {
        if (sshKeyChoice === '__manual__') {
          return form.ssh_keys.split('\n').map((k) => k.trim()).filter(Boolean);
        }
        if (sshKeyChoice === '__all__') {
          return savedSshKeys.map((k) => k.public_key).filter(Boolean);
        }
        if (sshKeyChoice) {
          const one = savedSshKeys.find((k) => k.id === sshKeyChoice);
          return one?.public_key ? [one.public_key] : [];
        }
        return [];
      };
      const payload: Record<string, unknown> = {
        name: form.name,
        description: form.description || undefined,
        project_id: form.project_id,
        template_id: form.install_source === 'iso' ? form.iso_id : form.template_id,
        os_profile: form.install_source === 'iso' ? form.os_profile : 'linux',
        vcpus: form.vcpus,
        ram_mb: form.ram_mb,
        disk_size_gb: form.disk_size_gb,
        // Backend schema still expects identity; keep minimal for ISO/Windows.
        cloud_init_identity: isCloudInstall
          ? {
              hostname,
              default_user: form.default_user,
              password: form.password || undefined,
              ssh_authorized_keys: resolveSshKeys(),
              disable_password_login: form.disable_password_login,
            }
          : {
              hostname,
              default_user: isWindowsInstall ? 'Administrator' : form.default_user || 'cloud',
              ssh_authorized_keys: [],
              disable_password_login: false,
            },
      };

      if (isCloudInstall) {
        payload.cloud_init_userdata = {
          // qemu-guest-agent is always injected by the backend for Linux cloud images
          packages: form.packages
            .split(',')
            .map((p) => p.trim())
            .filter((p) => p && p !== 'qemu-guest-agent'),
          runcmd: form.runcmd
            .split('\n')
            .map((c) => c.trim())
            .filter((c) => c && !c.includes('qemu-guest-agent')),
          timezone: form.timezone,
          keyboard_layout: form.keyboard_layout || undefined,
          locale: form.locale || undefined,
        };
        if (form.custom_userdata_yaml.trim()) {
          payload.custom_userdata_yaml = form.custom_userdata_yaml;
        }
      }

      if (form.install_source === 'iso') {
        payload.cloud_init_userdata = undefined;
        payload.cloud_init_metadata = undefined;
        payload.cloud_init_network = undefined;
        if (isWindowsInstall) {
          payload.virtio_iso_id = form.virtio_iso_id || undefined;
        }
      }

      // Network: pool required; optional specific public IP allocation
      payload.ip_pool_id = form.ip_pool_id;
      if (form.ip_allocation_id) {
        payload.ip_allocation_id = form.ip_allocation_id;
      }
      if (form.secondary_pool_ids.length > 0) {
        payload.secondary_pool_ids = form.secondary_pool_ids;
      }

      // Storage pool (optional)
      if (form.storage_pool_id) {
        payload.storage_pool_id = form.storage_pool_id;
      }

      await vmApi.create(currentTenant.id, payload);
      toast.success('VM is being created');
      navigate('/vms');
    } catch (error: unknown) {
      const msg = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'VM creation failed';
      toast.error(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!currentTenant) {
    return <div className="text-center py-12 text-gray-500 dark:text-gray-400">Please select a tenant.</div>;
  }

  const internalPools = ipPools.filter((p) => p.pool_type === 'internal');
  const freePublicIpOptions = publicIps
    .filter((ip) => ip.status === 'reserved')
    .slice()
    .sort((a, b) => a.ip_address.localeCompare(b.ip_address, undefined, { numeric: true }));

  const privatePools = ipPools.filter((p) => p.pool_type !== 'external');

  const networkChoiceValue = form.ip_allocation_id
    ? `alloc:${form.ip_allocation_id}`
    : form.ip_pool_id
      ? `pool:${form.ip_pool_id}`
      : '';

  const hasNetworkOptions = freePublicIpOptions.length > 0 || privatePools.length > 0;
  const availableSecondaryPools = internalPools.filter((p) => p.id !== form.ip_pool_id);
  const isCloudInstall = form.install_source === 'cloud';
  const isIsoInstall = form.install_source === 'iso';
  const isWindowsInstall = isIsoInstall && form.os_profile === 'windows';
  const installerIsos = isoImages.filter((img) => !isVirtioIso(img));
  const virtioIsos = isoImages.filter((img) => isVirtioIso(img));

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">
          Create VM
        </h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Create a new VM from a cloud image or installation ISO.
        </p>
      </div>

      {/* Quick Start Templates — cloud/Linux only */}
      {isCloudInstall && vmTemplates.length > 0 && (
        <div className="mb-8">
          <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">Quick Start — Choose a Preset</h2>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            {vmTemplates.map((t) => {
              const iconMap: Record<string, typeof ServerStackIcon> = {
                server: ServerStackIcon, database: CircleStackIcon, web: GlobeAltIcon,
                compute: CpuChipIcon, dev: CommandLineIcon, cloud: CloudIcon,
              };
              const IconComp = iconMap[t.icon] || ServerStackIcon;
              const formatRam = (mb: number) => mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 === 0 ? 0 : 1)} GB` : `${mb} MB`;
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => {
                    setForm((prev) => ({
                      ...prev,
                      vcpus: t.vcpus,
                      ram_mb: t.ram_mb,
                      disk_size_gb: t.disk_size_gb,
                      template_id: t.cloud_image_id || prev.template_id,
                      default_user: t.default_user,
                      packages: (t.packages || [])
                        .filter((p) => p !== 'qemu-guest-agent')
                        .join(', '),
                      runcmd: (t.runcmd || [])
                        .filter((c) => !c.includes('qemu-guest-agent'))
                        .join('\n'),
                      timezone: t.timezone,
                      keyboard_layout: t.keyboard_layout,
                      locale: t.locale || '',
                      ssh_keys: (t.ssh_keys || []).join('\n'),
                    }));
                    if ((t.ssh_keys || []).length) setSshKeyChoice('__manual__');
                    toast.success(`Template "${t.name}" applied`);
                  }}
                  className="text-left p-4 rounded-xl border-2 border-gray-200 dark:border-gray-700 hover:border-primary-400 dark:hover:border-primary-500 hover:shadow-md transition-all bg-white dark:bg-gray-800/50 group"
                >
                  <div className="flex items-center gap-2.5 mb-2">
                    <div className="p-1.5 rounded-lg bg-primary-50 dark:bg-primary-900/20 group-hover:bg-primary-100 dark:group-hover:bg-primary-900/30 transition-colors">
                      <IconComp className="h-5 w-5 text-primary-600 dark:text-primary-400" />
                    </div>
                    <span className="text-sm font-semibold text-gray-900 dark:text-white">{t.name}</span>
                  </div>
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {t.vcpus} vCPU · {formatRam(t.ram_mb)} · {t.disk_size_gb} GB
                  </p>
                  {t.description && (
                    <p className="text-xs text-gray-400 dark:text-gray-500 mt-1 line-clamp-1">{t.description}</p>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-8">
        {/* Basic Info */}
        <div className="card p-6">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            Basic Settings
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">VM Name *</label>
              <input name="name" value={form.name} onChange={handleChange} required className="input-field" placeholder="my-vm" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Hostname</label>
              <input name="hostname" value={form.hostname} onChange={handleChange} className="input-field" placeholder="auto from name" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Installation Source *</label>
              <select name="install_source" value={form.install_source} onChange={handleChange} required className="input-field">
                <option value="cloud">Cloud Image</option>
                <option value="iso">ISO Installer</option>
              </select>
            </div>
            {projects.length === 0 ? (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Project *</label>
                <select name="project_id" value="" disabled className="input-field">
                  <option value="">No project available</option>
                </select>
                <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
                  This tenant has no project yet. Create one under Projects first.
                </p>
              </div>
            ) : projects.length > 1 ? (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Project *</label>
                <select name="project_id" value={form.project_id} onChange={handleChange} required className="input-field">
                  <option value="">Select project...</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  Groups VMs inside this tenant (e.g. Prod / Dev).
                </p>
              </div>
            ) : null}
            {isCloudInstall ? (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Cloud Image *</label>
                <select name="template_id" value={form.template_id} onChange={handleChange} required className="input-field" disabled={images.length === 0}>
                  <option value="">{images.length === 0 ? 'No ready images available' : 'Select image...'}</option>
                  {images.map((img) => (
                    <option key={img.id} value={img.id}>{img.name} ({img.os_type} {img.os_version})</option>
                  ))}
                </select>
                {images.length === 0 && (
                  <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
                    Shared cloud images are platform-wide. Ask a superadmin to register a ready image.
                  </p>
                )}
              </div>
            ) : (
              <>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Installation ISO *</label>
                <select name="iso_id" value={form.iso_id} onChange={handleChange} required className="input-field">
                  <option value="">Select ISO...</option>
                  {installerIsos.map((img) => (
                    <option key={img.id} value={img.id}>{img.name} ({img.os_type} {img.os_version})</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">OS Profile *</label>
                <select name="os_profile" value={form.os_profile} onChange={handleChange} className="input-field">
                  <option value="linux">Linux</option>
                  <option value="windows">Windows</option>
                  <option value="other">Other</option>
                </select>
                {isWindowsInstall && (
                  <p className="mt-1 text-xs text-gray-400">Windows defaults: min 2 vCPU, 4 GB RAM, 60 GB disk. Guest setup is interactive via console.</p>
                )}
              </div>
              {isWindowsInstall && (
                <div className="md:col-span-2">
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">VirtIO Driver ISO (optional)</label>
                  <select name="virtio_iso_id" value={form.virtio_iso_id} onChange={handleChange} className="input-field">
                    <option value="">None</option>
                    {virtioIsos
                      .filter((img) => img.id !== form.iso_id)
                      .map((img) => (
                        <option key={img.id} value={img.id}>{img.name} ({img.os_type} {img.os_version})</option>
                      ))}
                  </select>
                  <p className="mt-1 text-xs text-gray-400">
                    Optional guest tools/drivers CD. Networking uses e1000e by default (no driver needed). Disk is SATA during install.
                  </p>
                </div>
              )}
              </>
            )}
            <div className="md:col-span-2">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Description</label>
              <textarea name="description" value={form.description} onChange={handleChange} className="input-field" rows={2} />
            </div>
          </div>
        </div>

        {/* Hardware */}
        <div className="card p-6">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            Hardware Profile
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">vCPUs</label>
              <input name="vcpus" type="number" min={1} max={128} value={form.vcpus} onChange={handleChange} className="input-field" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">RAM (MB)</label>
              <input name="ram_mb" type="number" min={256} step={256} value={form.ram_mb} onChange={handleChange} className="input-field" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Disk (GB)</label>
              <input name="disk_size_gb" type="number" min={5} value={form.disk_size_gb} onChange={handleChange} className="input-field" />
            </div>
            {storagePools.length > 0 && (
              <div className="md:col-span-3">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Storage Pool</label>
                <select name="storage_pool_id" value={form.storage_pool_id} onChange={handleChange} className="input-field">
                  <option value="">Default</option>
                  {storagePools.map((pool) => {
                    const usedPercent = pool.total_size_gb > 0 ? Math.round((pool.used_size_gb / pool.total_size_gb) * 100) : 0;
                    const freeGb = pool.total_size_gb - pool.used_size_gb;
                    return (
                      <option key={pool.id} value={pool.id}>
                        {pool.name} — {pool.pool_type} — {pool.path} ({freeGb} GB free, {usedPercent}% used)
                        {pool.is_default ? ' ★' : ''}
                      </option>
                    );
                  })}
                </select>
                <p className="mt-1 text-xs text-gray-400">Where the VM disk will be stored</p>
              </div>
            )}
          </div>
        </div>

        {isWindowsInstall && (
          <div className="card p-6 border border-sky-200 dark:border-sky-900 bg-sky-50/60 dark:bg-sky-950/20">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">
              Windows ISO install
            </h2>
            <ul className="text-sm text-gray-600 dark:text-gray-300 space-y-1 list-disc pl-5">
              <li>Cloud-Init / SSH / packages are not used for Windows.</li>
              <li>Complete setup interactively in the VM console after create.</li>
              <li>System disk is SATA; NIC is e1000e so Windows sees network without VirtIO drivers.</li>
            </ul>
          </div>
        )}

        {/* Cloud-Init Identity — Linux cloud images only */}
        {isCloudInstall && (
        <div className="card p-6">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            Cloud-Init: Identity
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Default user</label>
              <input name="default_user" value={form.default_user} onChange={handleChange} className="input-field" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Password (optional)</label>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <input
                    name="password"
                    type={showPassword ? 'text' : 'password'}
                    value={form.password}
                    onChange={handleChange}
                    className={`input-field pr-10 ${form.password && !isPasswordValid(form.password) ? 'border-red-500 dark:border-red-500' : ''}`}
                    placeholder="Min. 8 chars, A-Z, a-z, 0-9, special"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                  >
                    {showPassword ? <EyeSlashIcon className="h-4 w-4" /> : <EyeIcon className="h-4 w-4" />}
                  </button>
                </div>
                <button
                  type="button"
                  onClick={handleGeneratePassword}
                  className="btn btn-secondary flex items-center gap-1 text-sm whitespace-nowrap"
                  title="Generate secure password"
                >
                  <KeyIcon className="h-4 w-4" />
                  Generate
                </button>
              </div>
              {form.password && (
                <div className="mt-2 space-y-1">
                  <div className="flex gap-1">
                    {[
                      { ok: form.password.length >= 8, label: '8+ chars' },
                      { ok: /[A-Z]/.test(form.password), label: 'A-Z' },
                      { ok: /[a-z]/.test(form.password), label: 'a-z' },
                      { ok: /[0-9]/.test(form.password), label: '0-9' },
                      { ok: /[!@#$%^&*()_+\-=\[\]{}|;:',.<>?/`~]/.test(form.password), label: 'Special' },
                    ].map((rule) => (
                      <span
                        key={rule.label}
                        className={`text-xs px-1.5 py-0.5 rounded ${
                          rule.ok
                            ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                            : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                        }`}
                      >
                        {rule.ok ? '\u2713' : '\u2717'} {rule.label}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div className="md:col-span-2 space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  SSH Public Keys
                </label>
                <select
                  value={sshKeyChoice}
                  onChange={(e) => {
                    setSshKeyChoice(e.target.value);
                    if (e.target.value !== '__manual__') {
                      setForm((prev) => ({ ...prev, ssh_keys: '' }));
                    }
                  }}
                  className="input-field"
                >
                  <option value="">No SSH key</option>
                  {savedSshKeys.length > 1 && (
                    <option value="__all__">All saved keys ({savedSshKeys.length})</option>
                  )}
                  {savedSshKeys.map((k) => (
                    <option key={k.id} value={k.id}>
                      {k.name} — {k.fingerprint.slice(0, 24)}…
                    </option>
                  ))}
                  <option value="__manual__">Enter new key manually…</option>
                </select>
                {savedSshKeys.length === 0 && (
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    No keys in your profile yet. Choose “Enter new key manually” or add keys under User Profile.
                  </p>
                )}
                {sshKeyChoice && sshKeyChoice !== '__manual__' && sshKeyChoice !== '' && (
                  <p className="mt-1.5 text-xs text-gray-500 dark:text-gray-400">
                    Key material is not shown here — it will be injected via cloud-init from your encrypted profile.
                  </p>
                )}
              </div>
              {sshKeyChoice === '__manual__' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    SSH Public Keys (one per line)
                  </label>
                  <textarea
                    name="ssh_keys"
                    value={form.ssh_keys}
                    onChange={handleChange}
                    className="input-field font-mono text-xs"
                    rows={4}
                    placeholder="ssh-ed25519 AAAA..."
                  />
                </div>
              )}
            </div>
            <div className="flex items-center gap-2">
              <input name="disable_password_login" type="checkbox" checked={form.disable_password_login} onChange={handleChange} className="rounded border-gray-300 dark:border-gray-600 text-primary-600 focus:ring-primary-500" />
              <label className="text-sm text-gray-700 dark:text-gray-300">Disable password login</label>
            </div>
          </div>
        </div>
        )}

        {/* Network */}
        <div className="card p-6">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            Network
          </h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Network *</label>
              {!hasNetworkOptions ? (
                <div className="rounded-lg border-2 border-dashed border-gray-300 dark:border-gray-700 p-4 text-center">
                  <p className="text-sm text-gray-500 dark:text-gray-400">No network available.</p>
                  <p className="text-xs text-gray-400 mt-1">Create a NAT or internal network under Networks, or ask an admin for public IPs.</p>
                </div>
              ) : (
                <select
                  name="network_choice"
                  value={networkChoiceValue}
                  onChange={handleChange}
                  required
                  className="input-field"
                >
                  <option value="">Select network...</option>
                  {freePublicIpOptions.map((ip) => (
                    <option key={ip.id} value={`alloc:${ip.id}`}>
                      🌐 Public IP — {ip.ip_address}
                    </option>
                  ))}
                  {privatePools.map((pool) => (
                    <option key={pool.id} value={`pool:${pool.id}`}>
                      {pool.pool_type === 'nat'
                        ? `🔒 ${pool.name} — private NAT (${pool.available_ips} free)`
                        : `🔗 ${pool.name} — internal (${pool.available_ips} free)`}
                    </option>
                  ))}
                </select>
              )}
            </div>

            {/* Show selected network details */}
            {form.ip_pool_id && (() => {
              const pool = ipPools.find((p) => p.id === form.ip_pool_id);
              if (!pool) return null;
              const isNat = pool.pool_type === 'nat';
              const isExternal = pool.pool_type === 'external';
              const selectedPublic = form.ip_allocation_id
                ? publicIps.find((ip) => ip.id === form.ip_allocation_id)
                : null;
              return (
                <div className={`rounded-lg p-4 space-y-2 ${isNat ? 'bg-primary-50 dark:bg-primary-900/10 border border-primary-200 dark:border-primary-800' : isExternal ? 'bg-green-50 dark:bg-green-900/10 border border-green-200 dark:border-green-800' : 'bg-orange-50 dark:bg-orange-900/10 border border-orange-200 dark:border-orange-800'}`}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${isNat ? 'bg-primary-100 text-primary-700 dark:bg-primary-900/30 dark:text-primary-400' : isExternal ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400'}`}>
                      {isNat ? 'NAT (SNAT)' : isExternal ? 'Public IP' : 'Internal'}
                    </span>
                  </div>
                  {isExternal ? (
                    <div className="space-y-2 text-sm">
                      <div>
                        <span className="text-gray-500 dark:text-gray-400 block text-xs">Selected IP</span>
                        <span className="font-mono text-gray-900 dark:text-white">
                          {selectedPublic?.ip_address || '—'}
                        </span>
                      </div>
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        This public IP will be attached to the VM and is directly reachable.
                      </p>
                    </div>
                  ) : (
                    <>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                        <div>
                          <span className="text-gray-500 dark:text-gray-400 block text-xs">Subnet</span>
                          <span className="font-mono text-gray-900 dark:text-white">{pool.network}</span>
                        </div>
                        <div>
                          <span className="text-gray-500 dark:text-gray-400 block text-xs">Gateway</span>
                          <span className="font-mono text-gray-900 dark:text-white">{pool.gateway}</span>
                        </div>
                        <div>
                          <span className="text-gray-500 dark:text-gray-400 block text-xs">DNS</span>
                          <span className="font-mono text-gray-900 dark:text-white">
                            {pool.dns_primary}{pool.dns_secondary ? `, ${pool.dns_secondary}` : ''}
                          </span>
                        </div>
                        <div>
                          <span className="text-gray-500 dark:text-gray-400 block text-xs">Available</span>
                          <span className="font-medium text-gray-900 dark:text-white">{pool.available_ips} IPs</span>
                        </div>
                      </div>
                      <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                        {isNat
                          ? 'The VM receives a private IP. Internet access via SNAT. Incoming traffic via port forwarding.'
                          : 'Isolated VM-to-VM network.'}
                      </p>
                    </>
                  )}
                </div>
              );
            })()}

            {/* Additional internal interfaces at creation time */}
            <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-4">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Additional internal interfaces (optional)
              </label>
              {availableSecondaryPools.length === 0 ? (
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  No additional internal networks available.
                </p>
              ) : (
                <div className="space-y-2">
                  {availableSecondaryPools.map((pool) => {
                    const checked = form.secondary_pool_ids.includes(pool.id);
                    return (
                      <label key={pool.id} className="flex items-center justify-between gap-3 rounded border border-gray-200 dark:border-gray-700 p-2.5">
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-gray-900 dark:text-white">{pool.name}</p>
                          <p className="text-xs text-gray-500 dark:text-gray-400">
                            {pool.network} · DHCP {pool.internal_dhcp_enabled ? 'on' : 'off'}
                          </p>
                        </div>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleSecondaryPool(pool.id)}
                          className="rounded border-gray-300 dark:border-gray-600 text-primary-600 focus:ring-primary-500"
                        />
                      </label>
                    );
                  })}
                </div>
              )}
              <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                Selected: {form.secondary_pool_ids.length}/4. Interfaces are attached during VM provisioning.
              </p>
            </div>
          </div>
        </div>

        {/* Locale & Keyboard — Linux cloud images only */}
        {isCloudInstall && (
        <div className="card p-6">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            Language & Keyboard
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Keyboard Layout</label>
              <select name="keyboard_layout" value={form.keyboard_layout} onChange={handleChange} className="input-field">
                <option value="">— Default (en-US) —</option>
                <optgroup label="German">
                  <option value="de">German (QWERTZ)</option>
                  <option value="ch">Switzerland (QWERTZ)</option>
                  <option value="at">Austria (QWERTZ)</option>
                </optgroup>
                <optgroup label="Other">
                  <option value="us">English US (QWERTY)</option>
                  <option value="gb">English UK (QWERTY)</option>
                  <option value="fr">French (AZERTY)</option>
                  <option value="es">Spanish</option>
                  <option value="it">Italian</option>
                  <option value="pt">Portuguese</option>
                  <option value="nl">Dutch</option>
                  <option value="pl">Polish</option>
                  <option value="ru">Russian</option>
                  <option value="ja">Japanese</option>
                </optgroup>
              </select>
              <p className="mt-1 text-xs text-gray-400">
                Configured in guest OS via Cloud-Init
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Timezone</label>
              <select name="timezone" value={form.timezone} onChange={handleChange} className="input-field">
                <optgroup label="Europe">
                  <option value="Europe/Berlin">Berlin (CET/CEST)</option>
                  <option value="Europe/Zurich">Zurich (CET/CEST)</option>
                  <option value="Europe/Vienna">Vienna (CET/CEST)</option>
                  <option value="Europe/London">London (GMT/BST)</option>
                  <option value="Europe/Paris">Paris (CET/CEST)</option>
                  <option value="Europe/Amsterdam">Amsterdam (CET/CEST)</option>
                  <option value="Europe/Madrid">Madrid (CET/CEST)</option>
                  <option value="Europe/Rome">Rome (CET/CEST)</option>
                  <option value="Europe/Warsaw">Warsaw (CET/CEST)</option>
                  <option value="Europe/Moscow">Moscow (MSK)</option>
                </optgroup>
                <optgroup label="Americas">
                  <option value="America/New_York">New York (EST/EDT)</option>
                  <option value="America/Chicago">Chicago (CST/CDT)</option>
                  <option value="America/Los_Angeles">Los Angeles (PST/PDT)</option>
                  <option value="America/Sao_Paulo">São Paulo (BRT)</option>
                </optgroup>
                <optgroup label="Asia">
                  <option value="Asia/Tokyo">Tokyo (JST)</option>
                  <option value="Asia/Shanghai">Shanghai (CST)</option>
                  <option value="Asia/Singapore">Singapore (SGT)</option>
                  <option value="Asia/Dubai">Dubai (GST)</option>
                </optgroup>
                <optgroup label="Other">
                  <option value="UTC">UTC</option>
                  <option value="Australia/Sydney">Sydney (AEST)</option>
                </optgroup>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Locale (optional)</label>
              <input name="locale" value={form.locale} onChange={handleChange} className="input-field" placeholder="auto (from keyboard layout)" />
              <p className="mt-1 text-xs text-gray-400">
                e.g. de_DE.UTF-8 — empty = automatic
              </p>
            </div>
          </div>
        </div>
        )}

        {/* User-Data extras — Linux cloud images only */}
        {isCloudInstall && (
        <div className="card p-6">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            Cloud-Init: Advanced
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Packages (comma-separated)</label>
              <input name="packages" value={form.packages} onChange={handleChange} className="input-field" placeholder="htop, curl, nginx" />
              <p className="mt-1 text-xs text-gray-400">
                Extra packages only — <span className="font-mono">qemu-guest-agent</span> is installed automatically.
              </p>
            </div>
            <div className="md:col-span-2">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Commands after boot (one per line)</label>
              <textarea name="runcmd" value={form.runcmd} onChange={handleChange} className="input-field font-mono text-xs" rows={4} placeholder="apt install -y nginx&#10;systemctl enable --now nginx" />
              <p className="mt-1 text-xs text-gray-400">
                These commands are executed as root after first boot. Guest agent is enabled automatically.
              </p>
            </div>
            <div className="md:col-span-2">
              <div className="flex items-center justify-between gap-3 mb-1">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                  Custom user-data YAML (optional)
                </label>
                <label className="inline-flex items-center gap-2 rounded-lg border border-gray-300 dark:border-gray-700 px-3 py-1.5 text-xs text-gray-700 dark:text-gray-300 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800">
                  YAML-Datei laden
                  <input
                    type="file"
                    accept=".yml,.yaml,text/yaml,text/x-yaml"
                    onChange={handleCustomUserDataFile}
                    className="hidden"
                  />
                </label>
              </div>
              <textarea
                name="custom_userdata_yaml"
                value={form.custom_userdata_yaml}
                onChange={handleChange}
                className="input-field font-mono text-xs"
                rows={10}
                placeholder={`#cloud-config\nusers:\n  - name: devops\n    sudo: ALL=(ALL) NOPASSWD:ALL\npackages:\n  - docker.io\nruncmd:\n  - systemctl enable --now docker`}
              />
              <p className="mt-1 text-xs text-gray-400">
                Wird mit den Standardwerten zusammengeführt (Defaults bleiben erhalten, dein YAML ergänzt/überschreibt Werte).
              </p>
            </div>
          </div>
        </div>
        )}

        {/* Submit */}
        <div className="flex justify-end gap-3">
          <button type="button" onClick={() => navigate('/vms')} className="btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={isSubmitting} className="btn-primary disabled:opacity-50">
            {isSubmitting ? 'Creating...' : 'Create VM'}
          </button>
        </div>
      </form>
    </div>
  );
}
