import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { logoutAllUser, logoutUser } from "../api";
import { useAuthStore } from "../stores/authStore";

/**
 * Выпадающее меню профиля в шапке (KI-055).
 *
 * Пункты: обычный выход и «Выйти со всех устройств» (`POST /auth/logout-all`
 * отзывает все refresh-токены пользователя). Меню закрывается по клику вне
 * и по Escape; после выхода сессия очищается и происходит редирект на
 * `/login`.
 */
export function ProfileMenu() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    function handlePointerDown(event: MouseEvent) {
      const target = event.target;
      if (
        target instanceof Node &&
        containerRef.current &&
        !containerRef.current.contains(target)
      ) {
        setOpen(false);
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  async function signOut(allDevices: boolean) {
    setOpen(false);
    if (allDevices) {
      await logoutAllUser();
    } else {
      await logoutUser();
    }
    navigate("/login", { replace: true });
  }

  return (
    <div className="profile-menu" ref={containerRef}>
      <button
        type="button"
        className="button button--ghost button--small profile-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="profile-menu__label" title={user?.email}>
          {t("auth.loggedInAs", { email: user?.email ?? t("auth.guest") })}
        </span>
        <span className="profile-menu__caret" aria-hidden="true">
          ▾
        </span>
      </button>

      {open ? (
        <div
          className="profile-menu__list"
          role="menu"
          aria-label={t("auth.profileMenu")}
        >
          <button
            type="button"
            role="menuitem"
            className="profile-menu__item"
            onClick={() => {
              void signOut(false);
            }}
          >
            {t("auth.logout")}
          </button>
          <button
            type="button"
            role="menuitem"
            className="profile-menu__item profile-menu__item--danger"
            onClick={() => {
              void signOut(true);
            }}
          >
            {t("auth.logoutAll")}
          </button>
        </div>
      ) : null}
    </div>
  );
}
