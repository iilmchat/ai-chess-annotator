import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

/**
 * `/billing/cancel` — пользователь вернулся из Stripe Checkout без оплаты
 * (T-083). Ничего не меняем: тариф остаётся прежним, оплату можно повторить
 * из модалки лимита или профиля.
 */
export default function BillingCancelPage() {
  const { t } = useTranslation();

  return (
    <main className="page">
      <div className="page__head">
        <div>
          <h1>{t("billing.cancel.title")}</h1>
          <p className="muted">{t("billing.cancel.subtitle")}</p>
        </div>
      </div>

      <section className="panel">
        <p>{t("billing.cancel.body")}</p>
        <div className="actions">
          <Link className="button button--primary" to="/profile">
            {t("billing.cancel.toProfile")}
          </Link>
          <Link className="button button--ghost" to="/">
            {t("billing.cancel.toHome")}
          </Link>
        </div>
      </section>
    </main>
  );
}
