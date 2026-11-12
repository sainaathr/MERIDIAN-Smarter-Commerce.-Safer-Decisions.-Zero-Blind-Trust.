import React, { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import {
  DollarSign,
  Clock,
  Wallet,
  ShieldCheck,
  ArrowUpRight,
  ChevronRight,
  ShieldAlert,
  Sparkles,
  Activity,
  Eye,
  Loader2,
  AlertCircle,
  RefreshCw,
} from 'lucide-react';
import { MetricCard } from '../components/common/MetricCard';
import { StatusBadge } from '../components/common/StatusBadge';
import { FingerprintPill } from '../components/trust/FingerprintPill';
import { MeridianLogo } from '../components/common/MeridianLogo';
import { ProposalReviewModal } from '../components/common/ProposalReviewModal';
import { fetchProposals, fetchAuditEvents } from '../services/api';
import type { TransactionProposal, AuditEvent } from '../types';

export const OverviewPage: React.FC = () => {
  const [proposals, setProposals] = useState<TransactionProposal[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [selectedProposal, setSelectedProposal] = useState<TransactionProposal | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const loadOverviewData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [liveProposals, liveAudits] = await Promise.all([
        fetchProposals(),
        fetchAuditEvents(undefined, undefined, 5),
      ]);
      setProposals(liveProposals);
      setAuditEvents(liveAudits);
    } catch (err: any) {
      console.error('Failed to load overview live data:', err);
      setError(err.message || 'Failed to connect to Universal Trust Engine.');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadOverviewData();
  }, [loadOverviewData]);

  const pendingProposal = proposals.find(
    (p) => p.state === 'AWAITING_APPROVAL'
  );
  const pendingCount = proposals.filter((p) => p.state === 'AWAITING_APPROVAL').length;

  return (
    <div className="space-y-6">
      {/* Editorial Hero Banner */}
      <div className="p-6 sm:p-8 rounded-3xl bg-[#F04400] text-black shadow-2xl relative overflow-hidden">
        <div className="relative z-10 flex flex-col md:flex-row md:items-center justify-between gap-6">
          <div className="space-y-2 max-w-2xl">
            <div className="flex items-center gap-3">
              <div className="p-1 rounded-lg bg-black inline-flex">
                <MeridianLogo variant="icon" size="xs" />
              </div>
              <span className="px-3 py-1 rounded-md text-[10px] font-mono font-black bg-black text-[#FFD91A] uppercase tracking-widest">
                AUTONOMOUS COMMERCE KERNEL
              </span>
              <span className="text-xs font-mono font-black text-black/80">ENGINE v1.0</span>
            </div>
            <h1 className="text-3xl sm:text-4xl lg:text-5xl font-black font-mono uppercase tracking-tighter text-black leading-none">
              COMMERCE WITHOUT BLIND TRUST
            </h1>
            <p className="text-xs sm:text-sm font-mono font-bold text-black/90 leading-relaxed">
              AI agents propose purchases; the deterministic Universal Trust Engine holds cryptographic keys, verifies human consent, and authorizes payment.
            </p>
          </div>

          <div className="flex flex-col gap-2 shrink-0">
            <div className="p-3.5 rounded-2xl bg-black text-[#D0F030] border border-black/40 font-mono text-xs shadow-lg flex items-center gap-2.5">
              <span className="w-2.5 h-2.5 rounded-full bg-[#D0F030] animate-pulse" />
              <span className="font-black uppercase tracking-wider">10/10 INVARIANTS ACTIVE</span>
            </div>
            <div className="p-3.5 rounded-2xl bg-[#FFD91A] text-black border border-black/20 font-mono text-xs shadow-lg flex items-center justify-between gap-4">
              <span className="font-black uppercase">PAYPAL SANDBOX</span>
              <span className="text-[10px] bg-black text-white px-2 py-0.5 rounded font-black">ORDERS v2</span>
            </div>
          </div>
        </div>
      </div>

      {/* Error State Banner */}
      {error && (
        <div className="p-4 rounded-2xl bg-[#2A0E08] border border-[#F04400] text-[#F04400] space-y-3 font-mono">
          <div className="flex items-center gap-2 font-black text-xs">
            <AlertCircle className="w-4 h-4 text-[#F04400]" />
            <span>OVERVIEW SYNCHRONIZATION ERROR</span>
          </div>
          <p className="text-xs">{error}</p>
          <button
            onClick={() => loadOverviewData()}
            className="px-4 py-2 rounded-xl bg-[#F04400] hover:bg-[#ff5714] text-black text-xs font-black uppercase transition flex items-center gap-2"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Retry Connection</span>
          </button>
        </div>
      )}

      {/* Loading Spinner */}
      {isLoading && (
        <div className="p-8 text-center rounded-2xl bg-[#171717] border border-[#242424] text-[#A3A3A3] font-mono text-xs flex flex-col items-center justify-center gap-2">
          <Loader2 className="w-6 h-6 animate-spin text-[#F04400]" />
          <span>Synchronizing live proposals and audit events...</span>
        </div>
      )}

      {/* Metrics Row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="Total Spent (Nov)"
          value="$845.00"
          subtitle="Across all 4 modules"
          change="+12% from last month"
          changeType="neutral"
          icon={DollarSign}
          accent="orange"
        />
        <MetricCard
          title="Pending Approvals"
          value={isLoading ? '...' : `${pendingCount} Required`}
          subtitle={
            pendingProposal?.module?.toUpperCase() === 'CARE'
              ? 'Awaiting guardian consent'
              : pendingCount > 0
              ? 'Awaiting human authorization'
              : 'All clear — no actions pending'
          }
          change={pendingCount > 0 ? `Urgent: ${pendingCount} pending` : 'All clear'}
          changeType={pendingCount > 0 ? 'negative' : 'positive'}
          icon={Clock}
          badge={pendingCount > 0 ? 'ACTION REQUIRED' : undefined}
          accent="yellow"
        />
        <MetricCard
          title="Authorized Monthly Budget"
          value="$2,450.00"
          subtitle="Combined configured limit"
          icon={Wallet}
          accent="default"
        />
        <MetricCard
          title="Trust Invariants Active"
          value="10 / 10"
          subtitle="Kernel enforcement online"
          change="0 violations allowed"
          changeType="positive"
          icon={ShieldCheck}
          accent="lime"
        />
      </div>

      {/* Primary Action Callout: Pending Approval Inbox */}
      {pendingProposal && (
        <div className="rounded-3xl bg-[#FFD91A] text-black p-6 sm:p-7 shadow-2xl space-y-4">
          <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="px-2.5 py-0.5 rounded text-[10px] font-mono font-black bg-black text-[#FFD91A] uppercase tracking-wider">
                  PENDING HUMAN CONSENT
                </span>
                <span className="text-xs font-mono font-black uppercase text-black/80">
                  MODULE: {pendingProposal.module}
                </span>
              </div>
              <h3 className="text-xl sm:text-2xl font-black text-black font-mono uppercase tracking-tight">
                {pendingProposal.title}
              </h3>
              <p className="text-xs font-mono font-bold text-black/80 max-w-2xl leading-relaxed">
                {pendingProposal.ai_reasoning}
              </p>
              <div className="pt-2 flex flex-wrap items-center gap-3">
                <FingerprintPill hash={pendingProposal.fingerprint_sha256} />
                <span className="text-xs font-mono text-black font-bold">
                  Merchant: <span className="underline">{pendingProposal.merchant_name}</span>
                </span>
                <span className="text-xs font-mono text-black font-black">
                  Total: ${(pendingProposal.total ?? 0).toFixed(2)} {pendingProposal.currency}
                </span>
              </div>
            </div>

            <div className="flex items-center gap-3 w-full md:w-auto shrink-0 pt-2 md:pt-0">
              <button
                onClick={() => setSelectedProposal(pendingProposal)}
                className="flex-1 md:flex-initial inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-black hover:bg-[#171717] text-[#FFD91A] text-xs font-mono font-black uppercase transition shadow-md cursor-pointer"
              >
                <Eye className="w-4 h-4 text-[#FFD91A]" />
                Review &amp; Decide
              </button>
              <Link
                to={
                  pendingProposal.module?.toUpperCase() === 'BUSINESS'
                    ? `/business?review=${pendingProposal.id}`
                    : pendingProposal.module?.toUpperCase() === 'CARE'
                    ? `/care?review=${pendingProposal.id}`
                    : `/personal?review=${pendingProposal.id}`
                }
                className="flex-1 md:flex-initial inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-[#F04400] hover:bg-[#ff5714] text-black font-black text-xs font-mono uppercase tracking-wider shadow-lg transition"
              >
                Open in {pendingProposal.module?.toUpperCase() === 'BUSINESS' ? 'FLOW' : pendingProposal.module?.toUpperCase() === 'CARE' ? 'CARE' : 'PACT'}
                <ArrowUpRight className="w-4 h-4 stroke-[3]" />
              </Link>
            </div>
          </div>
        </div>
      )}

      {/* Grid: 4 Module Statuses & Security Status */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left 2 Cols: 4 Commerce Modules Overview */}
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-black uppercase tracking-widest text-[#FFD91A] font-mono flex items-center gap-2">
              <Activity className="w-4 h-4 text-[#F04400]" />
              Active Commerce Modules
            </h2>
            <span className="text-xs text-[#A3A3A3] font-mono">4 Connected Subsystems</span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {/* Personal / PACT */}
            <div className="rounded-2xl bg-[#171717] border border-[#242424] hover:border-[#F04400] p-5 shadow-lg transition duration-200 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono font-black text-[#F04400] uppercase tracking-wider">
                  PERSONAL // PACT
                </span>
                <StatusBadge state="AWAITING_APPROVAL" text="1 PENDING" />
              </div>
              <p className="text-xs text-[#D4D4D4] font-mono leading-relaxed">
                Autonomous personal buyer bound to strict price ceilings and merchant whitelists.
              </p>
              <div className="flex items-center justify-between text-xs font-mono text-[#A3A3A3] pt-3 border-t border-[#242424]">
                <span>Budget: $85 / $400</span>
                <Link to="/personal" className="text-[#FFD91A] hover:text-[#D0F030] font-black uppercase flex items-center gap-1 transition">
                  Open <ChevronRight className="w-3.5 h-3.5" />
                </Link>
              </div>
            </div>

            {/* Business / FLOW */}
            <div className="rounded-2xl bg-[#171717] border border-[#242424] hover:border-[#FFD91A] p-5 shadow-lg transition duration-200 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono font-black text-[#FFD91A] uppercase tracking-wider">
                  BUSINESS // FLOW
                </span>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[#D0F030] text-black font-black uppercase">
                  RUNWAY HEALTHY
                </span>
              </div>
              <p className="text-xs text-[#D4D4D4] font-mono leading-relaxed">
                Affordability intelligence, supplier payables, and 30-day cashflow runway projections.
              </p>
              <div className="flex items-center justify-between text-xs font-mono text-[#A3A3A3] pt-3 border-t border-[#242424]">
                <span>Next Due: 2 days ($480)</span>
                <Link to="/business" className="text-[#FFD91A] hover:text-[#D0F030] font-black uppercase flex items-center gap-1 transition">
                  Open <ChevronRight className="w-3.5 h-3.5" />
                </Link>
              </div>
            </div>

            {/* Collective / COMMON */}
            <div className="rounded-2xl bg-[#171717] border border-[#242424] hover:border-[#D0F030] p-5 shadow-lg transition duration-200 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono font-black text-[#D0F030] uppercase tracking-wider">
                  COLLECTIVE // COMMON
                </span>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[#FFD91A] text-black font-black uppercase">
                  75% FUNDED
                </span>
              </div>
              <p className="text-xs text-[#D4D4D4] font-mono leading-relaxed">
                Group purchasing pools with transparent member pledges and volume discount capture.
              </p>
              <div className="flex items-center justify-between text-xs font-mono text-[#A3A3A3] pt-3 border-t border-[#242424]">
                <span>Podcast Studio: 3/4 Paid</span>
                <Link to="/collective" className="text-[#FFD91A] hover:text-[#D0F030] font-black uppercase flex items-center gap-1 transition">
                  Open <ChevronRight className="w-3.5 h-3.5" />
                </Link>
              </div>
            </div>

            {/* Care / Assisted */}
            <div className="rounded-2xl bg-[#171717] border border-[#242424] hover:border-[#F04400] p-5 shadow-lg transition duration-200 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono font-black text-[#F04400] uppercase tracking-wider">
                  CARE // ASSISTED
                </span>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[#242424] text-[#D0F030] border border-[#333333] font-black uppercase">
                  MODE B ACTIVE
                </span>
              </div>
              <p className="text-xs text-[#D4D4D4] font-mono leading-relaxed">
                Assisted recurring essentials reordering with non-bypassable guardian boundaries.
              </p>
              <div className="flex items-center justify-between text-xs font-mono text-[#A3A3A3] pt-3 border-t border-[#242424]">
                <span>Reorder in: 3 days</span>
                <Link to="/care" className="text-[#FFD91A] hover:text-[#D0F030] font-black uppercase flex items-center gap-1 transition">
                  Open <ChevronRight className="w-3.5 h-3.5" />
                </Link>
              </div>
            </div>
          </div>

          {/* Recent Trust & Audit Events Section */}
          <div className="rounded-2xl bg-[#171717] border border-[#242424] p-6 shadow-lg space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-black text-white font-mono uppercase tracking-wider flex items-center gap-2">
                <ShieldAlert className="w-4 h-4 text-[#F04400]" />
                Recent Trust Engine Enforcements
              </h3>
              <Link to="/audit" className="text-xs text-[#FFD91A] hover:text-[#D0F030] font-black font-mono uppercase transition">
                View All Events &rarr;
              </Link>
            </div>

            <div className="space-y-3">
              {auditEvents.length === 0 && !isLoading && (
                <div className="p-6 text-center text-[#A3A3A3] font-mono text-xs rounded-xl bg-[#0E0E0E] border border-[#242424]">
                  No audit events recorded yet.
                </div>
              )}
              {auditEvents.slice(0, 3).map((evt) => (
                <div
                  key={evt.id}
                  className="flex items-start justify-between gap-3 p-3.5 rounded-xl bg-[#0E0E0E] border border-[#242424] text-xs font-mono"
                >
                  <div className="space-y-1.5 flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono text-[#A3A3A3] text-[10px]">{evt.timestamp}</span>
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-[#242424] text-[#FFD91A]">
                        {evt.module}
                      </span>
                      <StatusBadge severity={evt.severity} text={evt.event_type} />
                    </div>
                    <p className="text-white font-medium text-xs truncate">{evt.summary}</p>
                    {evt.fingerprint && <FingerprintPill hash={evt.fingerprint} compact />}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Right Col: Infrastructure Status */}
        <div className="space-y-4">
          <h2 className="text-sm font-black uppercase tracking-widest text-[#FFD91A] font-mono flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-[#D0F030]" />
            Infrastructure Status
          </h2>

          {/* PayPal Sandbox Card */}
          <div className="rounded-2xl bg-[#171717] border border-[#242424] p-5 shadow-lg space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-black font-mono text-white uppercase">PayPal REST v2</span>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-black bg-[#FFD91A] text-black">
                SANDBOX READY
              </span>
            </div>
            <p className="text-xs text-[#D4D4D4] font-mono leading-relaxed">
              Orders API v2 configured. Capture calls require cryptographic SHA-256 fingerprint match.
            </p>
            <div className="text-xs font-mono text-[#A3A3A3] space-y-1.5 bg-[#0E0E0E] p-3 rounded-xl border border-[#242424]">
              <div className="flex justify-between">
                <span>Auth Mode:</span>
                <span className="text-white font-bold">OAuth 2.0 (Server)</span>
              </div>
              <div className="flex justify-between">
                <span>Idempotency:</span>
                <span className="text-[#D0F030] font-black">ACTIVE</span>
              </div>
              <div className="flex justify-between">
                <span>Capture Gateway:</span>
                <span className="text-white">api-m.sandbox.paypal.com</span>
              </div>
            </div>
          </div>

          {/* AI Orchestrator Card */}
          <div className="rounded-2xl bg-[#171717] border border-[#242424] p-5 shadow-lg space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-black font-mono text-white uppercase">Google Gemini</span>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-black bg-[#F04400] text-black">
                PROPOSAL ONLY
              </span>
            </div>
            <p className="text-xs text-[#D4D4D4] font-mono leading-relaxed">
              Gemini provides natural language intent extraction and proposal generation. Direct payment authority is blocked.
            </p>
            <div className="text-xs font-mono text-[#A3A3A3] space-y-1.5 bg-[#0E0E0E] p-3 rounded-xl border border-[#242424]">
              <div className="flex justify-between">
                <span>Authority Level:</span>
                <span className="text-[#F04400] font-black">0 Authority (Blocked)</span>
              </div>
              <div className="flex justify-between">
                <span>Prompt Quarantine:</span>
                <span className="text-[#D0F030] font-black">ACTIVE</span>
              </div>
              <div className="flex justify-between">
                <span>Schema Validator:</span>
                <span className="text-white">Pydantic v2 Strict</span>
              </div>
            </div>
          </div>

          {/* Quick Trust Centre Link */}
          <div className="rounded-2xl bg-[#F04400] text-black p-5 shadow-lg space-y-3">
            <div className="flex items-center gap-2">
              <ShieldCheck className="w-5 h-5 text-black stroke-[2.5]" />
              <h4 className="text-xs font-black text-black font-mono uppercase tracking-wider">
                Universal Trust Engine
              </h4>
            </div>
            <p className="text-xs text-black/90 font-mono font-bold leading-relaxed">
              Explore the deterministic policies, tamper invalidation math, and active consent rules governing every module.
            </p>
            <Link
              to="/trust"
              className="inline-flex items-center gap-2 text-xs font-mono font-black text-black bg-[#FFD91A] hover:bg-[#ffe040] px-4 py-2 rounded-xl uppercase transition shadow-md"
            >
              Open Trust Centre &rarr;
            </Link>
          </div>
        </div>
      </div>

      {/* Proposal Review Modal */}
      {selectedProposal && (
        <ProposalReviewModal
          isOpen={!!selectedProposal}
          proposal={selectedProposal}
          onClose={() => setSelectedProposal(null)}
          onStatusChanged={(updated) => {
            setSelectedProposal(updated);
            loadOverviewData();
          }}
          onProceedToCheckout={(prop) => {
            setSelectedProposal(null);
            // Navigate to the respective module with the proposal ready for checkout
            const path = prop.module?.toUpperCase() === 'BUSINESS' ? '/business' : '/personal';
            window.location.href = `${path}?review=${prop.id}`;
          }}
        />
      )}
    </div>
  );
};
