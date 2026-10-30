import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { createCheckoutSession, getUsage } from "../api";
import type { UsageResponse } from "../types";

function compact(value: number): string {
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1).replace(/\.0$/, "")}k`;
  }
  return `${value}`;
}

/**
 * Полные числа для tooltip/модалки: разделитель тысяч берётся из языка
 * интерфейса (ru → «9 300», en → «9,300»), а не из локали системы.
 */
function formatAmount(value: number, language: string): string {
  const locale = language.toLowerCase().startsWith("en") ? "en-US" : "ru-RU";
  return value.toLocaleString(locale);
}

export function UsageBadge() {
  const { t, i18n } = useTranslation();
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [open, setOpen] = useState(false);
  const [checkoutLoading, setCheckoutLoading] = useState(false);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);
  const warned = useRef(false);

  const load = useCallback(() => {
    getUsage()
      .then((next) => setUsage(next))
      .catch(() => setUsage(null));
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 30_000);
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [load]);

  useEffect(() => {
    if (!usage?.enabled || warned.current) {
      return;
    }
    if (usage.used_tokens >= usage.limit_tokens) {
      warned.current = true;
      setOpen(true);
    }
  }, [usage]);

  async function startCheckout() {
    if (checkoutLoading) {
      return;
    }
    setCheckoutLoading(true);
    setCheckoutError(null);
    try {
      const { url } = await createCheckoutSession();
      // Полный переход на Stripe: после оплаты Stripe вернёт на
      // `/billing/success`, и сессия восстановится по refresh-cookie.
      window.location.href = url;
    } catch (cause) {
      setCheckoutError(
        cause instanceof Error && cause.message
          ? cause.message
          : t("billing.error"),
      );
      setCheckoutLoading(false);
    }
  }

  if (!usage?.enabled) {
    return null;
  }

  const over = usage.used_tokens >= usage.limit_tokens;
  const isFree = usage.tier === "free";
  const label = t("usage.label", {
    tier: t(`usage.tier.${usage.tier}`),
    used: compact(usage.used_tokens),
    limit: compact(usage.limit_tokens),
  });
  const usedTokens = formatAmount(usage.used_tokens, i18n.language);
  const limitTokens = formatAmount(usage.limit_tokens, i18n.language);
  const remainingTokens = formatAmount(usage.remaining_tokens, i18n.language);

  return (
    <>
      <button
        type="button"
        className={`usage-badge ${over ? "usage-badge--over" : ""}`}
        onClick={() => setOpen(true)}
        title={t("usage.tooltip", {
          used: usedTokens,
          limit: limitTokens,
        })}
        data-testid="usage-badge"
      >
        <span className="usage-badge__dot" />
        {label}
      </button>
      {open ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => setOpen(false)}
        >
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="usage-modal-title"
            onClick={(event) => event.stopPropagation()}
          >
            <h2 id="usage-modal-title">
              {over ? t("usage.modal.titleOver") : t("usage.modal.title")}
            </h2>
            <p>
              {t("usage.modal.body", {
                used: compact(usage.used_tokens),
                limit: compact(usage.limit_tokens),
              })}
            </p>
            <p className="muted">
              {t("usage.details", { remaining: remainingTokens })}
            </p>
            {isFree ? (
              <p className="muted">{t("usage.modal.proHint")}</p>
            ) : null}
            {checkoutError ? (
              <p className="banner banner--error" role="alert">
                {checkoutError}
              </p>
            ) : null}
            <div className="modal__actions">
              {isFree ? (
                <button
                  type="button"
                  className="button button--primary"
                  data-testid="upgrade-button"
                  disabled={checkoutLoading}
                  aria-busy={checkoutLoading}
                  onClick={() => {
                    void startCheckout();
                  }}
                >
                  {checkoutLoading
                    ? t("billing.upgrading")
                    : t("billing.upgrade")}
                </button>
              ) : null}
              <button
                type="button"
                className="button button--primary"
                onClick={() => setOpen(false)}
              >
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
