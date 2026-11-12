import React, { useState, useEffect, useCallback } from 'react';
import {
  ShieldAlert,
  ShieldCheck,
  UserCheck,
  Calendar,
  Clock,
  ToggleLeft,
  ToggleRight,
  Package,
  Sparkles,
  Loader2,
  CheckCircle,
  AlertCircle,
  Lock,
} from 'lucide-react';
import { SAMPLE_CARE_PROFILE } from '../services/sampleData';
import { MetricCard } from '../components/common/MetricCard';
import { StatusBadge } from '../components/common/StatusBadge';
import { MeridianLogo } from '../components/common/MeridianLogo';
import {
  fetchCareProfile,
  updateCareMode,
  triggerCareReorder,
  submitHumanApproval,
  createPayPalOrder,
  assistCareReorder,
  type CareAssistResponse,
  type PayPalOrderResponse,
  type PayPalCaptureResponse,
} from '../services/api';
import { PayPalCheckoutModal } from '../components/common/PayPalCheckoutModal';
import type { CareProfile } from '../types';

export const CarePage: React.FC = () => {
  const [profile, setProfile] = useState<CareProfile>(SAMPLE_CARE_PROFILE);
  const [mode, setMode] = useState<'MODE_A_EVERY_ORDER' | 'MODE_B_CONTROLLED_AUTO'>(
    profile.approval_mode
  );
  const [isLoadingProfile, setIsLoadingProfile] = useState<boolean>(true);
  const [isEvaluatingCare, setIsEvaluatingCare] = useState<boolean>(false);
  const [careAssistResult, setCareAssistResult] = useState<CareAssistResponse | null>(null);
  const [activeCheckoutOrder, setActiveCheckoutOrder] = useState<PayPalOrderResponse | null>(null);
  const [activeItemDetails, setActiveItemDetails] = useState<{ title: string; merchant: string } | null>(null);
  const [isOrdering, setIsOrdering] = useState<string | null>(null);
  const [orderedItems, setOrderedItems] = useState<Record<string, string>>({});
  const [reorderError, setReorderError] = useState<string | null>(null);
  const [isTogglingMode, setIsTogglingMode] = useState<boolean>(false);

  // Mode A Staged Proposal state
  const [pendingApproval, setPendingApproval] = useState<{
    proposalId: string;
    title: string;
    fingerprintSha256: string;
    total: number;
    merchant: string;
    scheduleId: string;
  } | null>(null);
  const [isApproving, setIsApproving] = useState<boolean>(false);

  const loadProfile = useCallback(async () => {
    setIsLoadingProfile(true);
    try {
      const p = await fetchCareProfile('care-eleanor-01');
      setProfile(p);
      setMode(p.approval_mode);
    } catch (err) {
      console.warn('Backend unavailable, using static fallback for Care profile:', err);
    } finally {
      setIsLoadingProfile(false);
    }
  }, []);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  const handleLaunchCareCheckout = async (item: typeof profile.monitored_items[0]) => {
    setIsOrdering(item.id);
    setReorderError(null);
    setPendingApproval(null);

    try {
      const res = await triggerCareReorder(item.id, profile.id, 'usr-alex-01');

      if (res.decision === 'DENY') {
        setReorderError(`Trust Engine Policy Denial: ${res.human_explanation}`);
        return;
      }

      if (res.requires_approval) {
        setPendingApproval({
          proposalId: res.proposal.id,
          title: item.name,
          fingerprintSha256: res.proposal.fingerprint_sha256,
          total: res.proposal.total_cents / 100,
          merchant: res.proposal.merchant_name,
          scheduleId: item.id,
        });
        return;
      }

      if (res.order) {
        setActiveCheckoutOrder(res.order);
        setActiveItemDetails({ title: item.name, merchant: item.merchant });
      }
    } catch (err: any) {
      console.error('Care reorder failure:', err);
      setReorderError(err.message || 'Failed to execute care replenishment proposal.');
    } finally {
      setIsOrdering(null);
    }
  };

  const handleConfirmGuardianApproval = async () => {
    if (!pendingApproval) return;
    setIsApproving(true);
    setReorderError(null);

    try {
      await submitHumanApproval(
        pendingApproval.proposalId,
        pendingApproval.fingerprintSha256,
        'usr-alex-01',
        'GUARDIAN_DELEGATED'
      );
      const order = await createPayPalOrder(pendingApproval.proposalId);
      setActiveCheckoutOrder(order);
      setActiveItemDetails({ title: pendingApproval.title, merchant: pendingApproval.merchant });
      setPendingApproval(null);
    } catch (err: any) {
      console.error('Approval failed:', err);
      setReorderError(err.message || 'Failed to submit guardian approval.');
    } finally {
      setIsApproving(false);
    }
  };

  const handleCareCaptureSuccess = (capture: PayPalCaptureResponse) => {
    if (activeCheckoutOrder) {
      setOrderedItems((prev) => ({
        ...prev,
        [activeItemDetails?.title || 'item']: capture.capture_id,
      }));
      loadProfile();
    }
  };

  const handleEvaluateCareAI = async () => {
    setIsEvaluatingCare(true);
    try {
      const res = await assistCareReorder('care-eleanor-01');
      setCareAssistResult(res);
    } catch (err) {
      console.error(err);
    } finally {
      setIsEvaluatingCare(false);
    }
  };

  const toggleMode = async () => {
    const newMode =
      mode === 'MODE_A_EVERY_ORDER' ? 'MODE_B_CONTROLLED_AUTO' : 'MODE_A_EVERY_ORDER';
    setIsTogglingMode(true);
    setReorderError(null);
    try {
      const updated = await updateCareMode(profile.id, newMode);
      setProfile(updated);
      setMode(updated.approval_mode);
    } catch (err: any) {
      console.error('Failed to update care approval mode:', err);
      setReorderError(err.message || 'Failed to update approval mode.');
    } finally {
      setIsTogglingMode(false);
    }
  };

  const remainingBudget = profile.monthly_budget - profile.monthly_spent;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 p-6 sm:p-7 rounded-3xl bg-[#171717] border border-[#242424] shadow-xl">
        <div className="space-y-1">
          <div className="flex items-center gap-2.5">
            <MeridianLogo variant="icon" size="xs" />
            <span className="px-2.5 py-0.5 rounded text-[10px] font-mono font-black bg-[#D0F030] text-black uppercase tracking-widest">
              ASSISTED CARE MODULE
            </span>
            <span className="text-xs font-mono text-[#FFD91A] font-bold">SOVEREIGN COMMERCE</span>
            {isLoadingProfile && <Loader2 className="w-3.5 h-3.5 text-[#F04400] animate-spin" />}
          </div>
          <h2 className="text-xl sm:text-2xl font-black text-white font-mono uppercase tracking-tight">
            Care / Assisted Commerce — Protected Autonomy
          </h2>
          <p className="text-xs text-[#A3A3A3] max-w-2xl font-mono leading-relaxed">
            Never miss an essential. Never spend without permission. Recipient retains sovereignty while authorized guardians provide oversight within strict financial bounds.
          </p>
        </div>

        {/* Approval Mode Selector */}
        <div className="flex items-center gap-3 p-3 rounded-2xl bg-[#0E0E0E] border border-[#242424] shrink-0">
          <div className="text-right">
            <span className="text-[10px] font-mono uppercase text-[#A3A3A3] font-bold block">
              Configured Mode:
            </span>
            <span className="text-xs font-mono font-black text-white uppercase">
              {mode === 'MODE_B_CONTROLLED_AUTO'
                ? 'Mode B: Auto-Reorder'
                : 'Mode A: Explicit Approval'}
            </span>
          </div>
          <button
            onClick={toggleMode}
            disabled={isTogglingMode}
            className="p-1 rounded-xl hover:bg-[#242424] text-[#D0F030] transition disabled:opacity-50"
            title="Toggle between Mode A (Require Approval for Every Order) and Mode B (Bounded Auto-Reorder)"
          >
            {mode === 'MODE_B_CONTROLLED_AUTO' ? (
              <ToggleRight className="w-8 h-8 text-[#D0F030]" />
            ) : (
              <ToggleLeft className="w-8 h-8 text-[#FFD91A]" />
            )}
          </button>
        </div>
      </div>

      {/* Error / Denial Alert Banner */}
      {reorderError && (
        <div className="p-4 rounded-2xl bg-[#2A0E08] border border-[#F04400] text-xs font-mono text-[#F04400] flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-[#F04400] shrink-0 mt-0.5" />
          <div className="space-y-1">
            <span className="font-black uppercase">Policy or Reorder Constraint Failure</span>
            <p className="text-[11px] text-[#D4D4D4]">{reorderError}</p>
          </div>
        </div>
      )}

      {/* Mode A Pending Human Approval Confirmation Card */}
      {pendingApproval && (
        <div className="p-6 rounded-3xl bg-[#1A180E] border border-[#FFD91A] text-xs font-mono space-y-3 shadow-xl">
          <div className="flex items-center justify-between">
            <span className="font-black text-[#FFD91A] flex items-center gap-2 uppercase tracking-wide">
              <Lock className="w-4 h-4 text-[#FFD91A]" />
              Mode A Protected Autonomy — Guardian Authorization Required
            </span>
            <span className="text-[10px] px-2.5 py-0.5 rounded bg-[#FFD91A] text-black font-black uppercase">
              Proposal #{pendingApproval.proposalId}
            </span>
          </div>
          <p className="text-[#D4D4D4] font-mono text-xs leading-relaxed">
            Replenishment proposal staged for <strong className="text-white">{pendingApproval.title}</strong> (${pendingApproval.total.toFixed(2)} at {pendingApproval.merchant}). Mode A mandates explicit human authorization before payment execution.
          </p>
          <div className="flex items-center gap-2 text-[11px] text-[#A3A3A3] font-mono">
            <span>Authoritative Fingerprint:</span>
            <code className="px-2.5 py-0.5 rounded bg-[#0E0E0E] border border-[#242424] text-[#D0F030] text-[10px] font-bold">
              {pendingApproval.fingerprintSha256.substring(0, 16)}...
            </code>
          </div>
          <div className="flex items-center gap-3 pt-2">
            <button
              onClick={handleConfirmGuardianApproval}
              disabled={isApproving}
              className="px-5 py-2.5 rounded-xl bg-[#FFD91A] hover:bg-[#ffe040] text-black font-black text-xs font-mono uppercase tracking-wider transition flex items-center gap-2 disabled:opacity-50 shadow-md"
            >
              {isApproving ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin text-black" />
                  <span>Signing Approval...</span>
                </>
              ) : (
                <>
                  <ShieldCheck className="w-4 h-4 stroke-[2.5]" />
                  <span>Authorize Order as Guardian</span>
                </>
              )}
            </button>
            <button
              onClick={() => setPendingApproval(null)}
              className="px-4 py-2.5 rounded-xl bg-[#242424] hover:bg-[#333333] text-white text-xs font-mono uppercase font-bold transition"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Metrics Row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="Care Recipient"
          value={profile.recipient_name}
          subtitle={profile.relationship}
          icon={UserCheck}
          accent="lime"
        />
        <MetricCard
          title="Authorized Monthly Budget"
          value={`$${profile.monthly_budget.toFixed(2)}`}
          subtitle={`Remaining: $${remainingBudget.toFixed(2)}`}
          changeType="positive"
          change="Cap enforced"
          icon={ShieldCheck}
          accent="orange"
        />
        <MetricCard
          title="Spent This Month (Live)"
          value={`$${profile.monthly_spent.toFixed(2)}`}
          subtitle={
            profile.simulated_monthly_spent && profile.simulated_monthly_spent > 0
              ? `Simulated Demo: $${profile.simulated_monthly_spent.toFixed(2)}`
              : (profile.monthly_budget > 0
                  ? `${Math.round((profile.monthly_spent / profile.monthly_budget) * 100)}% of limit`
                  : 'Active')
          }
          icon={Clock}
          accent="yellow"
        />
        <MetricCard
          title="Urgent Reorder"
          value="In 3 Days"
          subtitle="Continuous Glucose Monitor"
          change="Auto-order eligible"
          changeType="positive"
          icon={Package}
          accent="default"
        />
      </div>

      {/* Mode Comparison Explainer Box */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div
          className={`p-5 rounded-2xl border transition ${
            mode === 'MODE_A_EVERY_ORDER'
              ? 'bg-[#1A180E] border-[#FFD91A] shadow-lg'
              : 'bg-[#171717] border-[#242424] opacity-60'
          }`}
        >
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-mono font-black text-[#FFD91A] uppercase tracking-wider">
              MODE A — EXPLICIT APPROVAL FOR EVERY ORDER
            </span>
            {mode === 'MODE_A_EVERY_ORDER' && (
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[#FFD91A] text-black font-black uppercase">
                ACTIVE
              </span>
            )}
          </div>
          <p className="text-xs text-[#D4D4D4] leading-relaxed font-mono">
            AI predicts when an essential is low and stages the proposal. However, <strong className="text-white">no payment is executed</strong> until the care recipient or primary guardian manually confirms the exact amount, merchant, and line items.
          </p>
        </div>

        <div
          className={`p-5 rounded-2xl border transition ${
            mode === 'MODE_B_CONTROLLED_AUTO'
              ? 'bg-[#0E0E0E] border-[#D0F030] shadow-lg'
              : 'bg-[#171717] border-[#242424] opacity-60'
          }`}
        >
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-mono font-black text-[#D0F030] uppercase tracking-wider">
              MODE B — CONTROLLED AUTOMATIC REORDERING
            </span>
            {mode === 'MODE_B_CONTROLLED_AUTO' && (
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[#D0F030] text-black font-black uppercase">
                ACTIVE
              </span>
            )}
          </div>
          <p className="text-xs text-[#D4D4D4] leading-relaxed font-mono">
            Orders proceed autonomously <strong className="text-white">only if all conditions are satisfied</strong>: product on whitelist, merchant on whitelist, under $75 ceiling, within monthly budget, and schedule match. If price increases even $1, it reverts to Mode A.
          </p>
        </div>
      </div>

      {/* AI Care Intelligence & Schedule Assessment */}
      <div className="p-6 rounded-3xl bg-[#171717] border border-[#242424] shadow-xl space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h3 className="text-xs font-mono font-black uppercase tracking-widest text-[#FFD91A] flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-[#F04400]" />
              Gemini Assisted Commerce Reorder Engine
            </h3>
            <p className="text-xs text-[#A3A3A3] font-mono mt-0.5">
              Evaluates care essentials against authorized schedules. Strictly forbids autonomous prescription modifications or emergency bypasses.
            </p>
          </div>
          <button
            onClick={handleEvaluateCareAI}
            disabled={isEvaluatingCare}
            className="px-5 py-2.5 rounded-xl bg-[#F04400] hover:bg-[#ff5714] text-black text-xs font-mono font-black uppercase tracking-wider transition flex items-center gap-2 shrink-0 disabled:opacity-50 shadow-md"
          >
            {isEvaluatingCare ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin text-black" />
                <span>Auditing Schedule...</span>
              </>
            ) : (
              <>
                <Sparkles className="w-4 h-4 text-black" />
                <span>Run Care Reorder Audit</span>
              </>
            )}
          </button>
        </div>

        {careAssistResult && (
          <div className="mt-3 p-5 rounded-2xl bg-[#0E0E0E] border border-[#D0F030] text-xs font-mono space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-[#D0F030] font-black uppercase flex items-center gap-2">
                <CheckCircle className="w-4 h-4" />
                Care Plan Audit for {careAssistResult.recipient_name}
              </span>
              <span className="text-[10px] px-2.5 py-0.5 rounded bg-[#D0F030] text-black font-black uppercase">
                Source: {careAssistResult.source}
              </span>
            </div>
            <p className="text-[#D4D4D4] leading-relaxed font-sans text-xs">
              <strong className="text-white">Summary:</strong> {careAssistResult.summary}
            </p>
            <div className="p-3.5 rounded-xl bg-[#171717] border border-[#242424] text-[11px] text-white space-y-1">
              <p><strong className="text-[#FFD91A]">Guardian Notes:</strong> {careAssistResult.guardian_notes}</p>
              <p><strong className="text-[#D0F030]">Schedule Status:</strong> <span className="text-[#D0F030] font-bold">{careAssistResult.reorder_schedule_status}</span></p>
            </div>
            <p className="text-[10px] text-[#FFD91A] italic">
              {careAssistResult.safety_advisory}
            </p>
          </div>
        )}
      </div>

      {/* Essentials Reorder Schedule */}
      <div className="rounded-3xl bg-[#171717] border border-[#242424] p-6 shadow-xl space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-black text-white font-mono uppercase tracking-wider flex items-center gap-2">
              <Calendar className="w-4 h-4 text-[#FFD91A]" />
              Monitored Recurring Essentials ({profile.monitored_items.length})
            </h3>
            <p className="text-xs text-[#A3A3A3] font-mono mt-0.5">
              Configured supplies tracked for replenishment. Medical supplies derive from authorized care-plan records.
            </p>
          </div>
          <span className="text-xs font-mono text-[#D0F030] font-bold">Next predicted replenishment: Nov 11</span>
        </div>

        <div className="divide-y divide-[#242424] rounded-2xl bg-[#0E0E0E] border border-[#242424] overflow-hidden">
          {profile.monitored_items.map((item) => (
            <div
              key={item.id}
              className="p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs font-mono hover:bg-[#171717]/60 transition"
            >
              <div className="space-y-1.5">
                <div className="flex items-center gap-2.5">
                  <span className="font-black text-white text-sm uppercase">{item.name}</span>
                  <span className="px-2 py-0.5 rounded text-[10px] bg-[#242424] text-[#D4D4D4] border border-[#333333] font-bold">
                    {item.category}
                  </span>
                </div>
                <div className="flex items-center gap-3 text-[#A3A3A3] text-[11px]">
                  <span>Frequency: {item.frequency}</span>
                  <span>•</span>
                  <span>Preferred Merchant: {item.merchant}</span>
                  <span>•</span>
                  <span>Last Ordered: {item.last_ordered}</span>
                </div>
              </div>

              <div className="flex items-center justify-between sm:justify-end gap-4">
                <div className="text-left sm:text-right">
                  <span className="font-black text-white text-sm">${item.estimated_cost.toFixed(2)}</span>
                  <p
                    className={`text-[10px] font-bold ${
                      item.days_remaining <= 5 ? 'text-[#F04400]' : 'text-[#D0F030]'
                    }`}
                  >
                    {item.days_remaining} days supply left
                  </p>
                </div>
                <StatusBadge
                  severity={item.days_remaining <= 5 ? 'WARNING' : 'SUCCESS'}
                  text={item.days_remaining <= 5 ? 'REORDER READY' : 'IN STOCK'}
                />

                {orderedItems[item.name] ? (
                  <span className="px-3 py-1.5 rounded-xl bg-[#0E0E0E] text-[#D0F030] border border-[#D0F030] text-[10px] font-mono font-black uppercase flex items-center gap-1.5">
                    <CheckCircle className="w-3.5 h-3.5 text-[#D0F030]" />
                    <span>Captured on PayPal</span>
                  </span>
                ) : item.days_remaining <= 5 ? (
                  <button
                    onClick={() => handleLaunchCareCheckout(item)}
                    disabled={isOrdering === item.id}
                    className="px-4 py-2 rounded-xl bg-[#FFD91A] hover:bg-[#ffe040] text-black font-black text-xs font-mono uppercase tracking-wider transition flex items-center gap-1.5 disabled:opacity-50 shadow-md"
                  >
                    <Lock className="w-3.5 h-3.5 stroke-[2.5]" />
                    <span>{isOrdering === item.id ? 'Evaluating...' : 'Authorize Reorder'}</span>
                  </button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Guardians Circle & Non-Bypass Invariant */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Left: Authorized Guardian Circle */}
        <div className="rounded-3xl bg-[#171717] border border-[#242424] p-6 shadow-xl space-y-3">
          <h4 className="text-xs font-mono font-black uppercase tracking-widest text-[#FFD91A]">
            Authorized Care Circle ({profile.guardians.length})
          </h4>
          <div className="space-y-2.5">
            {profile.guardians.map((g) => (
              <div
                key={g.email}
                className="p-3.5 rounded-2xl bg-[#0E0E0E] border border-[#242424] flex items-center justify-between text-xs font-mono"
              >
                <div>
                  <p className="font-bold text-white">{g.name}</p>
                  <p className="text-[10px] text-[#A3A3A3]">{g.role}</p>
                </div>
                <span className="text-[11px] text-[#D0F030] font-bold">{g.email}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Right: Anti-Emergency Bypass Security Invariant */}
        <div className="rounded-3xl bg-[#171717] border border-[#242424] p-6 shadow-xl space-y-3">
          <div className="flex items-center gap-2 text-[#F04400] font-mono font-black text-xs uppercase tracking-wide">
            <ShieldAlert className="w-4 h-4 text-[#F04400]" />
            <span>CONTINUITY &amp; ANTI-BYPASS INVARIANT:</span>
          </div>
          <p className="text-xs text-[#D4D4D4] leading-relaxed font-mono">
            If an authorized caregiver is unresponsive during an urgent replenishment window, MERIDIAN escalates notifications across the authorized care circle, but <strong className="text-[#FFD91A]">never bypasses approval policy</strong> under the guise of an &quot;emergency&quot;. Real human consent is the inviolable foundation of financial safety.
          </p>
        </div>
      </div>

      {/* PayPal Sandbox Checkout Modal */}
      {activeCheckoutOrder && activeItemDetails && (
        <PayPalCheckoutModal
          order={activeCheckoutOrder}
          proposalTitle={activeItemDetails.title}
          merchantName={activeItemDetails.merchant}
          itemsCount={1}
          onClose={() => setActiveCheckoutOrder(null)}
          onSuccess={handleCareCaptureSuccess}
        />
      )}
    </div>
  );
};
