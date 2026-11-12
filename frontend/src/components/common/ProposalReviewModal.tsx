import React, { useState, useEffect } from 'react';
import {
  X,
  ShieldCheck,
  ShieldAlert,
  AlertTriangle,
  Lock,
  CheckCircle2,
  XCircle,
  Loader2,
  UserCheck,
  Calendar,
  Building2,
  CreditCard,
  ShoppingBag,
} from 'lucide-react';
import type { TransactionProposal } from '../../types';
import { StatusBadge } from './StatusBadge';
import { FingerprintPill } from '../trust/FingerprintPill';
import { submitHumanApproval, rejectProposal } from '../../services/api';

interface ProposalReviewModalProps {
  proposal: TransactionProposal | null;
  isOpen: boolean;
  onClose: () => void;
  onStatusChanged: (updatedProposal: TransactionProposal) => void;
  onProceedToCheckout?: (proposal: TransactionProposal) => void;
  currentUserId?: string;
}

export const ProposalReviewModal: React.FC<ProposalReviewModalProps> = ({
  proposal,
  isOpen,
  onClose,
  onStatusChanged,
  onProceedToCheckout,
  currentUserId = 'usr-alex-01',
}) => {
  const [activeProposal, setActiveProposal] = useState<TransactionProposal | null>(proposal);
  const [isApproving, setIsApproving] = useState(false);
  const [isRejecting, setIsRejecting] = useState(false);
  const [showApprovalConfirm, setShowApprovalConfirm] = useState(false);
  const [showRejectPrompt, setShowRejectPrompt] = useState(false);
  const [rejectReason, setRejectReason] = useState('Rejected by user review');
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    setActiveProposal(proposal);
    setShowApprovalConfirm(false);
    setShowRejectPrompt(false);
    setActionError(null);
  }, [proposal]);

  if (!isOpen || !activeProposal) return null;

  const isCareModule = activeProposal.module?.toUpperCase() === 'CARE' || !!(activeProposal as any).care_profile_id;
  const isGuardianRequired = isCareModule || (activeProposal as any).requires_guardian_oversight || (activeProposal as any).approval_status === 'AWAITING_GUARDIAN_CONSENT';
  const isAwaiting = activeProposal.state === 'AWAITING_APPROVAL';
  const isApproved = activeProposal.state === 'APPROVED';
  const isPolicyDenied = activeProposal.policy_decision === 'DENY' || activeProposal.state === 'REJECTED';
  const isPaid = activeProposal.state === 'PAID';
  const isPaymentPending = activeProposal.state === 'PAYMENT_PENDING';

  // Handle Human Approval
  const handleConfirmApproval = async () => {
    if (!activeProposal.fingerprint_sha256) {
      setActionError('Cannot approve proposal: cryptographic fingerprint is missing.');
      return;
    }
    if (isPolicyDenied) {
      setActionError('Cannot approve proposal: Universal Trust Engine policy decision is DENY.');
      return;
    }

    setIsApproving(true);
    setActionError(null);
    try {
      const approvalType = isGuardianRequired ? 'GUARDIAN_DELEGATED' : 'USER_DIRECT';
      await submitHumanApproval(activeProposal.id, activeProposal.fingerprint_sha256, currentUserId, approvalType);

      const updated: TransactionProposal = {
        ...activeProposal,
        state: 'APPROVED',
        approval_status: 'APPROVED',
        requires_approval: false,
      };
      setActiveProposal(updated);
      onStatusChanged(updated);
      setShowApprovalConfirm(false);
    } catch (err: any) {
      console.error('Approval failed:', err);
      setActionError(err.message || 'Failed to submit human approval to Trust Engine.');
    } finally {
      setIsApproving(false);
    }
  };

  // Handle Proposal Rejection
  const handleConfirmRejection = async () => {
    setIsRejecting(true);
    setActionError(null);
    try {
      await rejectProposal(activeProposal.id, rejectReason, currentUserId, 'USER');
      const updated: TransactionProposal = {
        ...activeProposal,
        state: 'REJECTED',
        approval_status: 'REJECTED',
        requires_approval: false,
      };
      setActiveProposal(updated);
      onStatusChanged(updated);
      setShowRejectPrompt(false);
    } catch (err: any) {
      console.error('Rejection failed:', err);
      setActionError(err.message || 'Failed to reject proposal.');
    } finally {
      setIsRejecting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/85 backdrop-blur-md animate-in fade-in duration-200">
      <div className="relative w-full max-w-2xl max-h-[90vh] flex flex-col rounded-3xl bg-[#171717] border border-[#242424] shadow-2xl overflow-hidden font-mono text-xs">
        {/* Header */}
        <div className="px-6 py-4 bg-[#0E0E0E] border-b border-[#242424] flex items-center justify-between shrink-0">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-xl bg-[#F04400] text-black">
              <ShoppingBag className="w-5 h-5 stroke-[2.5]" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-mono font-black px-2 py-0.5 rounded bg-black text-[#FFD91A] border border-[#242424] uppercase">
                  {activeProposal.module} MODULE
                </span>
                <span className="text-[#A3A3A3] text-[10px]">ID: {activeProposal.id.slice(0, 12)}...</span>
              </div>
              <h3 className="text-base font-black text-white uppercase tracking-tight mt-0.5">
                {activeProposal.title}
              </h3>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <StatusBadge state={activeProposal.state} />
            <button
              onClick={onClose}
              className="p-1.5 rounded-xl text-[#A3A3A3] hover:text-white hover:bg-[#242424] transition ml-2"
              title="Close modal"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Scrollable Content */}
        <div className="p-6 overflow-y-auto space-y-5 flex-1">
          {/* Action Error Banner */}
          {actionError && (
            <div className="p-3.5 rounded-xl bg-[#2A0E08] border border-[#F04400] text-[#F04400] flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 shrink-0 text-[#F04400]" />
              <span>{actionError}</span>
            </div>
          )}

          {/* Key Overview Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="p-3.5 rounded-2xl bg-[#0E0E0E] border border-[#242424] space-y-1">
              <span className="text-[10px] text-[#A3A3A3] uppercase font-bold flex items-center gap-1">
                <Building2 className="w-3.5 h-3.5 text-[#FFD91A]" />
                Merchant
              </span>
              <p className="font-bold text-white text-sm truncate">{activeProposal.merchant_name}</p>
              <span className="text-[10px] text-[#737373]">ID: {activeProposal.merchant_id}</span>
            </div>

            <div className="p-3.5 rounded-2xl bg-[#0E0E0E] border border-[#242424] space-y-1">
              <span className="text-[10px] text-[#A3A3A3] uppercase font-bold flex items-center gap-1">
                <CreditCard className="w-3.5 h-3.5 text-[#D0F030]" />
                Total Price
              </span>
              <p className="font-black text-[#D0F030] text-lg">
                ${(activeProposal.total ?? 0).toFixed(2)} {activeProposal.currency}
              </p>
              <span className="text-[10px] text-[#A3A3A3]">
                Subtotal: ${(activeProposal.subtotal ?? 0).toFixed(2)} + Tax: ${(activeProposal.tax ?? 0).toFixed(2)}
              </span>
            </div>

            <div className="p-3.5 rounded-2xl bg-[#0E0E0E] border border-[#242424] space-y-1">
              <span className="text-[10px] text-[#A3A3A3] uppercase font-bold flex items-center gap-1">
                <Calendar className="w-3.5 h-3.5 text-[#F04400]" />
                Created At
              </span>
              <p className="text-white text-xs font-bold">
                {new Date(activeProposal.created_at).toLocaleDateString()}
              </p>
              <span className="text-[10px] text-[#A3A3A3]">
                {new Date(activeProposal.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
            </div>
          </div>

          {/* Required Authorization Section */}
          <div className="p-4 rounded-2xl bg-[#0E0E0E] border border-[#242424] space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-black uppercase text-[#FFD91A] flex items-center gap-1.5">
                <UserCheck className="w-4 h-4 text-[#FFD91A]" />
                Required Authorization Policy
              </span>
              <span className="text-[10px] px-2 py-0.5 rounded bg-black text-[#D0F030] font-black uppercase border border-[#242424]">
                {isGuardianRequired ? 'GUARDIAN OVERSIGHT' : 'SOVEREIGN DIRECT'}
              </span>
            </div>
            <p className="text-[#D4D4D4] text-xs leading-relaxed">
              {isGuardianRequired ? (
                <>
                  This purchase is staged under an Assisted Care profile. In accordance with policy,{' '}
                  <strong className="text-white">an authorized financial guardian</strong> must verify and grant consent before payment can be dispatched.
                </>
              ) : (
                <>
                  This purchase was prepared by AI within your configured budget policy. In accordance with Mode A governance,{' '}
                  <strong className="text-white">your explicit human authorization</strong> is required to sign the transaction fingerprint.
                </>
              )}
            </p>
          </div>

          {/* Line Items Table */}
          <div className="space-y-2">
            <span className="text-[11px] font-black uppercase text-[#A3A3A3] tracking-wider">
              Itemized Line Items ({activeProposal.items?.length || 0})
            </span>
            <div className="rounded-2xl bg-[#0E0E0E] border border-[#242424] overflow-hidden">
              <table className="w-full text-left">
                <thead className="bg-black/60 text-[10px] text-[#A3A3A3] uppercase border-b border-[#242424]">
                  <tr>
                    <th className="py-2.5 px-4">Item Name</th>
                    <th className="py-2.5 px-3">Qty</th>
                    <th className="py-2.5 px-3">Unit Price</th>
                    <th className="py-2.5 px-4 text-right">Total</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#242424]">
                  {activeProposal.items && activeProposal.items.length > 0 ? (
                    activeProposal.items.map((it, idx) => (
                      <tr key={it.id || idx} className="hover:bg-[#171717]/60">
                        <td className="py-2.5 px-4 text-white font-bold">{it.name}</td>
                        <td className="py-2.5 px-3 text-[#A3A3A3]">{it.quantity}</td>
                        <td className="py-2.5 px-3 text-[#D4D4D4]">${it.unit_price.toFixed(2)}</td>
                        <td className="py-2.5 px-4 text-right text-[#D0F030] font-bold">
                          ${it.total.toFixed(2)}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={4} className="py-3 px-4 text-center text-[#737373]">
                        No individual line items detailed.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Cryptographic Deterministic Fingerprint */}
          <div className="p-4 rounded-2xl bg-[#0E0E0E] border border-[#242424] space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-black uppercase text-[#D0F030] flex items-center gap-1.5">
                <ShieldCheck className="w-4 h-4 text-[#D0F030]" />
                Cryptographic Fingerprint Binding
              </span>
              <span className="text-[10px] text-[#A3A3A3]">SHA-256 CANONICAL HASH</span>
            </div>
            <p className="text-[11px] text-[#A3A3A3] leading-relaxed">
              This proposal payload is cryptographically sealed. Any alteration to price, merchant, items, or quantities produces a hash mismatch and terminates payment authorization.
            </p>
            <div className="pt-1">
              <FingerprintPill hash={activeProposal.fingerprint_sha256} allowExpand={true} />
            </div>
          </div>

          {/* AI Rationale & Policy Verdict */}
          <div className="p-4 rounded-2xl bg-[#0E0E0E] border border-[#242424] space-y-2">
            <span className="text-[11px] font-black uppercase text-[#FFD91A]">
              Trust Engine &amp; AI Analysis
            </span>
            <p className="text-[#D4D4D4] text-xs leading-relaxed">
              <strong className="text-white">AI Reasoning:</strong> {activeProposal.ai_reasoning}
            </p>
            {activeProposal.policy_decision && (
              <div className="pt-2 flex items-center gap-2 border-t border-[#242424] text-[11px]">
                <span className="text-[#A3A3A3]">Policy Decision:</span>
                <span className={`font-black uppercase ${
                  activeProposal.policy_decision === 'ALLOW'
                    ? 'text-[#D0F030]'
                    : activeProposal.policy_decision === 'REQUIRE_APPROVAL'
                    ? 'text-[#FFD91A]'
                    : 'text-[#F04400]'
                }`}>
                  {activeProposal.policy_decision}
                </span>
                {activeProposal.policy_notes && (
                  <span className="text-[#A3A3A3] ml-2">({activeProposal.policy_notes})</span>
                )}
              </div>
            )}
          </div>

          {/* Confirmation Dialogs for Approval & Rejection */}
          {showApprovalConfirm && (
            <div className="p-4 rounded-2xl bg-[#1A180E] border border-[#FFD91A] space-y-3 animate-in fade-in duration-150">
              <div className="flex items-center gap-2 text-[#FFD91A] font-bold text-xs uppercase">
                <Lock className="w-4 h-4" />
                <span>Confirm {isGuardianRequired ? 'Guardian Delegated' : 'Direct Human'} Authorization</span>
              </div>
              <p className="text-xs text-[#D4D4D4] leading-relaxed">
                By approving this proposal, you authorize payment of <strong className="text-white">${(activeProposal.total ?? 0).toFixed(2)} {activeProposal.currency}</strong> to <strong className="text-white">{activeProposal.merchant_name}</strong>. The Trust Engine will record an immutable authorization token bound to this exact hash.
              </p>
              <div className="flex items-center gap-3 pt-1">
                <button
                  onClick={handleConfirmApproval}
                  disabled={isApproving}
                  className="px-4 py-2 rounded-xl bg-[#D0F030] hover:bg-[#b8da20] text-black font-black text-xs uppercase tracking-wider flex items-center gap-2 transition disabled:opacity-50"
                >
                  {isApproving ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin text-black" />
                      <span>Signing Authorization...</span>
                    </>
                  ) : (
                    <>
                      <CheckCircle2 className="w-4 h-4" />
                      <span>Authorize Proposal</span>
                    </>
                  )}
                </button>
                <button
                  onClick={() => setShowApprovalConfirm(false)}
                  disabled={isApproving}
                  className="px-4 py-2 rounded-xl bg-[#242424] hover:bg-[#333333] text-white text-xs font-bold uppercase transition"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {showRejectPrompt && (
            <div className="p-4 rounded-2xl bg-[#2A0E08] border border-[#F04400] space-y-3 animate-in fade-in duration-150">
              <div className="flex items-center gap-2 text-[#F04400] font-bold text-xs uppercase">
                <XCircle className="w-4 h-4" />
                <span>Reject &amp; Terminate Proposal</span>
              </div>
              <p className="text-xs text-[#D4D4D4] leading-relaxed">
                Rejecting this proposal transitions it to a terminal state. It cannot be approved or paid later.
              </p>
              <input
                type="text"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Reason for rejection (e.g. Unauthorized price, item not needed)"
                className="w-full px-3.5 py-2 rounded-xl bg-[#0E0E0E] border border-[#242424] text-white text-xs focus:outline-none focus:border-[#F04400]"
              />
              <div className="flex items-center gap-3 pt-1">
                <button
                  onClick={handleConfirmRejection}
                  disabled={isRejecting}
                  className="px-4 py-2 rounded-xl bg-[#F04400] hover:bg-[#ff5714] text-black font-black text-xs uppercase tracking-wider flex items-center gap-2 transition disabled:opacity-50"
                >
                  {isRejecting ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin text-black" />
                      <span>Rejecting...</span>
                    </>
                  ) : (
                    <>
                      <XCircle className="w-4 h-4" />
                      <span>Confirm Rejection</span>
                    </>
                  )}
                </button>
                <button
                  onClick={() => setShowRejectPrompt(false)}
                  disabled={isRejecting}
                  className="px-4 py-2 rounded-xl bg-[#242424] hover:bg-[#333333] text-white text-xs font-bold uppercase transition"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div className="px-6 py-4 bg-[#0E0E0E] border-t border-[#242424] flex flex-wrap items-center justify-between gap-3 shrink-0">
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-[#A3A3A3]">Current Status:</span>
            <StatusBadge state={activeProposal.state} />
          </div>

          <div className="flex items-center gap-2.5">
            {/* If Awaiting Approval: show Approve and Reject */}
            {isAwaiting && !isPolicyDenied && !showApprovalConfirm && !showRejectPrompt && (
              <>
                <button
                  onClick={() => setShowRejectPrompt(true)}
                  className="px-4 py-2 rounded-xl bg-[#2A0E08] hover:bg-[#3D140C] text-[#F04400] border border-[#F04400] text-xs font-black uppercase transition flex items-center gap-1.5"
                >
                  <XCircle className="w-4 h-4" />
                  <span>Reject</span>
                </button>
                <button
                  onClick={() => setShowApprovalConfirm(true)}
                  className="px-5 py-2 rounded-xl bg-[#D0F030] hover:bg-[#b8da20] text-black font-black text-xs uppercase tracking-wider transition flex items-center gap-2 shadow-md"
                >
                  <CheckCircle2 className="w-4 h-4" />
                  <span>{isGuardianRequired ? 'Grant Guardian Consent' : 'Approve Proposal'}</span>
                </button>
              </>
            )}

            {/* If Approved: show Proceed to PayPal Sandbox */}
            {isApproved && onProceedToCheckout && (
              <button
                onClick={() => {
                  onClose();
                  onProceedToCheckout(activeProposal);
                }}
                className="px-5 py-2 rounded-xl bg-[#FFD91A] hover:bg-[#ffe040] text-black font-black text-xs uppercase tracking-wider transition flex items-center gap-2 shadow-lg"
              >
                <CreditCard className="w-4 h-4 text-black stroke-[2.5]" />
                <span>Proceed to PayPal Sandbox</span>
              </button>
            )}

            {/* If Payment Pending: show Resume Checkout */}
            {isPaymentPending && onProceedToCheckout && (
              <button
                onClick={() => {
                  onClose();
                  onProceedToCheckout(activeProposal);
                }}
                className="px-5 py-2 rounded-xl bg-[#F04400] hover:bg-[#ff5714] text-black font-black text-xs uppercase tracking-wider transition flex items-center gap-2 shadow-lg"
              >
                <CreditCard className="w-4 h-4 text-black" />
                <span>Resume Sandbox Capture</span>
              </button>
            )}

            {/* Policy Denied */}
            {isPolicyDenied && (
              <span className="px-3.5 py-1.5 rounded-xl bg-[#2A0E08] text-[#F04400] border border-[#F04400] text-xs font-bold uppercase flex items-center gap-1.5">
                <ShieldAlert className="w-4 h-4" />
                Policy Engine DENY — Approval Blocked
              </span>
            )}

            {isPaid && (
              <span className="px-3.5 py-1.5 rounded-xl bg-[#0E0E0E] text-[#D0F030] border border-[#D0F030] text-xs font-bold uppercase flex items-center gap-1.5">
                <ShieldCheck className="w-4 h-4" />
                Payment Captured &amp; Verified
              </span>
            )}

            <button
              onClick={onClose}
              className="px-4 py-2 rounded-xl bg-[#242424] hover:bg-[#333333] text-white text-xs font-bold uppercase transition"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ProposalReviewModal;
