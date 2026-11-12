import React, { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Bell,
  Shield,
  CreditCard,
  Users,
  Check,
  ExternalLink,
  Clock,
  Inbox,
} from 'lucide-react';
import type { Notification } from '../../hooks/useNotifications';

interface NotificationPanelProps {
  isOpen: boolean;
  onClose: () => void;
  notifications: Notification[];
  unreadCount: number;
  loading: boolean;
  onMarkRead: (id: string) => void;
  onMarkAllRead: () => void;
}

export const NotificationPanel: React.FC<NotificationPanelProps> = ({
  isOpen,
  onClose,
  notifications,
  unreadCount,
  loading,
  onMarkRead,
  onMarkAllRead,
}) => {
  const navigate = useNavigate();
  const panelRef = useRef<HTMLDivElement>(null);

  // Close when clicking outside
  useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(event.target as Node)) {
        onClose();
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const getCategoryIcon = (category: Notification['category']) => {
    switch (category) {
      case 'approval':
        return <Bell className="w-4 h-4 text-[#FFD91A]" />;
      case 'payment':
        return <CreditCard className="w-4 h-4 text-[#D0F030]" />;
      case 'policy':
        return <Shield className="w-4 h-4 text-[#F04400]" />;
      case 'group':
        return <Users className="w-4 h-4 text-[#FFD91A]" />;
      default:
        return <Bell className="w-4 h-4 text-[#A3A3A3]" />;
    }
  };

  const formatTimestamp = (ts: string) => {
    try {
      const date = new Date(ts);
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return 'Recent';
    }
  };

  return (
    <div
      ref={panelRef}
      className="absolute right-0 top-14 w-80 sm:w-96 bg-[#171717] rounded-2xl shadow-2xl border border-[#242424] z-50 overflow-hidden flex flex-col max-h-[520px] animate-in fade-in slide-in-from-top-2 duration-150"
    >
      {/* Header */}
      <div className="px-4 py-3 border-b border-[#242424] flex items-center justify-between bg-[#0E0E0E]">
        <div className="flex items-center gap-2">
          <span className="text-sm font-black text-white font-mono uppercase tracking-wider">
            Notifications
          </span>
          {unreadCount > 0 && (
            <span className="px-2 py-0.5 rounded text-[10px] font-mono font-black bg-[#FFD91A] text-black">
              {unreadCount} NEW
            </span>
          )}
        </div>
        {unreadCount > 0 && (
          <button
            onClick={onMarkAllRead}
            className="text-xs font-bold font-mono text-[#FFD91A] hover:text-[#D0F030] flex items-center gap-1 transition"
          >
            <Check className="w-3.5 h-3.5" />
            <span>Mark all read</span>
          </button>
        )}
      </div>

      {/* Notifications List */}
      <div className="flex-1 overflow-y-auto divide-y divide-[#242424]">
        {loading ? (
          <div className="py-10 text-center text-xs text-[#A3A3A3] flex flex-col items-center gap-2 font-mono">
            <span className="w-4 h-4 border-2 border-[#F04400] border-t-transparent rounded-full animate-spin" />
            <span>Syncing Universal Trust Engine...</span>
          </div>
        ) : notifications.length === 0 ? (
          <div className="py-12 px-4 text-center flex flex-col items-center justify-center gap-2">
            <div className="w-10 h-10 rounded-xl bg-[#242424] flex items-center justify-center text-[#A3A3A3]">
              <Inbox className="w-5 h-5 text-[#D0F030]" />
            </div>
            <p className="text-sm font-bold text-white font-mono">ALL CLEAR</p>
            <p className="text-xs text-[#A3A3A3] max-w-xs font-mono">
              No pending consent requests or recent transaction alerts.
            </p>
          </div>
        ) : (
          notifications.map((item) => (
            <div
              key={item.id}
              onClick={() => {
                onMarkRead(item.id);
                if (item.href) {
                  onClose();
                  navigate(item.href);
                }
              }}
              className={`p-3.5 transition flex items-start gap-3 cursor-pointer hover:bg-[#242424] ${
                !item.read ? 'bg-[#F04400]/10 border-l-2 border-[#F04400]' : 'bg-[#171717]'
              }`}
            >
              <div className="mt-0.5 p-2 rounded-lg bg-[#242424] shrink-0 border border-[#333333]">
                {getCategoryIcon(item.category)}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-1">
                  <p className="text-xs font-bold text-white truncate font-mono">{item.title}</p>
                  <span className="text-[10px] text-[#A3A3A3] font-mono shrink-0 flex items-center gap-1">
                    <Clock className="w-2.5 h-2.5" />
                    {formatTimestamp(item.timestamp)}
                  </span>
                </div>
                <p className="text-xs text-[#D4D4D4] line-clamp-2 mt-0.5 font-sans">{item.message}</p>
              </div>
              {!item.read && (
                <span className="w-2 h-2 rounded-full bg-[#F04400] shrink-0 mt-1.5" />
              )}
            </div>
          ))
        )}
      </div>

      {/* Footer */}
      <div className="px-4 py-2.5 border-t border-[#242424] bg-[#0E0E0E] text-[11px] text-[#A3A3A3] font-mono flex items-center justify-between">
        <span className="text-[#D0F030] font-bold">10/10 Invariants Active</span>
        <button
          onClick={() => {
            onClose();
            navigate('/audit');
          }}
          className="text-white hover:text-[#FFD91A] flex items-center gap-1 font-bold transition"
        >
          <span>Audit Log</span>
          <ExternalLink className="w-3 h-3" />
        </button>
      </div>
    </div>
  );
};
