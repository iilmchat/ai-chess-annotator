import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AuthGate } from "./components/AuthGate";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Layout } from "./components/Layout";
import AnalysisPage from "./pages/AnalysisPage";
import HistoryPage from "./pages/HistoryPage";
import HomePage from "./pages/HomePage";
import LoginPage from "./pages/LoginPage";
import { hydrateAuth } from "./stores/authStore";

// Восстанавливаем сохранённую JWT-сессию до первого рендера.
hydrateAuth();

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
