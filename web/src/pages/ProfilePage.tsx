import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import {
  ApiError,
  changePassword,
  createCheckoutSession,
  createPortalSession,
  updateProfile,
} from "../api";
import { useAuthStore } from "../stores/authStore";
import type { Level } from "../types";

/** Уровни в порядке «от новичка к гроссмейстеру» (значения = `Level`). */
const LEVELS: readonly Level[] = ["beginner", "club", "gm"];

/** Минимальная длина пароля — как у регистрации (T-028). */
const MIN_PASSWORD_LENGTH = 8;

/** Максимальная длина имени — как в контракте `PATCH /auth/me` (T-082). */
const MAX_DISPLAY_NAME_LENGTH = 120;

/**
 * `/profile` — имя, предпочитаемый уровень и смена пароля (T-082).
 *
 * Страница приватная: AuthGate гарантирует, что `user` уже загружен, поэтому
 * здесь остаётся только форма. Сохранение профиля обновляет authStore, чтобы
 * шапка и остальные экраны сразу увидели новое имя/уровень.
 */
export default function ProfilePage() {
  const { t } = useTranslation();
  const user = useAuthStore((state) => state.user);
  const setUser = useAuthStore((state) => state.setUser);

  const [name, setName] = useState(user?.display_name ?? "");
  const [level, setLevel] = useState<Level>(user?.level ?? "club");
  //: Имя отправляем, только если его действительно правили: иначе `PATCH`
  //: не должен трогать `display_name` (контракт: отсутствие поля = не менять).
  const [nameEdited, setNameEdited] = useState(false);
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profileSaved, setProfileSaved] = useState(false);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordChanged, setPasswordChanged] = useState(false);

  const [billingLoading, setBillingLoading] = useState(false);
  const [billingError, setBillingError] = useState<string | null>(null);

  // AuthGate не рендерит страницу без профиля; запасной выход для типов.
  if (!user) {
    return null;
  }

  function errorMessage(cause: unknown): string {
    return cause instanceof Error && cause.message
      ? cause.message
      : t("common.unknown");
  }

  function resetProfileFeedback() {
    setProfileSaved(false);
    setProfileError(null);
  }

  async function handleProfileSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (savingProfile) {
      return;
    }
    const payload: { display_name?: string; level?: Level } = { level };
    const trimmedName = name.trim();
    //: Пустое имя не отправляем: backend трактует отсутствие `display_name`
    //: как «не менять» (стереть имя через API нельзя, пустая строка — 422).
    if (nameEdited && trimmedName) {
      payload.display_name = trimmedName;
    }
    setSavingProfile(true);
    setProfileError(null);
    setProfileSaved(false);
    try {
      const profile = await updateProfile(payload);
      setUser(profile);
      setName(profile.display_name ?? "");
      setLevel(profile.level);
      setNameEdited(false);
      setProfileSaved(true);
    } catch (cause) {
      setProfileError(errorMessage(cause));
    } finally {
      setSavingProfile(false);
    }
  }

  function resetPasswordFeedback() {
    setPasswordError(null);
    setPasswordChanged(false);
  }

  async function handlePasswordSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (changingPassword) {
      return;
    }
    // Клиентские проверки: запрос не отправляем, если форма заведомо неверна.
    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      setPasswordError(t("profile.password.tooShort"));
      return;
    }
    if (newPassword !== confirmation) {
      setPasswordError(t("profile.password.mismatch"));
      return;
    }
    setChangingPassword(true);
    setPasswordError(null);
    setPasswordChanged(false);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmation("");
      setPasswordChanged(true);
    } catch (cause) {
      if (
        cause instanceof ApiError &&
        (cause.status === 401 || cause.code === "invalid_credentials")
      ) {
        setPasswordError(t("profile.password.wrong"));
      } else if (cause instanceof ApiError && cause.status === 422) {
        setPasswordError(t("profile.password.tooShort"));
      } else {
        setPasswordError(errorMessage(cause));
      }
    } finally {
      setChangingPassword(false);
    }
  }

  async function openBilling(kind: "checkout" | "portal") {
    if (billingLoading) {
      return;
    }
    setBillingLoading(true);
    setBillingError(null);
    try {
      const { url } =
        kind === "checkout"
          ? await createCheckoutSession()
          : await createPortalSession();
      window.location.href = url;
    } catch (cause) {
      setBillingError(errorMessage(cause));
      setBillingLoading(false);
    }
  }

  return (
    <main className="page">
      <div className="page__head">
        <div>
          <h1>{t("profile.title")}</h1>
          <p className="muted">{t("profile.subtitle")}</p>
        </div>
      </div>

      <form className="panel" onSubmit={handleProfileSubmit} noValidate>
        <div className="panel__header">
          <p className="muted">{user.email}</p>
        </div>

        <div className="field">
          <label htmlFor="profile-name">{t("profile.name")}</label>
          <input
            id="profile-name"
            className="auth-input"
            type="text"
            name="display_name"
            autoComplete="name"
            maxLength={MAX_DISPLAY_NAME_LENGTH}
            value={name}
            placeholder={t("profile.namePlaceholder")}
            disabled={savingProfile}
            onChange={(event) => {
              setName(event.target.value);
              setNameEdited(true);
              resetProfileFeedback();
            }}
          />
        </div>

        <div className="field">
          <label htmlFor="profile-level">{t("profile.level")}</label>
          <select
            id="profile-level"
            name="level"
            value={level}
            disabled={savingProfile}
            onChange={(event) => {
              setLevel(event.target.value as Level);
              resetProfileFeedback();
            }}
          >
            {LEVELS.map((value) => (
              <option key={value} value={value}>
                {t(`level.${value}`)}
              </option>
            ))}
          </select>
        </div>

        {profileError ? (
          <div className="banner banner--error" role="alert">
            {profileError}
          </div>
        ) : null}

        {profileSaved ? (
          <p
            className="banner banner--success"
            role="status"
            data-testid="profile-saved"
          >
            {t("profile.saved")}
          </p>
        ) : null}

        <div className="actions">
          <button
            type="submit"
            className="button button--primary"
            disabled={savingProfile}
            aria-busy={savingProfile}
          >
            {savingProfile ? t("common.loading") : t("profile.save")}
          </button>
        </div>
      </form>

      <section className="panel" aria-labelledby="billing-title">
        <div className="panel__header">
          <h2 id="billing-title">{t("billing.subscription.title")}</h2>
        </div>

        <p data-testid="subscription-tier">
          {t("billing.subscription.current", {
            tier: t(`usage.tier.${user.tier}`),
          })}
        </p>
        <p className="muted">
          {user.tier === "pro"
            ? t("billing.subscription.proHint")
            : t("billing.subscription.freeHint")}
        </p>

        {billingError ? (
          <p className="banner banner--error" role="alert">
            {billingError}
          </p>
        ) : null}

        <div className="actions">
          {user.tier === "free" ? (
            <button
              type="button"
              className="button button--primary"
              data-testid="profile-upgrade"
              disabled={billingLoading}
              aria-busy={billingLoading}
              onClick={() => {
                void openBilling("checkout");
              }}
            >
              {billingLoading ? t("billing.upgrading") : t("billing.upgrade")}
            </button>
          ) : (
            <button
              type="button"
              className="button button--primary"
              data-testid="manage-subscription"
              disabled={billingLoading}
              aria-busy={billingLoading}
              onClick={() => {
                void openBilling("portal");
              }}
            >
              {billingLoading ? t("billing.openingPortal") : t("billing.manage")}
            </button>
          )}
        </div>
      </section>

      <form className="panel" onSubmit={handlePasswordSubmit} noValidate>
        <div className="panel__header">
          <h2>{t("profile.password.title")}</h2>
        </div>

        <div className="field">
          <label htmlFor="password-current">{t("profile.password.current")}</label>
          <input
            id="password-current"
            className="auth-input"
            type="password"
            name="current-password"
            autoComplete="current-password"
            value={currentPassword}
            disabled={changingPassword}
            onChange={(event) => {
              setCurrentPassword(event.target.value);
              resetPasswordFeedback();
            }}
          />
        </div>

        <div className="field">
          <label htmlFor="password-next">{t("profile.password.next")}</label>
          <input
            id="password-next"
            className="auth-input"
            type="password"
            name="new-password"
            autoComplete="new-password"
            value={newPassword}
            disabled={changingPassword}
            onChange={(event) => {
              setNewPassword(event.target.value);
              resetPasswordFeedback();
            }}
          />
        </div>

        <div className="field">
          <label htmlFor="password-confirm">{t("profile.password.confirm")}</label>
          <input
            id="password-confirm"
            className="auth-input"
            type="password"
            name="confirm-password"
            autoComplete="new-password"
            value={confirmation}
            disabled={changingPassword}
            onChange={(event) => {
              setConfirmation(event.target.value);
              resetPasswordFeedback();
            }}
          />
        </div>

        {passwordError ? (
          <p
            className="banner banner--error"
            role="alert"
            data-testid="password-error"
          >
            {passwordError}
          </p>
        ) : null}

        {passwordChanged ? (
          <p
            className="banner banner--success"
            role="status"
            data-testid="password-changed"
          >
            {t("profile.password.changed")}
          </p>
        ) : null}

        <div className="actions">
          <button
            type="submit"
            className="button button--primary"
            disabled={changingPassword}
            aria-busy={changingPassword}
          >
            {changingPassword
              ? t("common.loading")
              : t("profile.password.submit")}
          </button>
        </div>
      </form>
    </main>
  );
}
