import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink } from "react-router-dom";

import { UsageBadge } from "./UsageBadge";

export function Layout({ children }: { children: ReactNode }) {
  const { t, i18n } = useTranslation();

  function changeLanguage(language: string) {
    void i18n.changeLanguage(language);
    if (typeof window !== "undefined") {
      window.localStorage.setItem("chess.lang", language);
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
          <UsageBadge />
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
