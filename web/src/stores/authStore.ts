import { create } from "zustand";

import type { AuthTokens, AuthUser } from "../types";

/** Ключ localStorage с сохранённой сессией. */
export const AUTH_STORAGE_KEY = "chess.auth";

export interface PersistedAuth {
  accessToken: string | null;
  refreshToken: string | null;
  user: AuthUser | null;
}

export interface AuthState extends PersistedAuth {
  /** Зеркало `Boolean(accessToken)` для удобных подписок. */
  isAuthenticated: boolean;
  setSession: (tokens: AuthTokens, user?: AuthUser | null) => void;
  setUser: (user: AuthUser) => void;
  clearSession: () => void;
}

/**
 * localStorage может отсутствовать (Vitest/node, приватный режим, SSR).
 * Обращаемся к `window.localStorage` в браузере и к глобальному `localStorage`
 * в тестах, где window не объявлен.
 */
function getAuthStorage(): Storage | null {
  try {
    if (typeof window !== "undefined") {
      const candidate = (window as Window & { localStorage?: Storage })
        .localStorage;
      if (candidate) {
        return candidate;
      }
    }
  } catch {
    // Доступ к localStorage запрещён (iframe без allow-same-origin).
  }
  try {
    if (typeof localStorage !== "undefined") {
      return localStorage;
    }
  } catch {
    // Нет глобального localStorage.
  }
  return null;
}

function normalizeUser(value: unknown): AuthUser | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const candidate = value as Partial<AuthUser>;
  if (typeof candidate.email !== "string" || !candidate.email) {
    return null;
  }
  return candidate as AuthUser;
}

function readStoredAuth(): PersistedAuth | null {
  const storage = getAuthStorage();
  if (!storage) {
    return null;
  }
  let raw: string | null;
  try {
    raw = storage.getItem(AUTH_STORAGE_KEY);
  } catch {
    return null;
  }
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<PersistedAuth> | null;
    if (!parsed || typeof parsed !== "object") {
      return null;
    }
    const accessToken =
      typeof parsed.accessToken === "string" && parsed.accessToken
        ? parsed.accessToken
        : null;
    const refreshToken =
      typeof parsed.refreshToken === "string" && parsed.refreshToken
        ? parsed.refreshToken
        : null;
    if (!accessToken && !refreshToken) {
      return null;
    }
    return {
      accessToken,
      refreshToken,
      user: normalizeUser(parsed.user),
    };
  } catch {
    return null;
  }
}

function writeStoredAuth(state: PersistedAuth): void {
  const storage = getAuthStorage();
  if (!storage) {
    return;
  }
  try {
    if (!state.accessToken && !state.refreshToken) {
      storage.removeItem(AUTH_STORAGE_KEY);
      return;
    }
    storage.setItem(
      AUTH_STORAGE_KEY,
      JSON.stringify({
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        user: state.user,
      }),
    );
  } catch {
    // Квота/запрет записи — сессия остаётся только в памяти.
  }
}

function removeStoredAuth(): void {
  const storage = getAuthStorage();
  if (!storage) {
    return;
  }
  try {
    storage.removeItem(AUTH_STORAGE_KEY);
  } catch {
    // Ничего не остаётся, кроме как игнорировать.
  }
}

export const useAuthStore = create<AuthState>()((set, get) => ({
  accessToken: null,
  refreshToken: null,
  user: null,
  isAuthenticated: false,
  setSession: (tokens, user) => {
    const next: PersistedAuth = {
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
      // `undefined` — обновляем только токены, профиль не трогаем.
      user: user === undefined ? get().user : user,
    };
    set({
      ...next,
      isAuthenticated: Boolean(next.accessToken),
    });
    writeStoredAuth(next);
  },
  setUser: (user) => {
    set({ user });
    const { accessToken, refreshToken } = get();
    writeStoredAuth({ accessToken, refreshToken, user });
  },
  clearSession: () => {
    set({
      accessToken: null,
      refreshToken: null,
      user: null,
      isAuthenticated: false,
    });
    removeStoredAuth();
  },
}));

/** Селектор «есть действующий access-токен». */
export const selectIsAuthenticated = (state: AuthState): boolean =>
  Boolean(state.accessToken);

/** Короткий алиас селектора для `useAuthStore(isAuthenticated)`. */
export const isAuthenticated = selectIsAuthenticated;

/** Не-хук доступ к состоянию для api.ts (без циклического импорта). */
export function getAuthState(): AuthState {
  return useAuthStore.getState();
}

/**
 * Восстанавливает сессию из localStorage. Вызывается один раз при старте
 * приложения (App.tsx) до первого рендера.
 */
export function hydrateAuth(): PersistedAuth | null {
  const stored = readStoredAuth();
  if (!stored) {
    return null;
  }
  useAuthStore.setState({
    accessToken: stored.accessToken,
    refreshToken: stored.refreshToken,
    user: stored.user,
    isAuthenticated: Boolean(stored.accessToken),
  });
  return stored;
}
