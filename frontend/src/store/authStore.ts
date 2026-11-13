import { create } from 'zustand';
import type { User } from '@/types';
import { authApi } from '@/services/api';
import { clearAccessToken, setAccessToken } from '@/services/tokenMemory';

interface AuthState {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  // 2FA pending state (temp token stays in memory only)
  pending2fa: boolean;
  tempToken: string | null;
  login: (username: string, password: string, totpCode?: string) => Promise<void>;
  verify2fa: (totpCode: string) => Promise<void>;
  logout: () => Promise<void>;
  loadUser: () => Promise<void>;
  setUser: (user: User) => void;
  clearPending2fa: () => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  isAuthenticated: false,
  isLoading: false,
  pending2fa: false,
  tempToken: null,

  login: async (username: string, password: string, totpCode?: string) => {
    set({ isLoading: true });
    try {
      const tokens = await authApi.login({ username, password, totp_code: totpCode });

      // 2FA required?
      if (tokens.requires_2fa && tokens.temp_token) {
        set({
          isLoading: false,
          pending2fa: true,
          tempToken: tokens.temp_token,
        });
        return;
      }

      setAccessToken(tokens.access_token || null);
      const user = await authApi.me();
      set({ user, isAuthenticated: true, isLoading: false, pending2fa: false, tempToken: null });
    } catch (error) {
      clearAccessToken();
      set({ isLoading: false });
      throw error;
    }
  },

  verify2fa: async (totpCode: string) => {
    const { tempToken } = get();
    if (!tempToken) throw new Error('No temp token');

    set({ isLoading: true });
    try {
      const tokens = await authApi.verify2fa({ temp_token: tempToken, totp_code: totpCode });
      setAccessToken(tokens.access_token || null);
      const user = await authApi.me();
      set({ user, isAuthenticated: true, isLoading: false, pending2fa: false, tempToken: null });
    } catch (error) {
      set({ isLoading: false });
      throw error;
    }
  },

  clearPending2fa: () => set({ pending2fa: false, tempToken: null }),

  logout: async () => {
    try {
      await authApi.logout();
    } catch {
      // ignore network errors on logout
    }
    clearAccessToken();
    set({ user: null, isAuthenticated: false, pending2fa: false, tempToken: null });
  },

  setUser: (user: User) => set({ user }),

  loadUser: async () => {
    set({ isLoading: true });
    try {
      // Prefer silent refresh via httpOnly cookie (survives page reload)
      try {
        const tokens = await authApi.refresh();
        if (tokens.access_token) {
          setAccessToken(tokens.access_token);
        }
      } catch {
        // No valid refresh cookie — fall through to /me (may still have access cookie)
      }

      const user = await authApi.me();
      set({ user, isAuthenticated: true, isLoading: false });
    } catch {
      clearAccessToken();
      set({ user: null, isAuthenticated: false, isLoading: false });
    }
  },
}));
