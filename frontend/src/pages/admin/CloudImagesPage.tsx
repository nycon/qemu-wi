import { useEffect, useState } from 'react';
import { PlusIcon, TrashIcon, ArrowDownTrayIcon, ArrowPathIcon, CheckCircleIcon } from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { storageApi } from '@/services/api';
import type { CloudImage, StoragePool } from '@/types';

const statusConfig: Record<string, { label: string; color: string; animate?: boolean }> = {
  pending: { label: 'Pending', color: 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-400' },
  downloading: { label: 'Downloading...', color: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400', animate: true },
  extracting: { label: 'Extracting...', color: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400', animate: true },
  ready: { label: 'Ready', color: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' },
  failed: { label: 'Failed', color: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400' },
};

function formatBytes(bytes: number): string {
  if (bytes === 0) return '-';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function DownloadProgressBar({ progress, status }: { progress: number; status: string }) {
  if (status === 'ready' || status === 'failed' || status === 'pending') return null;

  const isExtracting = status === 'extracting';
  const displayProgress = Math.min(100, Math.max(0, progress));

  return (
    <div className="mt-2 w-full max-w-[200px]">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-gray-500 dark:text-gray-400">
          {isExtracting ? 'Extracting...' : `${displayProgress}%`}
        </span>
      </div>
      <div className="w-full bg-gray-200 rounded-full h-2 dark:bg-gray-700 overflow-hidden">
        {isExtracting ? (
          <div className="h-full rounded-full bg-gradient-to-r from-yellow-400 via-yellow-500 to-yellow-400 animate-pulse" style={{ width: '100%' }} />
        ) : (
          <div
            className="h-full rounded-full bg-blue-500 transition-all duration-500 ease-out"
            style={{ width: `${displayProgress}%` }}
          />
        )}
      </div>
    </div>
  );
}

function inferMetadataFromFilename(fileName: string): {
  inferredName: string;
  osType: 'linux' | 'windows' | 'bsd' | 'other';
  osVersion: string;
  format: 'qcow2' | 'raw' | 'iso';
} {
  const baseName = fileName.replace(/\.[^.]+$/, '');
  const lower = fileName.toLowerCase();

  let format: 'qcow2' | 'raw' | 'iso' = 'qcow2';
  if (lower.endsWith('.iso')) format = 'iso';
  else if (lower.endsWith('.raw')) format = 'raw';
  // .img from Ubuntu cloud-images.ubuntu.com is qcow2 despite the extension
  else if (lower.endsWith('.img')) format = 'qcow2';

  let osType: 'linux' | 'windows' | 'bsd' | 'other' = 'linux';
  if (lower.includes('windows') || lower.includes('win10') || lower.includes('win11') || lower.includes('server')) {
    osType = 'windows';
  } else if (lower.includes('freebsd') || lower.includes('openbsd') || lower.includes('netbsd') || lower.includes('opnsense') || lower.includes('pfsense')) {
    osType = 'bsd';
  } else if (lower.includes('virtio')) {
    osType = 'windows';
  }

  let osVersion = 'unknown';
  const yearMatch = lower.match(/\b(20\d{2})\b/);
  if (yearMatch) osVersion = yearMatch[1];
  const ubuntuMatch = lower.match(/ubuntu[-_ ]?(\d{2}\.\d{2})/);
  if (ubuntuMatch) osVersion = ubuntuMatch[1];
  const debianMatch = lower.match(/debian[-_ ]?(\d{1,2})/);
  if (debianMatch) osVersion = debianMatch[1];

  return {
    inferredName: baseName,
    osType,
    osVersion,
    format,
  };
}

export function CloudImagesPage() {
  const [images, setImages] = useState<CloudImage[]>([]);
  const [pools, setPools] = useState<StoragePool[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [uploadState, setUploadState] = useState<{
    active: boolean;
    fileName: string;
    loaded: number;
    total: number;
  }>({
    active: false,
    fileName: '',
    loaded: 0,
    total: 0,
  });
  const [form, setForm] = useState({
    name: '',
    os_type: 'linux',
    os_version: '',
    architecture: 'x86_64',
    format: 'qcow2',
    description: '',
    min_vcpus: 1,
    min_ram_mb: 512,
    min_disk_gb: 5,
    storage_pool_id: '',
    download_url: '',
    default_userdata_yaml: '',
  });

  const loadData = async () => {
    try {
      const [imgData, poolData] = await Promise.all([
        storageApi.listImages(),
        storageApi.listPools(),
      ]);
      setImages(imgData);
      setPools(poolData);
    } catch {
      toast.error('Data could not be loaded');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    // Poll for download status updates
    const interval = setInterval(loadData, 3000);
    return () => clearInterval(interval);
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const { name, value, type } = e.target;
    setForm((prev) => ({
      ...prev,
      [name]: type === 'number' ? Number(value) : value,
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await storageApi.createImage({
        ...form,
        storage_pool_id: form.storage_pool_id || undefined,
        download_url: form.download_url || undefined,
      } as Partial<CloudImage>);
      toast.success(form.download_url ? 'Image is downloading...' : 'Cloud Image registered');
      setShowForm(false);
      setForm({
        name: '',
        os_type: 'linux',
        os_version: '',
        architecture: 'x86_64',
        format: 'qcow2',
        description: '',
        min_vcpus: 1,
        min_ram_mb: 512,
        min_disk_gb: 5,
        storage_pool_id: '',
        download_url: '',
        default_userdata_yaml: '',
      });
      loadData();
    } catch {
      toast.error('Image could not be created');
    }
  };

  const handleDelete = async (img: CloudImage) => {
    if (!confirm(`Really delete image "${img.name}"?`)) return;
    try {
      await storageApi.deleteImage(img.id);
      toast.success('Image deleted');
      loadData();
    } catch {
      toast.error('Delete failed');
    }
  };

  const handleRetryDownload = async (img: CloudImage) => {
    try {
      await storageApi.retryDownload(img.id);
      toast.success('Download will be retried...');
      loadData();
    } catch {
      toast.error('Retry failed');
    }
  };

  // Check if any images are actively downloading
  const hasActiveDownloads = images.some((i) => i.download_status === 'downloading' || i.download_status === 'extracting');

  return (
    <div>
      <div className="sm:flex sm:items-center sm:justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Cloud Images</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {images.length} image(s) registered
            {hasActiveDownloads && <span className="ml-2 text-blue-500 animate-pulse">Downloads active...</span>}
          </p>
        </div>
        <button onClick={() => setShowForm(!showForm)} className="btn-primary mt-4 sm:mt-0">
          <PlusIcon className="h-5 w-5 mr-1.5" />
          Add image
        </button>
      </div>

      {showForm && (
        <div className="card p-6 mb-8">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">New Cloud Image</h2>
          <form onSubmit={handleSubmit} className="space-y-6">
            {/* Source: Download URL or File Upload */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="bg-blue-50 dark:bg-blue-900/20 rounded-lg p-4 border border-blue-200 dark:border-blue-800">
                <label className="block text-sm font-medium text-blue-800 dark:text-blue-300 mb-1">
                  <ArrowDownTrayIcon className="h-4 w-4 inline mr-1" />
                  Download URL
                </label>
                <input
                  name="download_url"
                  value={form.download_url}
                  onChange={handleChange}
                  className="input-field"
                  placeholder="https://cloud-images.ubuntu.com/..."
                />
                <p className="mt-1 text-xs text-blue-600 dark:text-blue-400">
                  .qcow2, .img, .raw, .tar.xz – amd64/x86_64 only on this host (no arm64)
                </p>
              </div>
              <div className="bg-green-50 dark:bg-green-900/20 rounded-lg p-4 border border-green-200 dark:border-green-800">
                <label className="block text-sm font-medium text-green-800 dark:text-green-300 mb-1">
                  Or: Upload file
                </label>
                <input
                  type="file"
                  accept=".qcow2,.img,.raw,.iso"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    const inferred = inferMetadataFromFilename(file.name);
                    const uploadName = form.name || inferred.inferredName;
                    const uploadOsType = form.os_type === 'linux' ? inferred.osType : form.os_type;
                    const uploadOsVersion = form.os_version || inferred.osVersion;
                    const uploadFormat = form.format === 'qcow2' ? inferred.format : form.format;

                    setForm((prev) => ({
                      ...prev,
                      name: prev.name || uploadName,
                      os_type: uploadOsType,
                      os_version: prev.os_version || uploadOsVersion,
                      format: uploadFormat,
                    }));

                    const formData = new FormData();
                    formData.append('file', file);
                    formData.append('name', uploadName);
                    formData.append('os_type', uploadOsType);
                    formData.append('os_version', uploadOsVersion || 'unknown');
                    formData.append('image_format', uploadFormat);
                    formData.append('min_disk_gb', String(form.min_disk_gb));
                    formData.append('min_ram_mb', String(form.min_ram_mb));
                    if (form.default_userdata_yaml.trim()) {
                      formData.append('default_userdata_yaml', form.default_userdata_yaml);
                    }
                    const t = toast.loading(`Uploading "${file.name}"...`);
                    setUploadState({
                      active: true,
                      fileName: file.name,
                      loaded: 0,
                      total: file.size || 0,
                    });
                    try {
                      await storageApi.uploadImage(formData, (loaded, total) => {
                        setUploadState({
                          active: true,
                          fileName: file.name,
                          loaded,
                          total: total || file.size || 0,
                        });
                      });
                      toast.success('Image uploaded!', { id: t });
                      loadData();
                      setShowForm(false);
                      setForm({
                        name: '',
                        os_type: 'linux',
                        os_version: '',
                        architecture: 'x86_64',
                        format: 'qcow2',
                        description: '',
                        min_vcpus: 1,
                        min_ram_mb: 512,
                        min_disk_gb: 5,
                        storage_pool_id: '',
                        download_url: '',
                        default_userdata_yaml: '',
                      });
                    } catch (err: unknown) {
                      const detail = (err && typeof err === 'object' && 'response' in err)
                        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
                        : String(err);
                      toast.error(`Upload failed: ${detail}`, { id: t });
                    } finally {
                      setUploadState({
                        active: false,
                        fileName: '',
                        loaded: 0,
                        total: 0,
                      });
                    }
                    e.target.value = '';
                  }}
                  className="block w-full text-sm text-gray-600 dark:text-gray-400 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-semibold file:bg-green-100 file:text-green-700 hover:file:bg-green-200 dark:file:bg-green-900/30 dark:file:text-green-400"
                />
                {uploadState.active && (() => {
                  const percent = uploadState.total > 0
                    ? Math.min(100, Math.round((uploadState.loaded / uploadState.total) * 100))
                    : 0;
                  return (
                    <div className="mt-3">
                      <div className="flex items-center justify-between text-xs mb-1 text-gray-600 dark:text-gray-300">
                        <span className="truncate pr-2">
                          Uploading: {uploadState.fileName}
                        </span>
                        <span>{percent}%</span>
                      </div>
                      <div className="w-full bg-gray-200 rounded-full h-2 dark:bg-gray-700 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-green-500 transition-all duration-300 ease-out"
                          style={{ width: `${percent}%` }}
                        />
                      </div>
                      <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                        {formatBytes(uploadState.loaded)} / {formatBytes(uploadState.total)}
                      </p>
                    </div>
                  );
                })()}
                <p className="mt-1 text-xs text-green-600 dark:text-green-400">
                  .qcow2, .img, .raw, .iso — upload directly from browser
                </p>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  Upload starts automatically and is saved without extra clicks.
                </p>
              </div>
            </div>

            {/* Metadata */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name *</label>
                <input name="name" value={form.name} onChange={handleChange} required className="input-field" placeholder="Ubuntu 22.04 LTS" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">OS Type *</label>
                <select name="os_type" value={form.os_type} onChange={handleChange} className="input-field">
                  <option value="linux">Linux</option>
                  <option value="windows">Windows</option>
                  <option value="bsd">BSD</option>
                  <option value="other">Other</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">OS Version *</label>
                <input name="os_version" value={form.os_version} onChange={handleChange} required className="input-field" placeholder="22.04" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Architecture</label>
                <select name="architecture" value={form.architecture} onChange={handleChange} className="input-field">
                  <option value="x86_64">x86_64</option>
                  <option value="aarch64">aarch64</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Format</label>
                <select name="format" value={form.format} onChange={handleChange} className="input-field">
                  <option value="qcow2">QCOW2</option>
                  <option value="raw">RAW</option>
                  <option value="iso">ISO</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Storage Pool</label>
                <select name="storage_pool_id" value={form.storage_pool_id} onChange={handleChange} className="input-field">
                  <option value="">Default</option>
                  {pools.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Min vCPUs</label>
                <input name="min_vcpus" type="number" min={1} value={form.min_vcpus} onChange={handleChange} className="input-field" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Min RAM (MB)</label>
                <input name="min_ram_mb" type="number" min={256} value={form.min_ram_mb} onChange={handleChange} className="input-field" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Min Disk (GB)</label>
                <input name="min_disk_gb" type="number" min={1} value={form.min_disk_gb} onChange={handleChange} className="input-field" />
              </div>
              <div className="md:col-span-2 lg:col-span-3">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Description</label>
                <textarea name="description" value={form.description} onChange={handleChange} className="input-field" rows={2} />
              </div>
              <div className="md:col-span-2 lg:col-span-3">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Default Cloud-Init user-data YAML (optional)
                </label>
                <textarea
                  name="default_userdata_yaml"
                  value={form.default_userdata_yaml}
                  onChange={handleChange}
                  className="input-field font-mono text-xs"
                  rows={8}
                  placeholder={`#cloud-config\npackages:\n  - htop\nruncmd:\n  - systemctl enable --now docker`}
                />
                <p className="mt-1 text-xs text-gray-400">
                  Dieses YAML wird bei jeder neuen VM aus diesem Image als Standard mit den System-Defaults gemerged.
                </p>
              </div>
            </div>

            <div className="flex justify-end gap-3">
              <button type="button" onClick={() => setShowForm(false)} className="btn-secondary">Cancel</button>
              <button type="submit" className="btn-primary">
                {form.download_url ? (
                  <><ArrowDownTrayIcon className="h-4 w-4 mr-1.5" />Download &amp; Register</>
                ) : (
                  'Register image'
                )}
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="card overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          <thead className="bg-gray-50 dark:bg-gray-800/50">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Name</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">OS</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Size</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Min. Resources</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Status</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Created</th>
              <th className="relative px-6 py-3"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
            {isLoading ? (
              <tr><td colSpan={7} className="px-6 py-12 text-center text-gray-500 dark:text-gray-400">Loading...</td></tr>
            ) : images.length === 0 ? (
              <tr><td colSpan={7} className="px-6 py-12 text-center text-gray-500 dark:text-gray-400">
                <ArrowDownTrayIcon className="h-10 w-10 mx-auto mb-2 text-gray-300" />
                <p>No Cloud Images registered</p>
                <p className="text-xs mt-1">Click &quot;Add image&quot; and enter a download URL</p>
              </td></tr>
            ) : images.map((img) => {
              const status = statusConfig[img.download_status] ?? statusConfig['pending'];
              const isActive = img.download_status === 'downloading' || img.download_status === 'extracting';
              return (
                <tr key={img.id} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="text-sm font-medium text-gray-900 dark:text-white">{img.name}</div>
                    <div className="text-xs text-gray-500 dark:text-gray-400">{img.description || img.format.toUpperCase()}</div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                    {img.os_type} {img.os_version} ({img.architecture})
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                    {formatBytes(img.file_size_bytes)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                    {img.min_vcpus} vCPU, {img.min_ram_mb} MB, {img.min_disk_gb} GB
                  </td>
                  <td className="px-6 py-4">
                    <div>
                      <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${status.color}`}>
                        {status.animate && <ArrowPathIcon className="h-3 w-3 animate-spin" />}
                        {img.download_status === 'ready' && <CheckCircleIcon className="h-3 w-3" />}
                        {status.label}
                      </span>
                      {/* Progress bar for active downloads */}
                      {isActive && (
                        <DownloadProgressBar
                          progress={img.download_progress}
                          status={img.download_status}
                        />
                      )}
                      {img.download_error && (
                        <p className="text-xs text-red-500 mt-1 max-w-xs truncate" title={img.download_error}>
                          {img.download_error}
                        </p>
                      )}
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                    {new Date(img.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right">
                    <div className="flex items-center justify-end gap-1">
                      {img.download_url && img.download_status !== 'downloading' && img.download_status !== 'extracting' && (
                        <button
                          onClick={() => handleRetryDownload(img)}
                          className="text-blue-500 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 p-1"
                          title={img.download_status === 'ready' ? 'Download again' : 'Start / retry download'}
                        >
                          <ArrowDownTrayIcon className="h-4 w-4" />
                        </button>
                      )}
                      <button onClick={() => handleDelete(img)} className="text-red-500 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 p-1" title="Delete">
                        <TrashIcon className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
