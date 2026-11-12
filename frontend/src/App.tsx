import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { ProtectedRoute } from './components/auth/ProtectedRoute';
import { AppLayout } from './components/layout/AppLayout';
import { LoginPage } from './pages/LoginPage';
import { OverviewPage } from './pages/OverviewPage';
import { PersonalPage } from './pages/PersonalPage';
import { BusinessPage } from './pages/BusinessPage';
import { CollectivePage } from './pages/CollectivePage';
import { CarePage } from './pages/CarePage';
import { TrustCentrePage } from './pages/TrustCentrePage';
import { TransactionsPage } from './pages/TransactionsPage';
import { AuditLogPage } from './pages/AuditLogPage';
import { SettingsPage } from './pages/SettingsPage';

export const App: React.FC = () => {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          {/* Public Authentication Route */}
          <Route path="/login" element={<LoginPage />} />

          {/* Protected Application Workspace */}
          <Route
            path="/*"
            element={
              <ProtectedRoute>
                <AppLayout>
                  <Routes>
                    <Route path="/" element={<OverviewPage />} />
                    <Route path="/personal" element={<PersonalPage />} />
                    <Route path="/business" element={<BusinessPage />} />
                    <Route path="/collective" element={<CollectivePage />} />
                    <Route path="/care" element={<CarePage />} />
                    <Route path="/trust" element={<TrustCentrePage />} />
                    <Route path="/transactions" element={<TransactionsPage />} />
                    <Route path="/audit" element={<AuditLogPage />} />
                    <Route path="/settings" element={<SettingsPage />} />
                    <Route path="*" element={<Navigate to="/" replace />} />
                  </Routes>
                </AppLayout>
              </ProtectedRoute>
            }
          />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
};

export default App;
