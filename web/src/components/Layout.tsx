import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, useNavigate } from "react-router-dom";

import { logoutUser } from "../api";
import { selectIsAuthenticated, useAuthStore } from "../stores/authStore";
import { UsageBadge } from "./UsageBadge";

export function Layout({ children }: { children: ReactNode }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const isAuthenticated = useAuthStore(selectIsAuthenticated);
  const user = useAuthStore((state) => state.user);
  const clearSession = useAuthStore((state) => state.clearSession);

  function changeLanguage(language: string) {
    void i18n.changeLanguage(language);
    if (typeof window !== "undefined") {
      window.localStorage.setItem("chess.lang", language);
    }
  }

  async function handleLogout() {
    // POST /auth/logout чистит HttpOnly cookie; локальную сессию — всегда.
    try {
      await logoutUser();
    } finally {
      clearSession();
      navigate("/login", { replace: true });
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <Link to="/" className="brand">
            <h1>{t("app.title")}</h1>
          </Link>
          <p className="topbar__subtitle">{t("app.subtitle")}</p>
          <nav className="topbar__nav" aria-label={t("nav.label")}>
            <NavLink to="/" end>
              {t("nav.home")}
            </NavLink>
            <NavLink to="/history">{t("nav.history")}</NavLink>
          </nav>
        </div>
        <div className="topbar__right">
          {isAuthenticated ? (
            <div className="auth-user">
              <span className="auth-user__email" title={user?.email}>
                {t("auth.loggedInAs", {
                  email: user?.email ?? t("auth.guest"),
                })}
              </span>
              <button
                type="button"
                className="button button--ghost button--small"
                onClick={() => {
                  void handleLogout();
                }}
              >
                {t("auth.logout")}
              </button>
            </div>
          ) : (
            <NavLink
              to="/login"
              className="button button--ghost button--small auth-login-link"
            >
              {t("auth.loginLink")}
            </NavLink>
          )}
          {isAuthenticated ? <UsageBadge /> : null}
          <span className="badge">{t("app.badge")}</span>
          <div className="lang-switch" role="group" aria-label="Language">
            {(["ru", "en"] as const).map((language) => (
              <button
                key={language}
                type="button"
                className={`lang-switch__button ${
                  i18n.language === language
                    ? "lang-switch__button--active"
                    : ""
                }`}
                onClick={() => changeLanguage(language)}
              >
                {language.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      </header>

      {children}

      <footer className="footer">
        <span>{t("app.footer")}</span>
        <span>{t("app.footerNote")}</span>
      </footer>
    </div>
  );
}
