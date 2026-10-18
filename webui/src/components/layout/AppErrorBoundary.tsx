import { Component, type ErrorInfo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

function AppCrashFallback() {
  const { t } = useTranslation();
  return (
    <div className="app-shell min-h-screen grid place-items-center p-6">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-bold text-cyber-text-primary">{t("appError.title")}</h1>
        <p className="mt-3 text-sm text-cyber-text-muted">{t("appError.description")}</p>
        <button type="button" className="btn primary mt-6" onClick={() => window.location.reload()}>
          {t("appError.reload")}
        </button>
      </div>
    </div>
  );
}

interface AppErrorBoundaryState {
  failed: boolean;
}

/** 渲染异常的最后一道兜底：避免单个卡片出错时整页变成空白。 */
export class AppErrorBoundary extends Component<{ children: ReactNode }, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("SiYe UI render failed", error, info.componentStack);
  }

  render() {
    return this.state.failed ? <AppCrashFallback /> : this.props.children;
  }
}
