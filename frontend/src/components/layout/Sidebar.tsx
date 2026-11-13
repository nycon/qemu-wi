import { NavLink } from 'react-router-dom';
import { clsx } from 'clsx';
import {
  ServerStackIcon,
  FolderIcon,
  GlobeAltIcon,
  CircleStackIcon,
  CloudIcon,
  ClipboardDocumentListIcon,
  ShieldCheckIcon,
  BuildingOfficeIcon,
  UsersIcon,
  Cog6ToothIcon,
  HomeIcon,
  DocumentTextIcon,
  RectangleGroupIcon,
  ShieldExclamationIcon,
  NoSymbolIcon,
  ScaleIcon,
  SignalIcon,
  ArchiveBoxIcon,
} from '@heroicons/react/24/outline';
import { useAuthStore } from '@/store/authStore';

const navigation = [
  { name: 'Dashboard', href: '/', icon: HomeIcon },
  { name: 'Virtual Machines', href: '/vms', icon: ServerStackIcon },
  { name: 'Projects', href: '/projects', icon: FolderIcon },
  { name: 'Networks', href: '/networks', icon: GlobeAltIcon },
  { name: 'Tasks', href: '/tasks', icon: ClipboardDocumentListIcon },
];

const adminNavigation = [
  { name: 'Tenants', href: '/admin/tenants', icon: BuildingOfficeIcon },
  { name: 'Users', href: '/admin/users', icon: UsersIcon },
  { name: 'VM Templates', href: '/admin/vm-templates', icon: RectangleGroupIcon },
  { name: 'Cloud Images', href: '/admin/images', icon: CloudIcon },
  { name: 'Storage Pools', href: '/admin/storage', icon: CircleStackIcon },
  { name: 'Public IPs', href: '/admin/ip-pools', icon: GlobeAltIcon },
  { name: 'Load Balancer', href: '/admin/load-balancer', icon: ScaleIcon },
  { name: 'Reverse Proxy', href: '/admin/reverse-proxy', icon: GlobeAltIcon },
  { name: 'Firewall / Egress', href: '/admin/firewall', icon: ShieldExclamationIcon },
  { name: 'IP Blacklist', href: '/admin/blacklist', icon: NoSymbolIcon },
  { name: 'Audit Logs', href: '/admin/audit', icon: ShieldCheckIcon },
  { name: 'Backups', href: '/admin/backups', icon: ArchiveBoxIcon },
  { name: 'Cluster', href: '/admin/cluster', icon: SignalIcon },
  { name: 'API Docs', href: '/admin/api-docs', icon: DocumentTextIcon },
];

export function Sidebar() {
  const user = useAuthStore((s) => s.user);

  return (
    <div className="flex grow flex-col gap-y-5 overflow-y-auto bg-gray-900 px-6 pb-4 w-72">
      {/* Logo */}
      <div className="flex h-16 shrink-0 items-center gap-3 border-b border-gray-800 pb-2">
        <ServerStackIcon className="h-8 w-8 text-primary-400" />
        <span className="text-lg font-bold text-white tracking-tight">
          QEMU Interface
        </span>
      </div>

      {/* Navigation */}
      <nav className="flex flex-1 flex-col">
        <ul className="flex flex-1 flex-col gap-y-7">
          {/* Main navigation */}
          <li>
            <div className="text-xs font-semibold leading-6 text-gray-400 uppercase tracking-wider">
              General
            </div>
            <ul className="-mx-2 mt-2 space-y-1">
              {navigation.map((item) => (
                <li key={item.name}>
                  <NavLink
                    to={item.href}
                    end={item.href === '/'}
                    className={({ isActive }) =>
                      clsx(
                        isActive
                          ? 'bg-gray-800 text-white'
                          : 'text-gray-400 hover:bg-gray-800 hover:text-white',
                        'group flex gap-x-3 rounded-lg p-2.5 text-sm leading-6 font-medium transition-colors'
                      )
                    }
                  >
                    <item.icon className="h-5 w-5 shrink-0" />
                    {item.name}
                  </NavLink>
                </li>
              ))}
            </ul>
          </li>

          {/* Admin navigation */}
          {user?.is_superadmin && (
            <li>
              <div className="text-xs font-semibold leading-6 text-gray-400 uppercase tracking-wider">
                Administration
              </div>
              <ul className="-mx-2 mt-2 space-y-1">
                {adminNavigation.map((item) => (
                  <li key={item.name}>
                    <NavLink
                      to={item.href}
                      className={({ isActive }) =>
                        clsx(
                          isActive
                            ? 'bg-gray-800 text-white'
                            : 'text-gray-400 hover:bg-gray-800 hover:text-white',
                          'group flex gap-x-3 rounded-lg p-2.5 text-sm leading-6 font-medium transition-colors'
                        )
                      }
                    >
                      <item.icon className="h-5 w-5 shrink-0" />
                      {item.name}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </li>
          )}

          {/* Settings (bottom) */}
          <li className="mt-auto">
            <NavLink
              to="/settings"
              className="group -mx-2 flex gap-x-3 rounded-lg p-2.5 text-sm font-medium leading-6 text-gray-400 hover:bg-gray-800 hover:text-white transition-colors"
            >
              <Cog6ToothIcon className="h-5 w-5 shrink-0" />
              Settings
            </NavLink>
          </li>
        </ul>
      </nav>
    </div>
  );
}
