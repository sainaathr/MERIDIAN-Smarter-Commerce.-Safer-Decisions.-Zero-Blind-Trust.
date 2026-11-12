import React from 'react';
import { NavLink } from 'react-router-dom';
import { MeridianLogo } from '../common/MeridianLogo';
import {
  LayoutDashboard,
  ShoppingBag,
  TrendingUp,
  Users,
  HeartHandshake,
  ShieldCheck,
  Receipt,
  ScrollText,
  Settings as SettingsIcon,
  ChevronRight,
} from 'lucide-react';

interface SidebarProps {
  isOpen: boolean;
  onClose?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ isOpen, onClose }) => {
  const navItems = [
    {
      to: '/',
      label: 'Overview',
      icon: LayoutDashboard,
      badge: null,
    },
    {
      to: '/personal',
      label: 'Personal / PACT',
      icon: ShoppingBag,
      badge: '1 Approval',
      badgeColor: 'bg-[#FFD91A] text-black font-black',
    },
    {
      to: '/business',
      label: 'Business / FLOW',
      icon: TrendingUp,
      badge: null,
    },
    {
      to: '/collective',
      label: 'Collective / COMMON',
      icon: Users,
      badge: 'Active Split',
      badgeColor: 'bg-[#D0F030] text-black font-black',
    },
    {
      to: '/care',
      label: 'Care / Assisted',
      icon: HeartHandshake,
      badge: 'Mode B',
      badgeColor: 'bg-[#FFD91A] text-black font-black',
    },
    {
      to: '/trust',
      label: 'Trust Centre',
      icon: ShieldCheck,
      badge: 'Kernel v1',
      badgeColor: 'bg-[#F04400] text-black font-black',
    },
    {
      to: '/transactions',
      label: 'Transactions',
      icon: Receipt,
      badge: null,
    },
    {
      to: '/audit',
      label: 'Audit Log',
      icon: ScrollText,
      badge: 'Immutable',
      badgeColor: 'bg-[#242424] text-[#D0F030] border border-[#333333] font-bold',
    },
    {
      to: '/settings',
      label: 'Settings',
      icon: SettingsIcon,
      badge: null,
    },
  ];

  return (
    <>
      {/* Mobile Backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/80 backdrop-blur-sm lg:hidden"
          onClick={onClose}
        />
      )}

      <aside
        className={`fixed top-0 bottom-0 left-0 z-40 w-64 bg-[#000000] border-r border-[#242424] flex flex-col transition-transform duration-200 ease-in-out lg:translate-x-0 ${
          isOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {/* Brand Header */}
        <div className="h-16 flex items-center px-4 sm:px-5 border-b border-[#242424] bg-[#000000]">
          <MeridianLogo variant="wordmark" size="sm" showTagline={true} linkTo="/" glow={true} />
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 py-5 space-y-1.5 overflow-y-auto">
          <div className="px-3 pb-2 text-[10px] font-mono uppercase tracking-widest text-[#FFD91A] font-black">
            Commerce Modules
          </div>

          {navItems.slice(0, 5).map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                onClick={onClose}
                className={({ isActive }) =>
                  `flex items-center justify-between px-3.5 py-2.5 rounded-xl text-xs font-mono uppercase tracking-wider transition group ${
                    isActive
                      ? 'bg-[#F04400] text-black font-black shadow-lg shadow-[#F04400]/20'
                      : 'text-[#D4D4D4] hover:text-white hover:bg-[#171717] font-semibold'
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    <div className="flex items-center gap-3">
                      <Icon
                        className={`w-4 h-4 transition ${
                          isActive ? 'text-black stroke-[2.5]' : 'text-[#A3A3A3] group-hover:text-white'
                        }`}
                      />
                      <span>{item.label}</span>
                    </div>
                    {item.badge && (
                      <span
                        className={`px-2 py-0.5 text-[9px] font-mono rounded ${
                          isActive ? 'bg-black text-[#FFD91A] font-black' : item.badgeColor
                        }`}
                      >
                        {item.badge}
                      </span>
                    )}
                  </>
                )}
              </NavLink>
            );
          })}

          <div className="pt-5 px-3 pb-2 text-[10px] font-mono uppercase tracking-widest text-[#FFD91A] font-black">
            Trust &amp; Security
          </div>

          {navItems.slice(5).map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                onClick={onClose}
                className={({ isActive }) =>
                  `flex items-center justify-between px-3.5 py-2.5 rounded-xl text-xs font-mono uppercase tracking-wider transition group ${
                    isActive
                      ? 'bg-[#F04400] text-black font-black shadow-lg shadow-[#F04400]/20'
                      : 'text-[#D4D4D4] hover:text-white hover:bg-[#171717] font-semibold'
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    <div className="flex items-center gap-3">
                      <Icon
                        className={`w-4 h-4 transition ${
                          isActive ? 'text-black stroke-[2.5]' : 'text-[#A3A3A3] group-hover:text-white'
                        }`}
                      />
                      <span>{item.label}</span>
                    </div>
                    {item.badge && (
                      <span
                        className={`px-2 py-0.5 text-[9px] font-mono rounded ${
                          isActive ? 'bg-black text-[#FFD91A] font-black' : item.badgeColor
                        }`}
                      >
                        {item.badge}
                      </span>
                    )}
                  </>
                )}
              </NavLink>
            );
          })}
        </nav>

        {/* Footer State */}
        <div className="p-3 border-t border-[#242424] bg-[#0E0E0E] text-xs">
          <div className="p-3 rounded-xl bg-[#171717] border border-[#242424] flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="w-2.5 h-2.5 rounded-full bg-[#D0F030] animate-pulse" />
              <div>
                <p className="text-[11px] font-mono text-white font-black uppercase">Policy Kernel</p>
                <p className="text-[10px] text-[#D0F030] font-mono font-bold">10/10 Invariants Active</p>
              </div>
            </div>
            <ChevronRight className="w-4 h-4 text-[#A3A3A3]" />
          </div>
        </div>
      </aside>
    </>
  );
};
