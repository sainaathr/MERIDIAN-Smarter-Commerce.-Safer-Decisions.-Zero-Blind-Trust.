import React, { useState, useEffect, useCallback } from 'react';
import {
  Search,
  Filter,
  Eye,
  Lock,
  RefreshCw,
  Loader2,
  AlertTriangle,
} from 'lucide-react';
import { fetchAuditEvents } from '../services/api';
import { StatusBadge } from '../components/common/StatusBadge';
import { FingerprintPill } from '../components/trust/FingerprintPill';
import { Modal } from '../components/common/Modal';
import { MeridianLogo } from '../components/common/MeridianLogo';
import type { AuditEvent } from '../types';

export const AuditLogPage: React.FC = () => {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [selectedEvent, setSelectedEvent] = useState<AuditEvent | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [severityFilter, setSeverityFilter] = useState('ALL');
  const [moduleFilter, setModuleFilter] = useState('ALL');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadAuditEvents = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const liveEvents = await fetchAuditEvents(
        severityFilter !== 'ALL' ? severityFilter : undefined,
        moduleFilter !== 'ALL' ? moduleFilter : undefined,
        100
      );
      setEvents(liveEvents);
    } catch (err: any) {
      console.error('Failed to load authoritative audit trail:', err);
      setError(err.message || 'Failed to fetch live audit trail.');
      setEvents([]);
    } finally {
      setIsLoading(false);
    }
  }, [severityFilter, moduleFilter]);

  useEffect(() => {
    loadAuditEvents();
  }, [loadAuditEvents]);

  const filteredEvents = events.filter((evt) => {
    const matchSearch =
      evt.summary.toLowerCase().includes(searchTerm.toLowerCase()) ||
      evt.actor.toLowerCase().includes(searchTerm.toLowerCase()) ||
      evt.id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      evt.event_type.toLowerCase().includes(searchTerm.toLowerCase());
    const matchSeverity = severityFilter === 'ALL' || evt.severity === severityFilter;
    const matchModule = moduleFilter === 'ALL' || evt.module === moduleFilter;
    return matchSearch && matchSeverity && matchModule;
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 p-6 sm:p-7 rounded-3xl bg-[#171717] border border-[#242424] shadow-xl">
        <div className="space-y-1">
          <div className="flex items-center gap-2.5">
            <MeridianLogo variant="icon" size="xs" />
            <span className="px-2.5 py-0.5 rounded text-[10px] font-mono font-black bg-[#D0F030] text-black uppercase tracking-widest">
              AUDIT TRAIL
            </span>
            <span className="text-xs font-mono text-[#FFD91A] font-bold">APPEND-ONLY</span>
          </div>
          <h2 className="text-xl sm:text-2xl font-black text-white font-mono uppercase tracking-tight">
            Immutable Trust Audit Trail
          </h2>
          <p className="text-xs text-[#A3A3A3] max-w-2xl font-mono leading-relaxed">
            Append-only security and policy event stream. Logs every proposal generation, policy evaluation, fingerprint binding, human approval, and PayPal capture.
          </p>
        </div>
        <div className="flex items-center gap-2.5 shrink-0">
          <button
            onClick={() => loadAuditEvents()}
            disabled={isLoading}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-mono bg-[#0E0E0E] hover:bg-[#242424] disabled:opacity-50 text-white border border-[#242424] transition font-bold uppercase"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin text-[#F04400]' : 'text-[#FFD91A]'}`} />
            <span>Refresh</span>
          </button>
          <span className="flex items-center gap-2 px-3.5 py-2 rounded-xl text-xs font-mono font-black bg-[#D0F030] text-black uppercase tracking-wider">
            <Lock className="w-3.5 h-3.5 stroke-[3]" />
            TAMPER-EVIDENT
          </span>
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="p-4 rounded-2xl bg-[#2A0E08] border border-[#F04400] text-[#F04400] text-xs font-mono flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-[#F04400] shrink-0" />
            <span>Failed to connect to Universal Trust Engine audit stream: {error}</span>
          </div>
          <button
            onClick={() => loadAuditEvents()}
            className="px-3 py-1.5 rounded-lg bg-[#F04400] text-black font-black uppercase transition"
          >
            Retry
          </button>
        </div>
      )}

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 text-xs font-mono">
        <div className="relative w-full sm:w-80">
          <Search className="w-4 h-4 absolute left-3.5 top-3 text-[#A3A3A3]" />
          <input
            type="text"
            placeholder="Search audit records..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full bg-[#171717] border border-[#242424] rounded-2xl pl-10 pr-4 py-2.5 text-white placeholder-[#737373] focus:outline-none focus:border-[#F04400] shadow-md transition"
          />
        </div>

        <div className="flex flex-wrap items-center gap-3 w-full sm:w-auto">
          <div className="flex items-center gap-2">
            <Filter className="w-4 h-4 text-[#FFD91A]" />
            <span className="text-[#A3A3A3] uppercase font-bold text-[11px]">Severity:</span>
            <select
              value={severityFilter}
              onChange={(e) => setSeverityFilter(e.target.value)}
              className="bg-[#171717] border border-[#242424] rounded-xl px-3 py-2 text-white focus:outline-none focus:border-[#F04400] shadow-md font-bold uppercase text-xs"
            >
              <option value="ALL">All Severities</option>
              <option value="INFO">INFO</option>
              <option value="SUCCESS">SUCCESS</option>
              <option value="WARNING">WARNING</option>
              <option value="ALERT">ALERT</option>
            </select>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[#A3A3A3] uppercase font-bold text-[11px]">Module:</span>
            <select
              value={moduleFilter}
              onChange={(e) => setModuleFilter(e.target.value)}
              className="bg-[#171717] border border-[#242424] rounded-xl px-3 py-2 text-white focus:outline-none focus:border-[#F04400] shadow-md font-bold uppercase text-xs"
            >
              <option value="ALL">All Scopes</option>
              <option value="PERSONAL">PERSONAL</option>
              <option value="BUSINESS">BUSINESS</option>
              <option value="COLLECTIVE">COLLECTIVE</option>
              <option value="CARE">CARE</option>
              <option value="TRUST_ENGINE">TRUST_ENGINE</option>
            </select>
          </div>
        </div>
      </div>

      {/* Audit Events List */}
      <div className="rounded-3xl bg-[#171717] border border-[#242424] shadow-xl overflow-hidden">
        {isLoading ? (
          <div className="p-12 text-center text-[#A3A3A3] text-xs font-mono flex flex-col items-center justify-center gap-3">
            <Loader2 className="w-6 h-6 animate-spin text-[#F04400]" />
            <span>Fetching append-only audit trail from Universal Trust Engine...</span>
          </div>
        ) : error ? (
          <div className="p-12 text-center text-[#A3A3A3] text-xs font-mono flex flex-col items-center justify-center gap-3">
            <AlertTriangle className="w-6 h-6 text-[#F04400]" />
            <span>Failed to load audit trail from backend. Please use the Retry button above.</span>
          </div>
        ) : filteredEvents.length === 0 ? (
          <div className="p-12 text-center text-[#A3A3A3] text-xs font-mono">
            No audit records match the selected filter criteria.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead className="bg-[#0E0E0E] border-b border-[#242424] text-[#FFD91A] uppercase text-[10px] tracking-wider">
                <tr>
                  <th className="px-5 py-3.5">Timestamp (UTC)</th>
                  <th className="px-5 py-3.5">Event Type</th>
                  <th className="px-5 py-3.5">Actor &amp; Role</th>
                  <th className="px-5 py-3.5">Module</th>
                  <th className="px-5 py-3.5">Summary</th>
                  <th className="px-5 py-3.5">Severity</th>
                  <th className="px-5 py-3.5 text-right">Raw Event</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#242424]">
                {filteredEvents.map((evt) => (
                  <tr key={evt.id} className="hover:bg-[#0E0E0E] transition">
                    <td className="px-5 py-4 text-[#A3A3A3] whitespace-nowrap text-[11px]">
                      {evt.timestamp?.slice(0, 19).replace('T', ' ')}
                    </td>
                    <td className="px-5 py-4 font-black text-white whitespace-nowrap uppercase">
                      {evt.event_type}
                    </td>
                    <td className="px-5 py-4 text-white whitespace-nowrap font-medium">
                      {evt.actor}{' '}
                      <span className="text-[10px] text-[#A3A3A3] block uppercase font-mono">[{evt.role}]</span>
                    </td>
                    <td className="px-5 py-4 whitespace-nowrap">
                      <span className="px-2.5 py-0.5 rounded text-[10px] font-bold bg-[#0E0E0E] text-[#D4D4D4] border border-[#242424] uppercase">
                        {evt.module}
                      </span>
                    </td>
                    <td className="px-5 py-4 text-[#D4D4D4] max-w-md truncate font-mono">
                      {evt.summary}
                    </td>
                    <td className="px-5 py-4 whitespace-nowrap">
                      <StatusBadge severity={evt.severity} text={evt.severity} />
                    </td>
                    <td className="px-5 py-4 text-right whitespace-nowrap">
                      <button
                        onClick={() => setSelectedEvent(evt)}
                        className="px-3 py-1.5 rounded-xl bg-[#242424] hover:bg-[#333333] text-white border border-[#333333] transition inline-flex items-center gap-1.5 text-[11px] font-bold uppercase"
                      >
                        <Eye className="w-3.5 h-3.5 text-[#FFD91A]" />
                        <span>Inspect JSON</span>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Raw Event Modal */}
      {selectedEvent && (
        <Modal
          isOpen={!!selectedEvent}
          onClose={() => setSelectedEvent(null)}
          title={`Audit Event #${selectedEvent.id}`}
          subtitle={`Event: ${selectedEvent.event_type} • Actor: ${selectedEvent.actor}`}
        >
          <div className="space-y-4 text-xs font-mono">
            <div className="p-4 rounded-2xl bg-[#0E0E0E] border border-[#242424] space-y-2">
              <div className="flex justify-between items-center">
                <span className="text-[#A3A3A3] uppercase font-bold">Timestamp:</span>
                <span className="text-white font-bold">{selectedEvent.timestamp}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-[#A3A3A3] uppercase font-bold">Security Classification:</span>
                <StatusBadge severity={selectedEvent.severity} text={selectedEvent.severity} />
              </div>
              {selectedEvent.fingerprint && (
                <div className="pt-2 border-t border-[#242424]">
                  <span className="text-[#FFD91A] block mb-1 uppercase font-black text-[10px]">Associated Cryptographic Fingerprint:</span>
                  <FingerprintPill hash={selectedEvent.fingerprint} />
                </div>
              )}
            </div>

            <div className="space-y-1">
              <span className="text-[#A3A3A3] uppercase text-[10px] font-black">Event Summary</span>
              <p className="p-3.5 rounded-2xl bg-[#0E0E0E] border border-[#242424] text-white font-sans">
                {selectedEvent.summary}
              </p>
            </div>

            <div className="space-y-1">
              <span className="text-[#FFD91A] uppercase text-[10px] font-black">
                Raw Event Payload (JSON)
              </span>
              <pre className="p-4 rounded-2xl bg-[#000000] border border-[#242424] text-[#D0F030] overflow-x-auto text-[11px] font-mono leading-relaxed">
                {JSON.stringify(selectedEvent, null, 2)}
              </pre>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
};
