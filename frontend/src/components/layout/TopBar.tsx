import { Fragment } from 'react';
import { Link } from 'react-router-dom';
import { Menu, Transition } from '@headlessui/react';
import {
  Bars3Icon,
  ChevronDownIcon,
  ArrowRightOnRectangleIcon,
  UserCircleIcon,
  SunIcon,
  MoonIcon,
} from '@heroicons/react/24/outline';
import { clsx } from 'clsx';
import { useAuthStore } from '@/store/authStore';
import { useTenantStore } from '@/store/tenantStore';
import { useThemeStore } from '@/store/themeStore';

export function TopBar() {
  const { user, logout } = useAuthStore();
  const { tenants, currentTenant, setCurrentTenant } = useTenantStore();
  const { toggleTheme, isDark } = useThemeStore();

  return (
    <div className="sticky top-0 z-40 flex h-16 shrink-0 items-center gap-x-4 border-b border-gray-200 bg-white px-4 shadow-sm sm:gap-x-6 sm:px-6 lg:px-8 dark:bg-gray-900 dark:border-gray-800">
      {/* Mobile sidebar toggle */}
      <button type="button" className="lg:hidden -m-2.5 p-2.5 text-gray-700 dark:text-gray-200">
        <Bars3Icon className="h-6 w-6" />
      </button>

      <div className="h-6 w-px bg-gray-200 dark:bg-gray-700 lg:hidden" />

      <div className="flex flex-1 gap-x-4 self-stretch lg:gap-x-6">
        {/* Tenant selector */}
        {tenants.length > 0 && (
          <div className="flex items-center">
            <Menu as="div" className="relative">
              <Menu.Button className="flex items-center gap-x-2 rounded-lg px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-800 transition-colors">
                <span className="text-xs text-gray-400">Tenant:</span>
                <span>{currentTenant?.name ?? 'Select'}</span>
                <ChevronDownIcon className="h-4 w-4 text-gray-400" />
              </Menu.Button>
              <Transition
                as={Fragment}
                enter="transition ease-out duration-100"
                enterFrom="transform opacity-0 scale-95"
                enterTo="transform opacity-100 scale-100"
                leave="transition ease-in duration-75"
                leaveFrom="transform opacity-100 scale-100"
                leaveTo="transform opacity-0 scale-95"
              >
                <Menu.Items className="absolute left-0 z-10 mt-2 w-56 origin-top-left rounded-lg bg-white shadow-lg ring-1 ring-black/5 focus:outline-none dark:bg-gray-800">
                  <div className="p-1">
                    {tenants.map((tenant) => (
                      <Menu.Item key={tenant.id}>
                        {({ active }) => (
                          <button
                            onClick={() => setCurrentTenant(tenant)}
                            className={clsx(
                              active ? 'bg-gray-100 dark:bg-gray-700' : '',
                              currentTenant?.id === tenant.id ? 'text-primary-600 font-semibold' : 'text-gray-700 dark:text-gray-200',
                              'block w-full text-left rounded-md px-3 py-2 text-sm transition-colors'
                            )}
                          >
                            {tenant.name}
                          </button>
                        )}
                      </Menu.Item>
                    ))}
                  </div>
                </Menu.Items>
              </Transition>
            </Menu>
          </div>
        )}

        {/* Spacer */}
        <div className="flex-1" />

        {/* Theme toggle + User menu */}
        <div className="flex items-center gap-x-3">
          <button
            onClick={toggleTheme}
            className="rounded-lg p-2 text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200 transition-colors"
            title={isDark() ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {isDark() ? <SunIcon className="h-5 w-5" /> : <MoonIcon className="h-5 w-5" />}
          </button>

          <div className="h-6 w-px bg-gray-200 dark:bg-gray-700" />

          <Menu as="div" className="relative">
            <Menu.Button className="flex items-center gap-x-2 -m-1.5 p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
              <UserCircleIcon className="h-8 w-8 text-gray-400" />
              <span className="hidden lg:flex lg:items-center">
                <span className="text-sm font-medium text-gray-700 dark:text-gray-200">
                  {user?.full_name ?? user?.username}
                </span>
                <ChevronDownIcon className="ml-2 h-4 w-4 text-gray-400" />
              </span>
            </Menu.Button>
            <Transition
              as={Fragment}
              enter="transition ease-out duration-100"
              enterFrom="transform opacity-0 scale-95"
              enterTo="transform opacity-100 scale-100"
              leave="transition ease-in duration-75"
              leaveFrom="transform opacity-100 scale-100"
              leaveTo="transform opacity-0 scale-95"
            >
              <Menu.Items className="absolute right-0 z-10 mt-2.5 w-52 origin-top-right rounded-lg bg-white py-2 shadow-lg ring-1 ring-black/5 focus:outline-none dark:bg-gray-800">
                <div className="px-3 py-2 text-xs text-gray-400 border-b border-gray-100 dark:border-gray-700">
                  {user?.email}
                  {user?.is_superadmin && (
                    <span className="ml-1 inline-flex items-center rounded-full bg-primary-50 px-2 py-0.5 text-xs font-medium text-primary-700 dark:bg-primary-900/30 dark:text-primary-400">
                      Admin
                    </span>
                  )}
                </div>
                <Menu.Item>
                  {({ active }) => (
                    <Link
                      to="/profile"
                      className={clsx(
                        active ? 'bg-gray-50 dark:bg-gray-700' : '',
                        'flex w-full items-center gap-x-2 px-3 py-2 text-sm text-gray-700 dark:text-gray-200'
                      )}
                    >
                      <UserCircleIcon className="h-4 w-4" />
                      User Profile
                    </Link>
                  )}
                </Menu.Item>
                <Menu.Item>
                  {({ active }) => (
                    <button
                      onClick={() => { void logout(); }}
                      className={clsx(
                        active ? 'bg-gray-50 dark:bg-gray-700' : '',
                        'flex w-full items-center gap-x-2 px-3 py-2 text-sm text-gray-700 dark:text-gray-200'
                      )}
                    >
                      <ArrowRightOnRectangleIcon className="h-4 w-4" />
                      Sign Out
                    </button>
                  )}
                </Menu.Item>
              </Menu.Items>
            </Transition>
          </Menu>
        </div>
      </div>
    </div>
  );
}
