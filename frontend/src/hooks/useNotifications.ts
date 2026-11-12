// MERIDIAN — Notification Centre Hook
import { useState, useCallback } from 'react';
import { fetchProposals, fetchAuditEvents } from '../services/api';

export interface Notification {
  id: string;
  title: string;
  message: string;
  category: 'approval' | 'payment' | 'policy' | 'group';
  read: boolean;
  timestamp: string;
  href: string;
}

export interface UseNotificationsResult {
  notifications: Notification[];
  unreadCount: number;
  loading: boolean;
  markRead: (id: string) => void;
  markAllRead: () => void;
  refresh: () => Promise<void>;
}

export function useNotifications(): UseNotificationsResult {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchNotifications = useCallback(async (): Promise<Notification[]> => {
    const results: Notification[] = [];

    // --- Approval notifications from pending proposals ---
    try {
      const proposals = await fetchProposals(undefined, 50);
      const pending = proposals.filter((p) => p.state === 'AWAITING_APPROVAL');
      for (const p of pending) {
        const isGuardian = p.module === 'CARE' || (p as any).requires_guardian_oversight || (p as any).approval_status === 'AWAITING_GUARDIAN_CONSENT';
        const targetModule = p.module === 'BUSINESS' ? 'business' : p.module === 'CARE' ? 'care' : 'personal';
        results.push({
          id: `proposal-${p.id}`,
          title: isGuardian ? 'Guardian Consent Required' : 'Approval Required',
          message: `${p.title} from ${p.merchant_name} — $${(p.total).toFixed(2)}`,
          category: 'approval',
          read: false,
          timestamp: p.created_at,
          href: `/${targetModule}?review=${p.id}`,
        });
      }
    } catch {
      // backend may not be running in demo mode
    }

    // --- Policy / payment notifications from audit events ---
    try {
      const events = await fetchAuditEvents(undefined, undefined, 20);
      for (const ev of events) {
        const isPayment =
          ev.event_type?.toLowerCase().includes('payment') ||
          ev.event_type?.toLowerCase().includes('capture') ||
          ev.event_type?.toLowerCase().includes('order');
        const category: 'payment' | 'policy' = isPayment ? 'payment' : 'policy';
        results.push({
          id: `audit-${ev.id}`,
          title: isPayment ? 'Payment Activity' : 'Policy Event',
          message: ev.summary || ev.event_type || 'System event recorded',
          category,
          read: false,
          timestamp: ev.timestamp,
          href: isPayment ? '/transactions' : '/audit',
        });
      }
    } catch {
      // backend may not be running in demo mode
    }

    // Sort descending by timestamp, cap at 20
    results.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
    return results.slice(0, 20);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const fresh = await fetchNotifications();
      // Preserve read state for items already seen
      setNotifications((prev) => {
        const readSet = new Set(prev.filter((n) => n.read).map((n) => n.id));
        return fresh.map((n) => ({ ...n, read: readSet.has(n.id) }));
      });
    } finally {
      setLoading(false);
    }
  }, [fetchNotifications]);

  const markRead = useCallback((id: string) => {
    setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)));
  }, []);

  const markAllRead = useCallback(() => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  }, []);

  const unreadCount = notifications.filter((n) => !n.read).length;

  return { notifications, unreadCount, loading, markRead, markAllRead, refresh };
}
