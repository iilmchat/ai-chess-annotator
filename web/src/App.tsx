import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AuthGate } from "./components/AuthGate";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Layout } from "./components/Layout";
import AnalysisPage from "./pages/AnalysisPage";
import HistoryPage from "./pages/HistoryPage";
import HomePage from "./pages/HomePage";
import LoginPage from "./pages/LoginPage";
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
                <AuthGate>
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
