import React, { useState } from 'react';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';
import { DemoNotice } from '../common/DemoNotice';
import { ErrorBoundary } from '../common/ErrorBoundary';

interface AppLayoutProps {
  children: React.ReactNode;
}

export const AppLayout: React.FC<AppLayoutProps> = ({ children }) => {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="min-h-screen bg-[#000000] text-white flex selection:bg-[#F04400] selection:text-black">
      {/* Sidebar Navigation */}
      <Sidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0 lg:pl-64">
        <Topbar onToggleSidebar={() => setSidebarOpen((prev) => !prev)} />

        <main className="flex-1 p-4 sm:p-6 lg:p-8 space-y-6 max-w-7xl w-full mx-auto">
          {/* Top Demo Banner across all screens */}
          <DemoNotice />

          {/* Routed Page Content wrapped in resilient ErrorBoundary */}
          <ErrorBoundary fallbackTitle="View Error">
            <div className="space-y-6">{children}</div>
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
};
