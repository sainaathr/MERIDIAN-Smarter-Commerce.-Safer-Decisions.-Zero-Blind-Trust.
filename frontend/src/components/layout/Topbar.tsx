import React, { useState, useEffect, useRef } from 'react';
import { Menu, Bell, Shield, Sparkles, LogOut, ChevronDown, User } from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';
import { fetchAIStatus, type AIHealthStatusResponse } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { NotificationPanel } from '../notifications/NotificationPanel';
import { useNotifications } from '../../hooks/useNotifications';
import { MeridianLogo } from '../common/MeridianLogo';

interface TopbarProps {
  onToggleSidebar: () => void;
  pendingApprovalsCount?: number;
  onOpenNotifications?: () => void;
}

export const Topbar: React.FC<TopbarProps> = ({ onToggleSidebar }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const [aiStatus, setAiStatus] = useState<AIHealthStatusResponse | null>(null);
  const [isNotifOpen, setIsNotifOpen] = useState(false);
  const [isUserMenuOpen, setIsUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);

  const {
    notifications,
    unreadCount,
    loading: notifLoading,
    markRead,
    markAllRead,
    refresh: refreshNotifs,
  } = useNotifications();

  useEffect(() => {
    let isMounted = true;
    fetchAIStatus().then((status) => {
      if (isMounted) setAiStatus(status);
    });
    const interval = setInterval(() => {
      fetchAIStatus().then((status) => {
        if (isMounted) setAiStatus(status);
      });
    }, 15000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  // Close user menu on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (userMenuRef.current && !userMenuRef.current.contains(event.target as Node)) {
        setIsUserMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const getPageTitle = (path: string) => {
    switch (path) {
      case '/':
        return 'Overview Dashboard';
      case '/personal':
        return 'Personal / PACT Commerce';
      case '/business':
        return 'Business / FLOW Intelligence';
      case '/collective':
        return 'Collective / COMMON Commerce';
      case '/care':
        return 'Care / Assisted Commerce';
      case '/trust':
        return 'Universal Trust Centre';
      case '/transactions':
        return 'Transaction History';
      case '/audit':
        return 'Immutable Audit Log';
      case '/settings':
        return 'System & Trust Settings';
      default:
        return 'MERIDIAN';
    }
  };

  const handleLogout = async () => {
    setIsUserMenuOpen(false);
    await logout();
    navigate('/login');
  };

  const initials = user?.full_name
    ? user.full_name
        .split(' ')
        .map((n) => n[0])
        .join('')
        .toUpperCase()
        .slice(0, 2)
    : 'AR';

  return (
    <header className="sticky top-0 z-30 h-16 bg-[#000000] border-b border-[#242424] px-4 sm:px-6 flex items-center justify-between shadow-md">
      {/* Left: Mobile Toggle & Page Title */}
      <div className="flex items-center gap-3">
        <button
          onClick={onToggleSidebar}
          className="p-2 rounded-xl text-[#A3A3A3] hover:text-white hover:bg-[#171717] lg:hidden transition"
          aria-label="Toggle navigation menu"
        >
          <Menu className="w-5 h-5" />
        </button>

        <div className="flex flex-col">
          <div className="flex items-center gap-2.5">
            <MeridianLogo variant="icon" size="xs" linkTo="/" glow={true} className="hidden sm:inline-block" />
            <span className="text-xs text-[#F04400] font-mono font-black hidden sm:inline">
              //
            </span>
            <h1 className="text-sm sm:text-base font-black text-white font-mono tracking-wider uppercase">
              {getPageTitle(location.pathname)}
            </h1>
          </div>
        </div>
      </div>

      {/* Right: Environment Pills & Controls */}
      <div className="flex items-center gap-2 sm:gap-3">
        {/* PayPal Sandbox Environment Indicator */}
        <div className="flex items-center gap-1.5 px-3 py-1 rounded-lg bg-[#FFD91A] text-black text-xs font-mono font-black shadow-xs">
          <span className="w-2 h-2 rounded-full bg-black animate-pulse shrink-0" />
          <span className="hidden sm:inline">PAYPAL SANDBOX</span>
          <span className="sm:hidden">SANDBOX</span>
        </div>

        {/* AI Orchestrator Status Indicator */}
        {aiStatus?.status === 'connected' ? (
          <div className="flex items-center gap-1.5 px-3 py-1 rounded-lg bg-[#171717] border border-[#D0F030] text-[#D0F030] text-xs font-mono font-bold">
            <Sparkles className="w-3.5 h-3.5 text-[#D0F030] shrink-0" />
            <span className="hidden md:inline">GEMINI 2.0 CONNECTED</span>
            <span className="md:hidden">GEMINI</span>
          </div>
        ) : (
          <div className="flex items-center gap-1.5 px-3 py-1 rounded-lg bg-[#171717] border border-[#FFD91A] text-[#FFD91A] text-xs font-mono font-bold">
            <Sparkles className="w-3.5 h-3.5 text-[#FFD91A] shrink-0" />
            <span className="hidden md:inline">AI: FALLBACK MODE</span>
            <span className="md:hidden">AI DEMO</span>
          </div>
        )}

        {/* Trust Engine Status */}
        <div className="hidden lg:flex items-center gap-1.5 px-3 py-1 rounded-lg bg-[#171717] border border-[#F04400] text-[#F04400] text-xs font-mono font-black">
          <Shield className="w-3.5 h-3.5 text-[#F04400] shrink-0" />
          <span>TRUST ENGINE: ENFORCING</span>
        </div>

        {/* Notification Bell with Panel */}
        <div className="relative">
          <button
            onClick={() => {
              setIsNotifOpen((prev) => !prev);
              refreshNotifs();
            }}
            className="relative p-2 rounded-xl text-[#A3A3A3] hover:text-white bg-[#171717] border border-[#242424] hover:border-[#F04400] transition"
            title={`${unreadCount} notifications`}
          >
            <Bell className="w-4 h-4" />
            {unreadCount > 0 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-[#F04400] text-black font-black text-[9px] flex items-center justify-center font-mono">
                {unreadCount}
              </span>
            )}
          </button>

          <NotificationPanel
            isOpen={isNotifOpen}
            onClose={() => setIsNotifOpen(false)}
            notifications={notifications}
            unreadCount={unreadCount}
            loading={notifLoading}
            onMarkRead={markRead}
            onMarkAllRead={markAllRead}
          />
        </div>

        {/* User Profile Dropdown Menu */}
        <div className="relative" ref={userMenuRef}>
          <button
            onClick={() => setIsUserMenuOpen((prev) => !prev)}
            className="flex items-center gap-2 pl-2 sm:pl-3 border-l border-[#242424] hover:opacity-90 transition group"
          >
            <div className="w-8 h-8 rounded-xl bg-[#F04400] border-2 border-black flex items-center justify-center text-xs font-mono font-black text-black shadow-md">
              {initials}
            </div>
            <div className="hidden xl:block text-left">
              <p className="text-xs font-black text-white leading-tight flex items-center gap-1 font-mono">
                <span>{user?.full_name || 'Alex Rivera'}</span>
                <ChevronDown className="w-3 h-3 text-[#A3A3A3] group-hover:text-white" />
              </p>
              <p className="text-[10px] text-[#FFD91A] font-mono font-bold">
                {user?.role === 'CONSUMER' ? 'Primary Guardian' : user?.role || 'Authenticated'}
              </p>
            </div>
          </button>

          {isUserMenuOpen && (
            <div className="absolute right-0 top-12 w-56 bg-[#171717] rounded-2xl shadow-2xl border border-[#242424] z-50 py-2 animate-in fade-in slide-in-from-top-1 duration-100">
              <div className="px-4 py-2.5 border-b border-[#242424]">
                <p className="text-xs font-black text-white font-mono">{user?.full_name}</p>
                <p className="text-[11px] text-[#A3A3A3] font-mono truncate">{user?.email}</p>
              </div>
              <button
                onClick={() => {
                  setIsUserMenuOpen(false);
                  navigate('/settings');
                }}
                className="w-full px-4 py-2.5 text-left text-xs text-[#D4D4D4] hover:text-white hover:bg-[#242424] flex items-center gap-2 transition font-mono"
              >
                <User className="w-3.5 h-3.5 text-[#F04400]" />
                <span>Account &amp; Policies</span>
              </button>
              <div className="border-t border-[#242424] my-1" />
              <button
                onClick={handleLogout}
                className="w-full px-4 py-2.5 text-left text-xs text-[#F04400] hover:bg-[#242424] flex items-center gap-2 transition font-bold font-mono"
              >
                <LogOut className="w-3.5 h-3.5 text-[#F04400]" />
                <span>Sign Out</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
};
