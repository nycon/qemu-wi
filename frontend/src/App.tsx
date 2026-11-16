import { useEffect } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from 'react-hot-toast';

import { useAuthStore } from '@/store/authStore';
import { useTenantStore } from '@/store/tenantStore';
import { AppLayout } from '@/components/layout/AppLayout';
import { LoginPage } from '@/pages/LoginPage';
import { DashboardPage } from '@/pages/DashboardPage';
import { VMListPage } from '@/pages/VMListPage';
import { VMCreatePage } from '@/pages/VMCreatePage';
import { VMDetailPage } from '@/pages/VMDetailPage';
import { ConsolePage } from '@/pages/ConsolePage';
import { TasksPage } from '@/pages/TasksPage';
import { ProjectsPage } from '@/pages/ProjectsPage';
import { CloudImagesPage } from '@/pages/admin/CloudImagesPage';
import { StoragePoolsPage } from '@/pages/admin/StoragePoolsPage';
import { IPPoolsPage } from '@/pages/admin/IPPoolsPage';
import { NetworksPage } from '@/pages/NetworksPage';
import { TenantsPage } from '@/pages/admin/TenantsPage';
import { UsersPage } from '@/pages/admin/UsersPage';
import { AuditLogsPage } from '@/pages/admin/AuditLogsPage';
import { ReverseProxyPage } from '@/pages/admin/ReverseProxyPage';
import { ApiDocsPage } from '@/pages/admin/ApiDocsPage';
import { VMImportPage } from '@/pages/VMImportPage';
import { VMTemplatesPage } from '@/pages/admin/VMTemplatesPage';
import { FirewallPage } from '@/pages/admin/FirewallPage';
import { BlacklistPage } from '@/pages/admin/BlacklistPage';
import { LoadBalancerPage } from '@/pages/admin/LoadBalancerPage';
import { ClusterPage } from '@/pages/admin/ClusterPage';
import { BackupsPage } from '@/pages/admin/BackupsPage';
import { SettingsPage } from '@/pages/SettingsPage';
import { ProfilePage } from '@/pages/ProfilePage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function AppInit() {
  const { isAuthenticated, loadUser } = useAuthStore();
  const { loadTenants } = useTenantStore();

  useEffect(() => {
    loadUser();
  }, [loadUser]);

  useEffect(() => {
    if (isAuthenticated) {
      loadTenants();
    }
  }, [isAuthenticated, loadTenants]);

  return null;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppInit />
        <Routes>
          {/* Public routes */}
          <Route path="/login" element={<LoginPage />} />

          {/* Console popup — no sidebar/topbar so the window is independent of the panel */}
          <Route path="/vms/:vmId/console" element={<ConsolePage />} />

          {/* Protected routes */}
          <Route element={<AppLayout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/vms" element={<VMListPage />} />
            <Route path="/vms/create" element={<VMCreatePage />} />
            <Route path="/vms/import" element={<VMImportPage />} />
            <Route path="/vms/:vmId" element={<VMDetailPage />} />
            <Route path="/tasks" element={<TasksPage />} />
            {/* Admin pages */}
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/networks" element={<NetworksPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/profile" element={<ProfilePage />} />
            <Route path="/admin/tenants" element={<TenantsPage />} />
            <Route path="/admin/users" element={<UsersPage />} />
            <Route path="/admin/images" element={<CloudImagesPage />} />
            <Route path="/admin/storage" element={<StoragePoolsPage />} />
            <Route path="/admin/ip-pools" element={<IPPoolsPage />} />
            <Route path="/admin/load-balancer" element={<LoadBalancerPage />} />
            <Route path="/admin/reverse-proxy" element={<ReverseProxyPage />} />
            <Route path="/admin/vm-templates" element={<VMTemplatesPage />} />
            <Route path="/admin/firewall" element={<FirewallPage />} />
            <Route path="/admin/blacklist" element={<BlacklistPage />} />
            <Route path="/admin/audit" element={<AuditLogsPage />} />
            <Route path="/admin/cluster" element={<ClusterPage />} />
            <Route path="/admin/backups" element={<BackupsPage />} />
            <Route path="/admin/api-docs" element={<ApiDocsPage />} />
          </Route>
        </Routes>
        <Toaster
          position="top-right"
          toastOptions={{
            className: 'dark:bg-gray-800 dark:text-white',
            duration: 4000,
          }}
        />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
