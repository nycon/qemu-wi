import { useCallback, useEffect, useState } from 'react';
import {
  PlusIcon,
  PencilIcon,
  TrashIcon,
  CpuChipIcon,
  GlobeAltIcon,
  ServerStackIcon,
  CircleStackIcon,
  CommandLineIcon,
  CloudIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { useAuthStore } from '@/store/authStore';
import { vmTemplateApi, storageApi, type VMTemplateItem } from '@/services/api';
import type { CloudImage } from '@/types';

const ICON_OPTIONS = [
  { value: 'server', label: 'Server', icon: ServerStackIcon },
  { value: 'database', label: 'Database', icon: CircleStackIcon },
  { value: 'web', label: 'Web', icon: GlobeAltIcon },
  { value: 'compute', label: 'Compute', icon: CpuChipIcon },
  { value: 'dev', label: 'Development', icon: CommandLineIcon },
  { value: 'cloud', label: 'Cloud', icon: CloudIcon },
];

function getIconComponent(icon: string) {
  const found = ICON_OPTIONS.find((o) => o.value === icon);
  return found ? found.icon : ServerStackIcon;
}

export function VMTemplatesPage() {
  const user = useAuthStore((s) => s.user);
  const [templates, setTemplates] = useState<VMTemplateItem[]>([]);
  const [images, setImages] = useState<CloudImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const emptyForm = {
    name: '',
    description: '',
    icon: 'server',
    vcpus: 2,
    ram_mb: 2048,
    disk_size_gb: 20,
    cloud_image_id: '',
    default_user: 'cloud',
    packages: '',
    runcmd: '',
    timezone: 'Europe/Berlin',
    keyboard_layout: 'de',
    locale: '',
    ssh_keys: '',
    is_global: false,
  };

  const [form, setForm] = useState(emptyForm);

  const loadData = useCallback(async () => {
    try {
      const [t, img] = await Promise.all([
        vmTemplateApi.list(),
        storageApi.listImages().catch(() => []),
      ]);
      setTemplates(t);
      setImages(img.filter((i) => i.download_status === 'ready'));
    } catch {
      toast.error('Failed to load templates');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleEdit = (t: VMTemplateItem) => {
    setEditingId(t.id);
    setForm({
      name: t.name,
      description: t.description || '',
      icon: t.icon,
      vcpus: t.vcpus,
      ram_mb: t.ram_mb,
      disk_size_gb: t.disk_size_gb,
      cloud_image_id: t.cloud_image_id || '',
      default_user: t.default_user,
      packages: (t.packages || []).join(', '),
      runcmd: (t.runcmd || []).join('\n'),
      timezone: t.timezone,
      keyboard_layout: t.keyboard_layout,
      locale: t.locale || '',
      ssh_keys: (t.ssh_keys || []).join('\n'),
      is_global: t.is_global,
    });
    setShowForm(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      const payload = {
        name: form.name,
        description: form.description || undefined,
        icon: form.icon,
        vcpus: form.vcpus,
        ram_mb: form.ram_mb,
        disk_size_gb: form.disk_size_gb,
        cloud_image_id: form.cloud_image_id || undefined,
        default_user: form.default_user,
        packages: form.packages.split(',').map((p) => p.trim()).filter(Boolean),
        runcmd: form.runcmd.split('\n').map((c) => c.trim()).filter(Boolean),
        timezone: form.timezone,
        keyboard_layout: form.keyboard_layout,
        locale: form.locale || undefined,
        ssh_keys: form.ssh_keys.split('\n').map((k) => k.trim()).filter(Boolean),
        is_global: form.is_global,
      };

      if (editingId) {
        await vmTemplateApi.update(editingId, payload);
        toast.success('Template updated');
      } else {
        await vmTemplateApi.create(payload);
        toast.success('Template created');
      }

      setShowForm(false);
      setEditingId(null);
      setForm(emptyForm);
      loadData();
    } catch (err: unknown) {
      const detail = (err && typeof err === 'object' && 'response' in err)
        ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Error'
        : String(err);
      toast.error(detail);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (t: VMTemplateItem) => {
    if (!window.confirm(`Delete template "${t.name}"?`)) return;
    try {
      await vmTemplateApi.delete(t.id);
      toast.success('Template deleted');
      loadData();
    } catch {
      toast.error('Delete failed');
    }
  };

  const formatRam = (mb: number) => mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 === 0 ? 0 : 1)} GB` : `${mb} MB`;

  const getImageName = (id: string | null) => {
    if (!id) return null;
    const img = images.find((i) => i.id === id);
    return img ? `${img.name} (${img.os_type} ${img.os_version})` : null;
  };

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">VM Templates</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Pre-configured VM presets for quick creation
          </p>
        </div>
        <button
          onClick={() => { setShowForm(!showForm); setEditingId(null); setForm(emptyForm); }}
          className="btn btn-primary flex items-center gap-2"
        >
          <PlusIcon className="h-4 w-4" />
          New Template
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleSubmit} className="card p-6 mb-8 space-y-5">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            {editingId ? 'Edit Template' : 'New Template'}
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="md:col-span-2">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name *</label>
              <input type="text" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="input w-full" placeholder="e.g. Web Server, Database, Dev Box" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Icon</label>
              <select value={form.icon} onChange={(e) => setForm({ ...form, icon: e.target.value })} className="input w-full">
                {ICON_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Description</label>
            <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="input w-full" rows={2} placeholder="What is this template for?" />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">vCPUs</label>
              <input type="number" min={1} max={128} value={form.vcpus} onChange={(e) => setForm({ ...form, vcpus: parseInt(e.target.value) || 1 })} className="input w-full" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">RAM (MB)</label>
              <input type="number" min={256} step={256} value={form.ram_mb} onChange={(e) => setForm({ ...form, ram_mb: parseInt(e.target.value) || 1024 })} className="input w-full" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Disk (GB)</label>
              <input type="number" min={5} value={form.disk_size_gb} onChange={(e) => setForm({ ...form, disk_size_gb: parseInt(e.target.value) || 20 })} className="input w-full" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Cloud Image</label>
              <select value={form.cloud_image_id} onChange={(e) => setForm({ ...form, cloud_image_id: e.target.value })} className="input w-full">
                <option value="">— Any —</option>
                {images.map((img) => (
                  <option key={img.id} value={img.id}>{img.name} ({img.os_type} {img.os_version})</option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Default User</label>
              <input type="text" value={form.default_user} onChange={(e) => setForm({ ...form, default_user: e.target.value })} className="input w-full" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Keyboard</label>
              <select value={form.keyboard_layout} onChange={(e) => setForm({ ...form, keyboard_layout: e.target.value })} className="input w-full">
                <option value="de">German</option>
                <option value="ch">Swiss</option>
                <option value="us">US English</option>
                <option value="fr">French</option>
                <option value="gb">UK English</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Timezone</label>
              <select value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })} className="input w-full">
                <option value="Europe/Berlin">Berlin</option>
                <option value="Europe/Zurich">Zurich</option>
                <option value="Europe/Vienna">Vienna</option>
                <option value="Europe/London">London</option>
                <option value="UTC">UTC</option>
                <option value="America/New_York">New York</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Locale</label>
              <input type="text" value={form.locale} onChange={(e) => setForm({ ...form, locale: e.target.value })} className="input w-full" placeholder="auto" />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Packages (comma-separated)</label>
            <input type="text" value={form.packages} onChange={(e) => setForm({ ...form, packages: e.target.value })} className="input w-full" placeholder="nginx, htop, curl" />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Run commands (one per line)</label>
            <textarea value={form.runcmd} onChange={(e) => setForm({ ...form, runcmd: e.target.value })} className="input w-full font-mono text-xs" rows={3} placeholder="systemctl enable --now nginx" />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">SSH Keys (one per line)</label>
            <textarea value={form.ssh_keys} onChange={(e) => setForm({ ...form, ssh_keys: e.target.value })} className="input w-full font-mono text-xs" rows={2} placeholder="ssh-ed25519 AAAA..." />
          </div>

          {user?.is_superadmin && (
            <div className="flex items-center gap-2">
              <input type="checkbox" checked={form.is_global} onChange={(e) => setForm({ ...form, is_global: e.target.checked })} className="rounded border-gray-300 dark:border-gray-600 text-primary-600 focus:ring-primary-500" />
              <label className="text-sm text-gray-700 dark:text-gray-300">Global template (available to all tenants)</label>
            </div>
          )}

          <div className="flex gap-3">
            <button type="submit" disabled={saving} className="btn btn-primary">
              {saving ? 'Saving...' : editingId ? 'Update' : 'Create'}
            </button>
            <button type="button" onClick={() => { setShowForm(false); setEditingId(null); }} className="btn btn-secondary">
              Cancel
            </button>
          </div>
        </form>
      )}

      {loading ? (
        <div className="text-center py-12 text-gray-400">Loading...</div>
      ) : templates.length === 0 ? (
        <div className="card p-12 text-center">
          <ServerStackIcon className="mx-auto h-12 w-12 text-gray-300 dark:text-gray-600 mb-4" />
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-1">No templates yet</h3>
          <p className="text-sm text-gray-500 dark:text-gray-400">Create your first VM template to speed up VM creation.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {templates.map((t) => {
            const IconComp = getIconComponent(t.icon);
            const imageName = getImageName(t.cloud_image_id);
            return (
              <div key={t.id} className="card p-5 hover:border-primary-300 dark:hover:border-primary-700 transition-colors">
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <div className="p-2 rounded-lg bg-primary-50 dark:bg-primary-900/20">
                      <IconComp className="h-6 w-6 text-primary-600 dark:text-primary-400" />
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold text-gray-900 dark:text-white">{t.name}</h3>
                      {t.is_global && (
                        <span className="text-xs text-primary-600 dark:text-primary-400 font-medium">Global</span>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-1">
                    <button onClick={() => handleEdit(t)} className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300" title="Edit">
                      <PencilIcon className="h-4 w-4" />
                    </button>
                    <button onClick={() => handleDelete(t)} className="p-1 text-gray-400 hover:text-red-500" title="Delete">
                      <TrashIcon className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {t.description && (
                  <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">{t.description}</p>
                )}

                <div className="space-y-1.5 text-xs">
                  <div className="flex justify-between text-gray-600 dark:text-gray-300">
                    <span>Hardware</span>
                    <span className="font-medium">{t.vcpus} vCPU · {formatRam(t.ram_mb)} · {t.disk_size_gb} GB</span>
                  </div>
                  {imageName && (
                    <div className="flex justify-between text-gray-600 dark:text-gray-300">
                      <span>Image</span>
                      <span className="font-medium truncate ml-2">{imageName}</span>
                    </div>
                  )}
                  {(t.packages && t.packages.length > 0) && (
                    <div className="flex justify-between text-gray-600 dark:text-gray-300">
                      <span>Packages</span>
                      <span className="font-mono truncate ml-2">{t.packages.join(', ')}</span>
                    </div>
                  )}
                  <div className="flex justify-between text-gray-600 dark:text-gray-300">
                    <span>Locale</span>
                    <span className="font-medium">{t.keyboard_layout.toUpperCase()} · {t.timezone.split('/')[1] || t.timezone}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
