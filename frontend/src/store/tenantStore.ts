import { create } from 'zustand';
import type { Tenant } from '@/types';
import { tenantApi } from '@/services/api';

interface TenantState {
  tenants: Tenant[];
  currentTenant: Tenant | null;
  isLoading: boolean;
  loadTenants: () => Promise<void>;
  setCurrentTenant: (tenant: Tenant) => void;
}

export const useTenantStore = create<TenantState>((set) => ({
  tenants: [],
  currentTenant: null,
  isLoading: false,

  loadTenants: async () => {
    set({ isLoading: true });
    try {
      const tenants = await tenantApi.list();
      const currentTenantId = localStorage.getItem('current_tenant_id');
      const current = tenants.find((t) => t.id === currentTenantId) ?? tenants[0] ?? null;
      set({ tenants, currentTenant: current, isLoading: false });
    } catch {
      set({ isLoading: false });
    }
  },

  setCurrentTenant: (tenant: Tenant) => {
    localStorage.setItem('current_tenant_id', tenant.id);
    set({ currentTenant: tenant });
  },
}));
