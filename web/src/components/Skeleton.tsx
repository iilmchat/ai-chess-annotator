import i18n from "../i18n";

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div
      className="skeleton"
      role="status"
      aria-live="polite"
      aria-label={i18n.t("common.loading")}
    >
      {Array.from({ length: lines }, (_, index) => (
        <span
          key={index}
          className="skeleton__line"
          style={{ width: `${70 + ((index * 13) % 30)}%` }}
        />
      ))}
    </div>
  );
}

export function AnalysisSkeleton() {
  return (
    <main className="layout" aria-busy="true" aria-label="Loading analysis">
      <aside className="column column--left">
        <section className="panel">
          <Skeleton lines={4} />
        </section>
        <section className="panel">
          <Skeleton lines={5} />
        </section>
      </aside>
      <section className="column column--center">
        <div className="skeleton skeleton--board" />
        <section className="panel">
          <Skeleton lines={3} />
        </section>
      </section>
      <aside className="column column--right">
        <section className="panel">
          <Skeleton lines={6} />
        </section>
        <section className="panel">
          <Skeleton lines={4} />
        </section>
      </aside>
    </main>
  );
}
