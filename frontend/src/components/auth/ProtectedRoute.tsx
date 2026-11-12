import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { Loader2 } from 'lucide-react';
import { MeridianLogo } from '../common/MeridianLogo';

interface ProtectedRouteProps {
  children: React.ReactNode;
}

export const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children }) => {
  const { isAuthenticated, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="min-h-screen bg-[#000000] flex items-center justify-center p-4">
        <div className="flex flex-col items-center gap-4 text-center max-w-sm">
          <MeridianLogo variant="icon" size="xl" glow={true} />
          <div className="space-y-1">
            <h2 className="text-base font-black tracking-widest text-white font-mono uppercase">
              M<span className="text-[#F04400]">E</span>RIDIAN
            </h2>
            <p className="text-[10px] text-[#FFD91A] font-mono tracking-widest font-black uppercase">
              COMMERCE WITHOUT BLIND TRUST
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs font-mono text-[#A3A3A3] pt-2">
            <Loader2 className="w-4 h-4 animate-spin text-[#F04400]" />
            <span>Verifying session with Universal Trust Engine...</span>
          </div>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
};
