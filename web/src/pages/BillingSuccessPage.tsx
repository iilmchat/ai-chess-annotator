import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { getMe } from "../api";
import { useAuthStore } from "../stores/authStore";

/**
 * `/billing/success` — возврат из Stripe Checkout (T-083).
 *
 * Страница публичная: после полного редиректа на Stripe access-токен из
 * памяти теряется, но refresh-cookie остаётся, и `bootstrapAuth()` в
 * `App.tsx` восстанавливает сессию. `getMe()` дополнительно подтягивает
 * `tier`, который webhook мог обновить уже после возврата: пользователь
 * сразу видит «Pro», не перезагружая вкладку.
 */
export default function BillingSuccessPage() {
  const { t } = useTranslation();
  const user = useAuthStore((state) => state.user);
  const setUser = useAuthStore((state) => state.setUser);

  useEffect(() => {
    // Неавторизованный пользователь (например, открыл URL вручную) — не
    // ошибка: страница всё равно показывает подтверждение оплаты.
    getMe()
      .then((profile) => setUser(profile))
      .catch(() => undefined);
  }, [setUser]);

  return (
    <main className="page">
      <div className="page__head">
        <div>
          <h1>{t("billing.success.title")}</h1>
          <p className="muted">{t("billing.success.subtitle")}</p>
        </div>
      </div>

      <section className="panel">
        <p>{t("billing.success.body")}</p>
        {user ? (
          <p data-testid="billing-success-tier">
            {t("billing.subscription.current", {
              tier: t(`usage.tier.${user.tier}`),
            })}
          </p>
        ) : null}
        <p className="muted">{t("billing.success.webhookHint")}</p>
        <div className="actions">
          <Link className="button button--primary" to="/profile">
            {t("billing.success.toProfile")}
          </Link>
          <Link className="button button--ghost" to="/">
            {t("billing.success.toHome")}
          </Link>
        </div>
      </section>
    </main>
  );
}
