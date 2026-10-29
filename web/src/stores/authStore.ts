import { create } from "zustand";

import { refreshSession } from "../api";
import type { AuthTokens, AuthUser } from "../types";

/**
 * Сессия — только в памяти (KI-037): access-токен и профиль не пишутся в
 * localStorage, а refresh-токен живёт в HttpOnly cookie и JS его не видит.
 * После перезагрузки страницы сессия восстанавливается через
 * `bootstrapAuth()` → `POST /auth/refresh`.
 */
export interface AuthState {
  accessToken: string | null;
  user: AuthUser | null;
  /** Зеркало `Boolean(accessToken)` для удобных подписок. */
  isAuthenticated: boolean;
  /** Пока bootstrap через `/auth/refresh` не завершён — AuthGate показывает loading. */
  initializing: boolean;
  setSession: (tokens: AuthTokens, user?: AuthUser | null) => void;
  setUser: (user: AuthUser) => void;
  clearSession: () => void;
  /** Один `POST /auth/refresh` при старте приложения (single-flight). */
  bootstrapAuth: () => Promise<void>;
}

export const useAuthStore = create<AuthState>()((set, get) => ({
  accessToken: null,
  user: null,
  isAuthenticated: false,
  // Стартуем «в процессе bootstrap»: App вызывает bootstrapAuth() до рендера,
  // а AuthGate до его завершения не должен редиректить на /login.
  initializing: true,
  setSession: (tokens, user) => {
    set({
      accessToken: tokens.access_token,
      // `undefined` — обновляем только access-токен, профиль не трогаем.
      user: user === undefined ? get().user : user,
      isAuthenticated: Boolean(tokens.access_token),
    });
  },
  setUser: (user) => {
    set({ user });
  },
  clearSession: () => {
    set({
      accessToken: null,
      user: null,
      isAuthenticated: false,
    });
  },
  bootstrapAuth: () => bootstrapAuth(),
}));

/** Селектор «есть действующий access-токен». */
export const selectIsAuthenticated = (state: AuthState): boolean =>
  Boolean(state.accessToken);

/** Короткий алиас селектора для `useAuthStore(isAuthenticated)`. */
export const isAuthenticated = selectIsAuthenticated;

/** Не-хук доступ к состоянию для api.ts (импорт authStore → api циклический, но безопасный). */
export function getAuthState(): AuthState {
  return useAuthStore.getState();
}

/**
 * Один общий bootstrap на всё приложение: React.StrictMode/HMR могут вызвать
 * `bootstrapAuth()` повторно, но `POST /auth/refresh` уйдёт только один раз
 * (и `refreshSession()` в api.ts тоже single-flight).
 */
let bootstrapPromise: Promise<void> | null = null;

async function runBootstrap(): Promise<void> {
  useAuthStore.setState({ initializing: true });
  try {
    // 200 → сессия выставлена в api.ts; 401/сеть → refreshSession очистит её.
    await refreshSession();
  } catch {
    // Нет/просрочен/отозван refresh-cookie или backend недоступен — аноним.
    useAuthStore.getState().clearSession();
  } finally {
    useAuthStore.setState({ initializing: false });
  }
}

/**
 * Восстанавливает сессию по HttpOnly refresh-cookie. Вызывается один раз при
 * старте приложения (App.tsx) до первого рендера.
 */
export function bootstrapAuth(): Promise<void> {
  if (!bootstrapPromise) {
    bootstrapPromise = runBootstrap().finally(() => {
      bootstrapPromise = null;
    });
  }
  return bootstrapPromise;
}
