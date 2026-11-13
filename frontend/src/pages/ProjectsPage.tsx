import { useEffect, useState, useCallback } from 'react';
import {
  PlusIcon,
  PencilIcon,
  TrashIcon,
  FolderIcon,
  ServerStackIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { useTenantStore } from '@/store/tenantStore';
import { projectApi, vmApi } from '@/services/api';
import type { Project, VirtualMachine } from '@/types';

export function ProjectsPage() {
  const { currentTenant } = useTenantStore();
  const [projects, setProjects] = useState<Project[]>([]);
  const [vms, setVms] = useState<VirtualMachine[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingProject, setEditingProject] = useState<Project | null>(null);
  const [form, setForm] = useState({ name: '', description: '' });

  const loadData = useCallback(async () => {
    if (!currentTenant) return;
    try {
      const [projData, vmData] = await Promise.all([
        projectApi.list(currentTenant.id),
        vmApi.list(currentTenant.id, { page_size: 100 }),
      ]);
      setProjects(projData);
      setVms(vmData.items);
    } catch {
      toast.error('Data could not be loaded');
    } finally {
      setIsLoading(false);
    }
  }, [currentTenant]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentTenant) return;

    try {
      if (editingProject) {
        await projectApi.update(currentTenant.id, editingProject.id, form);
        toast.success('Project updated');
      } else {
        await projectApi.create(currentTenant.id, form);
        toast.success('Project created');
      }
      setShowForm(false);
      setEditingProject(null);
      setForm({ name: '', description: '' });
      loadData();
    } catch (error: unknown) {
      const msg = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Error saving';
      toast.error(msg);
    }
  };

  const handleEdit = (project: Project) => {
    setEditingProject(project);
    setForm({ name: project.name, description: project.description ?? '' });
    setShowForm(true);
  };

  const handleDelete = async (project: Project) => {
    if (!currentTenant) return;
    if (projects.length <= 1) {
      toast.error('Keep at least one project per tenant (usually Default).');
      return;
    }
    const vmCount = vms.filter((vm) => vm.project_id === project.id).length;
    if (vmCount > 0) {
      toast.error(`Project "${project.name}" still has ${vmCount} VM(s). Please delete all VMs first.`);
      return;
    }
    if (!confirm(`Really delete project "${project.name}"?`)) return;

    try {
      await projectApi.delete(currentTenant.id, project.id);
      toast.success('Project deleted');
      loadData();
    } catch {
      toast.error('Delete failed');
    }
  };

  const handleCancel = () => {
    setShowForm(false);
    setEditingProject(null);
    setForm({ name: '', description: '' });
  };

  // Count VMs per project
  const vmCountByProject = (projectId: string) =>
    vms.filter((vm) => vm.project_id === projectId).length;

  const runningVmsByProject = (projectId: string) =>
    vms.filter((vm) => vm.project_id === projectId && vm.status === 'running').length;

  if (!currentTenant) {
    return (
      <div className="text-center py-12 text-gray-500 dark:text-gray-400">
        Please select a tenant.
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="sm:flex sm:items-center sm:justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Projects</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Optional folders for VMs inside tenant <span className="font-medium text-gray-700 dark:text-gray-300">{currentTenant.name}</span>
            {' · '}{projects.length} project(s)
          </p>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400 max-w-2xl">
            Most tenants only need the auto-created <span className="font-medium">Default</span> project.
            Add more only if you want to split teams or environments (e.g. Prod / Dev). VM create hides the project field while there is just one.
          </p>
        </div>
        <button
          onClick={() => { setShowForm(!showForm); setEditingProject(null); setForm({ name: '', description: '' }); }}
          className="btn-primary mt-4 sm:mt-0"
        >
          <PlusIcon className="h-5 w-5 mr-1.5" />
          New Project
        </button>
      </div>

      {/* Form */}
      {showForm && (
        <div className="card p-6 mb-8">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            {editingProject ? 'Edit Project' : 'New Project'}
          </h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Project name *
                </label>
                <input
                  name="name"
                  value={form.name}
                  onChange={handleChange}
                  required
                  className="input-field"
                  placeholder="e.g. Webserver, Development, Staging"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Description
                </label>
                <input
                  name="description"
                  value={form.description}
                  onChange={handleChange}
                  className="input-field"
                  placeholder="Short project description"
                />
              </div>
            </div>
            <div className="flex justify-end gap-3">
              <button type="button" onClick={handleCancel} className="btn-secondary">
                Cancel
              </button>
              <button type="submit" className="btn-primary">
                {editingProject ? 'Save' : 'Create'}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Projects Grid */}
      {isLoading ? (
        <div className="text-center py-12">
          <svg className="animate-spin h-8 w-8 mx-auto text-primary-500 mb-3" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <p className="text-sm text-gray-500 dark:text-gray-400">Loading...</p>
        </div>
      ) : projects.length === 0 ? (
        <div className="card p-12 text-center">
          <FolderIcon className="mx-auto h-12 w-12 text-gray-400 mb-3" />
          <h3 className="text-sm font-semibold text-gray-900 dark:text-white">No projects</h3>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Create a project to organize VMs.
          </p>
          <button
            onClick={() => setShowForm(true)}
            className="btn-primary mt-4 inline-flex"
          >
            <PlusIcon className="h-5 w-5 mr-1" />
            Create first project
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {projects.map((project) => {
            const totalVms = vmCountByProject(project.id);
            const runningVms = runningVmsByProject(project.id);
            return (
              <div key={project.id} className="card p-6 hover:shadow-lg transition-shadow">
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className="rounded-lg p-2.5 bg-primary-100 dark:bg-primary-900/30">
                      <FolderIcon className="h-6 w-6 text-primary-600 dark:text-primary-400" />
                    </div>
                    <div>
                      <h3 className="text-base font-semibold text-gray-900 dark:text-white">
                        {project.name}
                      </h3>
                      {project.description && (
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                          {project.description}
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-1">
                    <button
                      onClick={() => handleEdit(project)}
                      className="p-1.5 text-gray-400 hover:text-primary-600 transition-colors rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
                      title="Edit"
                    >
                      <PencilIcon className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => handleDelete(project)}
                      disabled={projects.length <= 1}
                      className="p-1.5 text-gray-400 hover:text-red-600 transition-colors rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40 disabled:hover:text-gray-400 disabled:hover:bg-transparent"
                      title={projects.length <= 1 ? 'Keep at least one project' : 'Delete'}
                    >
                      <TrashIcon className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {/* Stats */}
                <div className="flex items-center gap-4 pt-4 border-t border-gray-200 dark:border-gray-800">
                  <div className="flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400">
                    <ServerStackIcon className="h-4 w-4" />
                    <span>{totalVms} VM{totalVms !== 1 ? 's' : ''}</span>
                  </div>
                  {runningVms > 0 && (
                    <div className="flex items-center gap-1.5 text-sm text-green-600 dark:text-green-400">
                      <span className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
                      <span>{runningVms} running</span>
                    </div>
                  )}
                  <div className="ml-auto text-xs text-gray-400">
                    {new Date(project.created_at).toLocaleDateString('en-US')}
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
