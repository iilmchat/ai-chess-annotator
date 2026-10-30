import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AuthGate } from "./components/AuthGate";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Layout } from "./components/Layout";
import AnalysisPage from "./pages/AnalysisPage";
import ForgotPasswordPage from "./pages/ForgotPasswordPage";
import HistoryPage from "./pages/HistoryPage";
import HomePage from "./pages/HomePage";
import LoginPage from "./pages/LoginPage";
import ResetPasswordPage from "./pages/ResetPasswordPage";
import VerifyEmailPage from "./pages/VerifyEmailPage";
import { bootstrapAuth } from "./stores/authStore";

// Сессия восстанавливается по HttpOnly refresh-cookie (один POST /auth/refresh)
// до первого рендера; access-токен в localStorage не хранится (KI-037).
void bootstrapAuth();

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Layout>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            {/* KI-055: публичные auth-страницы. Ссылки из писем ведут сюда,
                а не на JSON-эндпоинты API. */}
            <Route path="/verify-email" element={<VerifyEmailPage />} />
            <Route path="/forgot-password" element={<ForgotPasswordPage />} />
            <Route path="/reset-password" element={<ResetPasswordPage />} />
            <Route
              path="/"
              element={
                <AuthGate>
                  <HomePage />
                </AuthGate>
              }
            />
            <Route
              path="/history"
              element={
                // Итерация 35: страница сама показывает placeholder для
                // неподтверждённого пользователя (без запроса партий).
                <AuthGate allowUnverified>
                  <HistoryPage />
                </AuthGate>
              }
            />
            <Route
              path="/analysis/:id"
              element={
                <AuthGate>
                  <AnalysisPage />
                </AuthGate>
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
