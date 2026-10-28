import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { getUsage } from "../api";
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

export function UsageBadge() {
  const { t } = useTranslation();
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [open, setOpen] = useState(false);
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

  if (!usage?.enabled) {
    return null;
  }

  const over = usage.used_tokens >= usage.limit_tokens;
  const label = t("usage.label", {
    tier: t(`usage.tier.${usage.tier}`),
    used: compact(usage.used_tokens),
    limit: compact(usage.limit_tokens),
  });

  return (
    <>
      <button
        type="button"
        className={`usage-badge ${over ? "usage-badge--over" : ""}`}
        onClick={() => setOpen(true)}
        title={t("usage.details", {
          remaining: compact(usage.remaining_tokens),
        })}
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
            {over ? <p className="muted">{t("usage.modal.proHint")}</p> : null}
            <div className="modal__actions">
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
