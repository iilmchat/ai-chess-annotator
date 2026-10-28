import { Component, type ErrorInfo, type ReactNode } from "react";

import i18n from "../i18n";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("UI render failed", error, info.componentStack);
  }

  render() {
    if (!this.state.error) {
      return this.props.children;
    }
    return (
      <div className="error-boundary">
        <div className="error-boundary__card">
          <h1>{i18n.t("errorBoundary.title")}</h1>
          <p>{i18n.t("errorBoundary.body")}</p>
          <pre>{this.state.error.message}</pre>
          <button
            type="button"
            className="button button--primary"
            onClick={() => window.location.reload()}
          >
            {i18n.t("errorBoundary.reload")}
          </button>
        </div>
      </div>
    );
  }
}
