import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowUpTrayIcon,
  ArrowPathIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  ServerStackIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { useTenantStore } from '@/store/tenantStore';
import { vmApi, storageApi, ipamApi } from '@/services/api';
import type { StoragePool, IPPool } from '@/types';

type ImportStep = 'config' | 'upload' | 'registering' | 'done' | 'error';

export function VMImportPage() {
  const navigate = useNavigate();
  const currentTenant = useTenantStore((s) => s.currentTenant);

  const [step, setStep] = useState<ImportStep>('config');
  const [storagePools, setStoragePools] = useState<StoragePool[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [vmId, setVmId] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState('');

  const [form, setForm] = useState({
    name: '',
    vcpus: 2,
    ram_mb: 2048,
    disk_size_gb: 20,
    storage_pool_id: '',
    bridge: 'virbr0',
  });
  const [file, setFile] = useState<File | null>(null);

  useEffect(() => {
    storageApi.listPools(currentTenant ? { tenant_id: currentTenant.id } : undefined).then((pools) => {
      const active = pools.filter((p) => p.is_active);
      setStoragePools(active);
      const def = active.find((p) => p.is_default);
      if (def) setForm((f) => ({ ...f, storage_pool_id: def.id }));
    }).catch(() => {});
  }, [currentTenant]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentTenant || !file) return;

    setLoading(true);
    setStep('upload');

    try {
      // Step 1: Create VM record + prepare storage
      const result = await vmApi.importVM(currentTenant.id, {
        name: form.name,
        vcpus: form.vcpus,
        ram_mb: form.ram_mb,
        disk_size_gb: form.disk_size_gb,
        storage_pool_id: form.storage_pool_id || undefined,
        bridge: form.bridge,
      });
      setVmId(result.vm_id);

      // Step 2: Upload disk to host agent via backend proxy
      // We use XMLHttpRequest for progress tracking
      const uploadUrl = `/api/v1/tenants/${currentTenant.id}/vms/${result.vm_id}/import/upload`;
      const { getAccessToken } = await import('@/services/tokenMemory');
      const token = getAccessToken();

      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('PUT', uploadUrl, true);
        xhr.withCredentials = true;
        if (token) {
          xhr.setRequestHeader('Authorization', `Bearer ${token}`);
        }
        xhr.setRequestHeader('Content-Type', 'application/octet-stream');

        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            setUploadProgress(Math.round((e.loaded / e.total) * 100));
          }
        };

        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve();
          } else {
            reject(new Error(`Upload failed: ${xhr.statusText}`));
          }
        };
        xhr.onerror = () => reject(new Error('Upload failed'));
        xhr.send(file);
      });

      // Step 3: Finalize – register in libvirt
      setStep('registering');
      await vmApi.finalizeImport(currentTenant.id, result.vm_id);

      // Poll for completion
      const poll = setInterval(async () => {
        try {
          const vmData = await vmApi.get(currentTenant.id, result.vm_id);
          if (vmData.status === 'stopped' || vmData.status === 'running') {
            clearInterval(poll);
            setStep('done');
            setLoading(false);
            toast.success('VM imported successfully!');
          } else if (vmData.status === 'error') {
            clearInterval(poll);
            setStep('error');
            setErrorMsg('VM registration failed');
            setLoading(false);
          }
        } catch {
          // Keep polling
        }
      }, 3000);

      // Timeout after 5 min
      setTimeout(() => {
        setStep('done');
        setLoading(false);
      }, 300000);

    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      setStep('error');
      setErrorMsg(detail);
      setLoading(false);
      toast.error(detail);
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  };

  if (!currentTenant) {
    return <div className="text-center py-12 text-gray-500 dark:text-gray-400">Please select a tenant.</div>;
  }

  return (
    <div className="max-w-2xl mx-auto">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Import VM</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Import a virtual machine from a qcow2 disk image
        </p>
      </div>

      {step === 'config' && (
        <form onSubmit={handleSubmit} className="card p-6 space-y-5">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">VM Name *</label>
            <input
              type="text"
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. imported-webserver"
              className="input w-full"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Disk Image (qcow2) *</label>
            <div className="mt-1 flex justify-center rounded-lg border-2 border-dashed border-gray-300 dark:border-gray-600 px-6 py-8">
              <div className="text-center">
                {file ? (
                  <>
                    <CheckCircleIcon className="mx-auto h-10 w-10 text-green-500" />
                    <p className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{file.name}</p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">{formatSize(file.size)}</p>
                    <button type="button" onClick={() => setFile(null)} className="mt-2 text-xs text-red-500 hover:text-red-600">
                      Remove
                    </button>
                  </>
                ) : (
                  <>
                    <ArrowUpTrayIcon className="mx-auto h-10 w-10 text-gray-400" />
                    <div className="mt-2">
                      <label className="cursor-pointer text-sm font-medium text-primary-600 hover:text-primary-500 dark:text-primary-400">
                        Choose file
                        <input
                          type="file"
                          accept=".qcow2,.img,.raw"
                          className="sr-only"
                          onChange={(e) => {
                            const f = e.target.files?.[0];
                            if (f) setFile(f);
                          }}
                        />
                      </label>
                      <span className="text-sm text-gray-500 dark:text-gray-400"> or drag and drop</span>
                    </div>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">qcow2, img, raw formats supported</p>
                  </>
                )}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">vCPUs</label>
              <input type="number" min={1} max={32} value={form.vcpus} onChange={(e) => setForm({ ...form, vcpus: parseInt(e.target.value) || 1 })} className="input w-full" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">RAM (MB)</label>
              <input type="number" min={256} step={256} value={form.ram_mb} onChange={(e) => setForm({ ...form, ram_mb: parseInt(e.target.value) || 1024 })} className="input w-full" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Disk (GB)</label>
              <input type="number" min={1} value={form.disk_size_gb} onChange={(e) => setForm({ ...form, disk_size_gb: parseInt(e.target.value) || 20 })} className="input w-full" />
            </div>
          </div>

          {storagePools.length > 0 && (
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Storage Pool</label>
              <select
                value={form.storage_pool_id}
                onChange={(e) => setForm({ ...form, storage_pool_id: e.target.value })}
                className="input w-full"
              >
                {storagePools.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.pool_type}) – {p.path}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-800/30 rounded-lg p-3">
            <p className="text-xs text-amber-700 dark:text-amber-400">
              <strong>Note:</strong> The imported disk image will be placed in the VM's storage directory.
              If the disk is smaller than the configured size, it will be automatically resized.
              The VM will be registered in libvirt and available as a stopped VM.
            </p>
          </div>

          <div className="flex gap-3 pt-2">
            <button
              type="submit"
              disabled={!form.name || !file}
              className="btn btn-primary flex items-center gap-2"
            >
              <ArrowUpTrayIcon className="h-4 w-4" />
              Import VM
            </button>
            <button type="button" onClick={() => navigate('/vms')} className="btn btn-secondary">
              Cancel
            </button>
          </div>
        </form>
      )}

      {step === 'upload' && (
        <div className="card p-8 text-center">
          <ArrowUpTrayIcon className="mx-auto h-12 w-12 text-primary-500 mb-4" />
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">Uploading Disk Image...</h2>
          <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-3 mb-3">
            <div
              className="bg-primary-600 h-3 rounded-full transition-all duration-300"
              style={{ width: `${uploadProgress}%` }}
            />
          </div>
          <p className="text-sm text-gray-500 dark:text-gray-400">{uploadProgress}% uploaded</p>
          {file && <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">{file.name} ({formatSize(file.size)})</p>}
        </div>
      )}

      {step === 'registering' && (
        <div className="card p-8 text-center">
          <ArrowPathIcon className="mx-auto h-12 w-12 text-primary-500 animate-spin mb-4" />
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">Registering VM...</h2>
          <p className="text-sm text-gray-500 dark:text-gray-400">The VM is being registered in libvirt. This may take a moment.</p>
        </div>
      )}

      {step === 'done' && (
        <div className="card p-8 text-center">
          <CheckCircleIcon className="mx-auto h-12 w-12 text-green-500 mb-4" />
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">Import Successful!</h2>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">The VM has been imported and is ready to use.</p>
          <div className="flex gap-3 justify-center">
            {vmId && (
              <button onClick={() => navigate(`/vms/${vmId}`)} className="btn btn-primary">
                View VM
              </button>
            )}
            <button onClick={() => navigate('/vms')} className="btn btn-secondary">
              VM List
            </button>
          </div>
        </div>
      )}

      {step === 'error' && (
        <div className="card p-8 text-center">
          <ExclamationTriangleIcon className="mx-auto h-12 w-12 text-red-500 mb-4" />
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">Import Failed</h2>
          <p className="text-sm text-red-500 dark:text-red-400 mb-6">{errorMsg}</p>
          <div className="flex gap-3 justify-center">
            <button onClick={() => { setStep('config'); setErrorMsg(''); }} className="btn btn-primary">
              Try Again
            </button>
            <button onClick={() => navigate('/vms')} className="btn btn-secondary">
              VM List
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
