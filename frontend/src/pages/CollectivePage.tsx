import React, { useState, useEffect, useCallback } from 'react';
import {
  Users,
  Target,
  CheckCircle2,
  Clock,
  Copy,
  ShieldCheck,
  Receipt,
  Plus,
  Loader2,
  AlertCircle,
  Sparkles,
  Check,
  RefreshCw,
} from 'lucide-react';
import { StatusBadge } from '../components/common/StatusBadge';
import { MetricCard } from '../components/common/MetricCard';
import { Modal } from '../components/common/Modal';
import { MeridianLogo } from '../components/common/MeridianLogo';
import {
  fetchGroups,
  createGroup,
  joinGroup,
  evaluateGroupPolicy,
} from '../services/api';
import type { CollectiveGroup } from '../types';

export const CollectivePage: React.FC = () => {
  const [groups, setGroups] = useState<CollectiveGroup[]>([]);
  const [selectedGroup, setSelectedGroup] = useState<CollectiveGroup | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [copiedLink, setCopiedLink] = useState(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Modal States
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isJoinModalOpen, setIsJoinModalOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [modalError, setModalError] = useState<string | null>(null);

  // Create Form State
  const [createTitle, setCreateTitle] = useState('');
  const [createTargetItem, setCreateTargetItem] = useState('');
  const [createTargetAmount, setCreateTargetAmount] = useState('500.00');
  const [createDeadlineDays, setCreateDeadlineDays] = useState('14');
  const [createOrganizerShare, setCreateOrganizerShare] = useState('125.00');

  // Join Form State
  const [joinName, setJoinName] = useState('');
  const [joinEmail, setJoinEmail] = useState('');
  const [joinShareAmount, setJoinShareAmount] = useState('125.00');

  // Policy Evaluation State
  const [isEvaluatingPolicy, setIsEvaluatingPolicy] = useState(false);
  const [policyResult, setPolicyResult] = useState<any>(null);

  const loadGroupsData = useCallback(async (preserveSelectedId?: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const fetched = await fetchGroups();
      setGroups(fetched);
      if (fetched.length > 0) {
        if (preserveSelectedId) {
          const match = fetched.find((g) => g.id === preserveSelectedId);
          setSelectedGroup(match || fetched[0]);
        } else {
          setSelectedGroup((prev) => (prev ? fetched.find((g) => g.id === prev.id) || fetched[0] : fetched[0]));
        }
      } else {
        setSelectedGroup(null);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to connect to COMMON API.');
      setGroups([]);
      setSelectedGroup(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadGroupsData();
  }, [loadGroupsData]);

  const handleCopyInvite = () => {
    if (!selectedGroup) return;
    navigator.clipboard.writeText(`https://meridian.demo/collective/join/${selectedGroup.id}`);
    setCopiedLink(true);
    setTimeout(() => setCopiedLink(false), 2000);
  };

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setModalError(null);
    const targetCents = Math.round(parseFloat(createTargetAmount) * 100);
    const organizerShareCents = Math.round(parseFloat(createOrganizerShare || '0') * 100);

    if (isNaN(targetCents) || targetCents <= 0) {
      setModalError('Target goal amount must be a positive number.');
      return;
    }
    if (isNaN(organizerShareCents) || organizerShareCents < 0) {
      setModalError('Organizer share must be a valid non-negative number.');
      return;
    }
    if (organizerShareCents > targetCents) {
      setModalError('Organizer share cannot exceed total target amount.');
      return;
    }

    setIsSubmitting(true);
    try {
      const newGroup = await createGroup({
        title: createTitle.trim(),
        target_item: createTargetItem.trim(),
        total_target_cents: targetCents,
        deadline_days: parseInt(createDeadlineDays, 10) || 7,
        organizer_name: 'Alex Rivera (Organizer)',
        organizer_email: 'alex.rivera@demo.meridian',
        organizer_share_cents: organizerShareCents,
      });

      setSuccessMsg(`Campaign "${newGroup.title}" created successfully!`);
      setTimeout(() => setSuccessMsg(null), 4000);
      setIsCreateModalOpen(false);
      setCreateTitle('');
      setCreateTargetItem('');
      await loadGroupsData(newGroup.id);
    } catch (err: any) {
      setModalError(err.message || 'Failed to create group campaign.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleJoinSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedGroup) return;
    setModalError(null);
    const shareCents = Math.round(parseFloat(joinShareAmount) * 100);

    if (!joinName.trim() || !joinEmail.trim()) {
      setModalError('Name and email are required.');
      return;
    }
    if (isNaN(shareCents) || shareCents <= 0) {
      setModalError('Pledged share must be greater than zero.');
      return;
    }

    setIsSubmitting(true);
    try {
      const updated = await joinGroup(selectedGroup.id, {
        name: joinName.trim(),
        email: joinEmail.trim().toLowerCase(),
        share_cents: shareCents,
      });

      setSuccessMsg(`Successfully joined campaign with $${(shareCents / 100).toFixed(2)} commitment!`);
      setTimeout(() => setSuccessMsg(null), 4000);
      setIsJoinModalOpen(false);
      setJoinName('');
      setJoinEmail('');
      await loadGroupsData(updated.id);
    } catch (err: any) {
      setModalError(err.message || 'Failed to join group.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleEvaluatePolicy = async () => {
    if (!selectedGroup) return;
    setIsEvaluatingPolicy(true);
    setPolicyResult(null);
    try {
      const result = await evaluateGroupPolicy(selectedGroup.id);
      setPolicyResult(result);
    } catch (err: any) {
      setPolicyResult({
        decision: 'DENY',
        human_explanation: err.message || 'Policy evaluation failed.',
        passed_checks: [],
        failed_checks: ['POLICY_ERROR'],
      });
    } finally {
      setIsEvaluatingPolicy(false);
    }
  };

  // Authoritative calculations
  const totalTarget = selectedGroup?.total_target ?? 0;
  const committedTotal = selectedGroup?.total_committed ?? (selectedGroup?.amount_collected ?? 0);
  const remainingTotal = selectedGroup?.remaining_amount ?? Math.max(0, totalTarget - (selectedGroup?.amount_collected ?? 0));
  const percentage = totalTarget > 0 ? Math.min(100, Math.round((committedTotal / totalTarget) * 100)) : 0;
  const participants = selectedGroup?.participants || [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 p-6 sm:p-7 rounded-3xl bg-[#171717] border border-[#242424] shadow-xl">
        <div className="space-y-1">
          <div className="flex items-center gap-2.5">
            <MeridianLogo variant="icon" size="xs" />
            <span className="px-2.5 py-0.5 rounded text-[10px] font-mono font-black bg-[#FFD91A] text-black uppercase tracking-widest">
              COLLECTIVE MODULE
            </span>
            <span className="text-xs font-mono text-[#D0F030] font-bold">COMMON GROUP COMMERCE</span>
          </div>
          <h2 className="text-xl sm:text-2xl font-black text-white font-mono uppercase tracking-tight">
            COMMON Commerce — Collective &amp; Shared Purchasing
          </h2>
          <p className="text-xs text-[#A3A3A3] max-w-2xl font-mono leading-relaxed">
            Coordinate multi-party purchases with full transparency. Tracks individual commitments without giving organizers blanket authority to debit participant wallets.
          </p>
        </div>
        <div className="flex items-center gap-2.5 shrink-0">
          {selectedGroup && (
            <button
              onClick={handleCopyInvite}
              className="px-3.5 py-2.5 rounded-xl bg-[#0E0E0E] hover:bg-[#242424] text-white border border-[#242424] text-xs font-mono transition flex items-center gap-2 shadow-xs font-bold uppercase"
            >
              {copiedLink ? <Check className="w-3.5 h-3.5 text-[#D0F030]" /> : <Copy className="w-3.5 h-3.5 text-[#A3A3A3]" />}
              <span>{copiedLink ? 'Link Copied!' : 'Copy Share Link'}</span>
            </button>
          )}
          <button
            onClick={() => {
              setModalError(null);
              setIsCreateModalOpen(true);
            }}
            className="px-5 py-2.5 rounded-xl bg-[#F04400] hover:bg-[#ff5714] text-black text-xs font-mono transition flex items-center gap-2 shadow-lg shadow-[#F04400]/20 font-black uppercase tracking-wider"
          >
            <Plus className="w-4 h-4 stroke-[3]" />
            <span>New Campaign</span>
          </button>
        </div>
      </div>

      {/* Success Notification Banner */}
      {successMsg && (
        <div className="p-3.5 rounded-2xl bg-[#0E0E0E] border border-[#D0F030] text-[#D0F030] text-xs font-mono flex items-center gap-2 font-bold uppercase">
          <Check className="w-4 h-4 shrink-0 text-[#D0F030]" />
          <span>{successMsg}</span>
        </div>
      )}

      {/* Error State Banner */}
      {error && (
        <div className="p-4 rounded-2xl bg-[#2A0E08] border border-[#F04400] text-[#F04400] space-y-3 font-mono">
          <div className="flex items-center gap-2 font-bold text-xs uppercase">
            <AlertCircle className="w-4 h-4 text-[#F04400]" />
            <span>API Communication Error</span>
          </div>
          <p className="text-xs">{error}</p>
          <button
            onClick={() => loadGroupsData()}
            className="px-4 py-2 rounded-xl bg-[#F04400] text-black text-xs font-black uppercase transition flex items-center gap-2"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Retry Connection</span>
          </button>
        </div>
      )}

      {/* Loading Spinner */}
      {isLoading && (
        <div className="p-12 text-center rounded-3xl bg-[#171717] border border-[#242424] text-[#A3A3A3] font-mono text-xs flex flex-col items-center justify-center gap-3">
          <Loader2 className="w-6 h-6 animate-spin text-[#F04400]" />
          <span>Loading collective purchasing campaigns from backend...</span>
        </div>
      )}

      {/* Empty State */}
      {!isLoading && !error && groups.length === 0 && (
        <div className="p-12 text-center rounded-3xl bg-[#171717] border border-[#242424] space-y-4">
          <div className="w-12 h-12 rounded-2xl bg-[#0E0E0E] flex items-center justify-center mx-auto text-[#A3A3A3]">
            <Users className="w-6 h-6 text-[#FFD91A]" />
          </div>
          <div className="space-y-1 font-mono">
            <h3 className="text-sm font-black text-white uppercase">No Group Campaigns Found</h3>
            <p className="text-xs text-[#A3A3A3] max-w-md mx-auto">
              Start a shared equipment purchase or collective order pool with friends, colleagues, or co-workers.
            </p>
          </div>
          <button
            onClick={() => setIsCreateModalOpen(true)}
            className="px-5 py-2.5 rounded-xl bg-[#F04400] hover:bg-[#ff5714] text-black text-xs font-mono font-black uppercase tracking-wider transition inline-flex items-center gap-2 shadow-lg"
          >
            <Plus className="w-4 h-4 stroke-[3]" />
            <span>Create First Campaign</span>
          </button>
        </div>
      )}

      {/* Active Group Content */}
      {!isLoading && !error && selectedGroup && (
        <>
          {/* Campaign Selector Tabs */}
          {groups.length > 1 && (
            <div className="flex items-center gap-2 overflow-x-auto pb-1">
              <span className="text-[11px] font-mono uppercase font-black text-[#FFD91A] shrink-0">Campaigns:</span>
              {groups.map((g) => (
                <button
                  key={g.id}
                  onClick={() => {
                    setSelectedGroup(g);
                    setPolicyResult(null);
                  }}
                  className={`px-3.5 py-1.5 rounded-xl text-xs font-mono transition shrink-0 uppercase font-black ${
                    selectedGroup.id === g.id
                      ? 'bg-[#F04400] text-black shadow-md'
                      : 'bg-[#171717] text-[#D4D4D4] hover:text-white border border-[#242424]'
                  }`}
                >
                  {g.title}
                </button>
              ))}
            </div>
          )}

          {/* Metrics Row */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <MetricCard
              title="Target Goal"
              value={`$${totalTarget.toFixed(2)}`}
              subtitle={selectedGroup.currency}
              icon={Target}
              accent="orange"
            />
            <MetricCard
              title="Pledged / Committed"
              value={`$${committedTotal.toFixed(2)}`}
              subtitle={`${percentage}% of target`}
              changeType="positive"
              change={`$${selectedGroup.amount_collected.toFixed(2)} verified paid`}
              icon={CheckCircle2}
              accent="lime"
            />
            <MetricCard
              title="Remaining to Fund"
              value={`$${remainingTotal.toFixed(2)}`}
              subtitle={`${participants.length} participants joined`}
              changeType={remainingTotal > 0 ? 'negative' : 'positive'}
              change={remainingTotal > 0 ? `Target: $${totalTarget.toFixed(2)}` : 'Goal Reached!'}
              icon={Clock}
              accent="yellow"
            />
            <MetricCard
              title="Split Allocation"
              value={participants.length > 0 ? `${participants.length} Shares` : 'Open'}
              subtitle={
                participants.length > 0
                  ? `Avg $${(totalTarget / participants.length).toFixed(2)} / participant`
                  : 'Awaiting participants'
              }
              icon={Receipt}
              accent="default"
            />
          </div>

          {/* Main Campaign Card */}
          <div className="rounded-3xl bg-[#171717] border border-[#242424] p-6 sm:p-7 shadow-xl space-y-6">
            <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
              <div>
                <div className="flex items-center gap-2">
                  <span
                    className={`px-2.5 py-0.5 rounded text-[10px] font-mono uppercase font-black ${
                      selectedGroup.status === 'FUNDED'
                        ? 'bg-[#D0F030] text-black'
                        : selectedGroup.status === 'CANCELLED'
                        ? 'bg-[#2A0E08] text-[#F04400] border border-[#F04400]'
                        : 'bg-[#FFD91A] text-black'
                    }`}
                  >
                    CAMPAIGN {selectedGroup.status}
                  </span>
                  <span className="text-xs font-mono text-[#A3A3A3]">ID: {selectedGroup.id}</span>
                </div>
                <h3 className="text-xl font-black text-white font-mono uppercase mt-1 tracking-tight">{selectedGroup.title}</h3>
                <p className="text-xs text-[#D4D4D4] font-mono mt-0.5">{selectedGroup.description}</p>
              </div>

              <div className="text-right">
                <span className="text-3xl font-black font-mono text-[#D0F030]">
                  ${committedTotal.toFixed(2)}
                </span>
                <span className="text-[#A3A3A3] font-mono text-sm"> / ${totalTarget.toFixed(2)}</span>
                <p className="text-[11px] font-mono text-[#FFD91A] font-black uppercase mt-0.5">{percentage}% OF GOAL REACHED</p>
              </div>
            </div>

            {/* Progress Bar */}
            <div className="space-y-1.5 font-mono">
              <div className="w-full h-3.5 rounded-full bg-[#0E0E0E] border border-[#242424] overflow-hidden p-0.5">
                <div
                  className="h-full bg-gradient-to-r from-[#F04400] to-[#FFD91A] rounded-full transition-all duration-500"
                  style={{ width: `${percentage}%` }}
                />
              </div>
              <div className="flex justify-between text-[11px] text-[#A3A3A3]">
                <span>Target Item: <strong className="text-white">{selectedGroup.target_item}</strong></span>
                <span>Deadline: <strong className="text-[#FFD91A]">{selectedGroup.deadline}</strong></span>
              </div>
            </div>

            {/* Action Bar */}
            <div className="flex flex-wrap items-center gap-3 pt-2">
              <button
                onClick={() => {
                  setModalError(null);
                  setIsJoinModalOpen(true);
                }}
                disabled={selectedGroup.status !== 'OPEN'}
                className={`px-5 py-2.5 rounded-xl text-xs font-mono font-black uppercase tracking-wider transition flex items-center gap-2 ${
                  selectedGroup.status === 'OPEN'
                    ? 'bg-[#FFD91A] hover:bg-[#ffe040] text-black shadow-md'
                    : 'bg-[#242424] text-[#737373] cursor-not-allowed'
                }`}
              >
                <Plus className="w-4 h-4 stroke-[3]" />
                <span>Pledge Share / Join</span>
              </button>

              <button
                onClick={handleEvaluatePolicy}
                disabled={isEvaluatingPolicy}
                className="px-4 py-2.5 rounded-xl bg-[#242424] hover:bg-[#333333] text-white border border-[#333333] text-xs font-mono transition flex items-center gap-2 shadow-xs font-bold uppercase"
              >
                {isEvaluatingPolicy ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-[#F04400]" />
                ) : (
                  <Sparkles className="w-3.5 h-3.5 text-[#D0F030]" />
                )}
                <span>Verify Policy (pol-004)</span>
              </button>
            </div>

            {/* Policy Evaluation Banner */}
            {policyResult && (
              <div
                className={`p-4 rounded-2xl border text-xs font-mono space-y-2 animate-fadeIn ${
                  policyResult.decision === 'PERMIT'
                    ? 'bg-[#0E0E0E] border-[#D0F030] text-[#D0F030]'
                    : policyResult.decision === 'REQUIRE_APPROVAL'
                    ? 'bg-[#1A180E] border-[#FFD91A] text-[#FFD91A]'
                    : 'bg-[#2A0E08] border-[#F04400] text-[#F04400]'
                }`}
              >
                <div className="flex items-center justify-between font-black uppercase">
                  <div className="flex items-center gap-2">
                    <ShieldCheck className="w-4 h-4" />
                    <span>Trust Engine Decision: {policyResult.decision}</span>
                  </div>
                  <span className="text-[10px] uppercase px-2 py-0.5 rounded bg-black border border-[#242424] font-bold text-white">
                    Scope: COLLECTIVE (pol-004)
                  </span>
                </div>
                <p className="text-[#D4D4D4] font-sans">{policyResult.human_explanation}</p>
                {policyResult.passed_checks?.length > 0 && (
                  <div className="text-[10px] text-[#A3A3A3]">
                    Passed: {policyResult.passed_checks.join(', ')}
                  </div>
                )}
              </div>
            )}

            {/* Participants Table */}
            <div className="space-y-3 pt-4 border-t border-[#242424]">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-mono font-black uppercase tracking-widest text-[#FFD91A]">
                  Participants &amp; Allocation ({participants.length})
                </h4>
                <span className="text-xs font-mono text-[#A3A3A3]">
                  {participants.length} registered participant{participants.length !== 1 ? 's' : ''}
                </span>
              </div>

              {participants.length === 0 ? (
                <div className="p-6 text-center rounded-2xl bg-[#0E0E0E] border border-[#242424] text-[#A3A3A3] font-mono text-xs">
                  No participants have joined yet. Be the first to pledge a contribution!
                </div>
              ) : (
                <div className="divide-y divide-[#242424] rounded-2xl bg-[#0E0E0E] border border-[#242424] overflow-hidden">
                  {participants.map((p) => (
                    <div
                      key={p.id}
                      className="flex items-center justify-between p-4 text-xs font-mono hover:bg-[#171717] transition"
                    >
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-xl bg-[#F04400] text-black font-black flex items-center justify-center text-xs">
                          {p.name.slice(0, 2).toUpperCase()}
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="font-bold text-white">{p.name}</span>
                            {p.is_organizer && (
                              <span className="px-1.5 py-0.2 rounded text-[9px] bg-[#FFD91A] text-black font-black uppercase">
                                ORGANIZER
                              </span>
                            )}
                          </div>
                          <span className="text-[10px] text-[#A3A3A3]">{p.email}</span>
                        </div>
                      </div>

                      <div className="flex items-center gap-4">
                        <span className="font-black text-white">${p.share_amount.toFixed(2)}</span>
                        <StatusBadge
                          severity={p.status === 'PAID' ? 'SUCCESS' : p.status === 'COMMITTED' ? 'INFO' : 'WARNING'}
                          text={p.status}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Collective Trust Notice */}
            <div className="p-4 rounded-2xl bg-[#0E0E0E] border border-[#FFD91A]/50 text-xs space-y-2">
              <div className="flex items-center gap-2 text-[#FFD91A] font-mono font-black uppercase">
                <ShieldCheck className="w-4 h-4 text-[#FFD91A]" />
                <span>MERIDIAN Group Trust Invariant:</span>
              </div>
              <p className="text-[#D4D4D4] leading-relaxed font-mono text-xs">
                Group invitations do not grant organizer debit consent. Each member explicitly authorizes their contribution. Once the target threshold is met and all commitments are verified, the Trust Engine binds the SHA-256 fingerprint and creates a single consolidated PayPal Sandbox Order for supplier checkout.
              </p>
            </div>
          </div>
        </>
      )}

      {/* Modal: Create Campaign */}
      <Modal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        title="Create Collective Group Campaign"
      >
        <form onSubmit={handleCreateSubmit} className="space-y-4 text-xs font-mono">
          {modalError && (
            <div className="p-3 rounded-xl bg-[#2A0E08] border border-[#F04400] text-[#F04400] text-[11px]">
              {modalError}
            </div>
          )}

          <div>
            <label className="block text-white font-black uppercase mb-1">Campaign Title</label>
            <input
              type="text"
              required
              placeholder="e.g. Studio Podcast Equipment Pool"
              value={createTitle}
              onChange={(e) => setCreateTitle(e.target.value)}
              className="w-full px-3 py-2.5 rounded-xl bg-[#0E0E0E] border border-[#242424] text-white focus:outline-none focus:border-[#F04400]"
            />
          </div>

          <div>
            <label className="block text-white font-black uppercase mb-1">Target Product / Item</label>
            <input
              type="text"
              required
              placeholder="e.g. Sony ZV-E10 II Camera + Mic Rig"
              value={createTargetItem}
              onChange={(e) => setCreateTargetItem(e.target.value)}
              className="w-full px-3 py-2.5 rounded-xl bg-[#0E0E0E] border border-[#242424] text-white focus:outline-none focus:border-[#F04400]"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-white font-black uppercase mb-1">Total Target ($)</label>
              <input
                type="number"
                step="0.01"
                required
                value={createTargetAmount}
                onChange={(e) => setCreateTargetAmount(e.target.value)}
                className="w-full px-3 py-2.5 rounded-xl bg-[#0E0E0E] border border-[#242424] text-white focus:outline-none focus:border-[#F04400]"
              />
            </div>
            <div>
              <label className="block text-white font-black uppercase mb-1">Organizer Share ($)</label>
              <input
                type="number"
                step="0.01"
                value={createOrganizerShare}
                onChange={(e) => setCreateOrganizerShare(e.target.value)}
                className="w-full px-3 py-2.5 rounded-xl bg-[#0E0E0E] border border-[#242424] text-white focus:outline-none focus:border-[#F04400]"
              />
            </div>
          </div>

          <div>
            <label className="block text-white font-black uppercase mb-1">Campaign Duration (Days)</label>
            <input
              type="number"
              min="1"
              max="90"
              required
              value={createDeadlineDays}
              onChange={(e) => setCreateDeadlineDays(e.target.value)}
              className="w-full px-3 py-2.5 rounded-xl bg-[#0E0E0E] border border-[#242424] text-white focus:outline-none focus:border-[#F04400]"
            />
          </div>

          <div className="pt-3 flex justify-end gap-2.5 border-t border-[#242424]">
            <button
              type="button"
              onClick={() => setIsCreateModalOpen(false)}
              className="px-4 py-2 rounded-xl bg-[#242424] hover:bg-[#333333] text-white transition uppercase font-bold"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="px-5 py-2 rounded-xl bg-[#F04400] hover:bg-[#ff5714] text-black font-black uppercase transition flex items-center gap-2 shadow-lg"
            >
              {isSubmitting && <Loader2 className="w-3.5 h-3.5 animate-spin text-black" />}
              <span>{isSubmitting ? 'Creating...' : 'Create Campaign'}</span>
            </button>
          </div>
        </form>
      </Modal>

      {/* Modal: Join / Pledge Share */}
      <Modal
        isOpen={isJoinModalOpen}
        onClose={() => setIsJoinModalOpen(false)}
        title={`Join Campaign: ${selectedGroup?.title || ''}`}
      >
        <form onSubmit={handleJoinSubmit} className="space-y-4 text-xs font-mono">
          {modalError && (
            <div className="p-3 rounded-xl bg-[#2A0E08] border border-[#F04400] text-[#F04400] text-[11px]">
              {modalError}
            </div>
          )}

          <div>
            <label className="block text-white font-black uppercase mb-1">Your Full Name</label>
            <input
              type="text"
              required
              placeholder="e.g. Sarah Jenkins"
              value={joinName}
              onChange={(e) => setJoinName(e.target.value)}
              className="w-full px-3 py-2.5 rounded-xl bg-[#0E0E0E] border border-[#242424] text-white focus:outline-none focus:border-[#F04400]"
            />
          </div>

          <div>
            <label className="block text-white font-black uppercase mb-1">Your Email</label>
            <input
              type="email"
              required
              placeholder="e.g. sarah@example.com"
              value={joinEmail}
              onChange={(e) => setJoinEmail(e.target.value)}
              className="w-full px-3 py-2.5 rounded-xl bg-[#0E0E0E] border border-[#242424] text-white focus:outline-none focus:border-[#F04400]"
            />
          </div>

          <div>
            <label className="block text-white font-black uppercase mb-1">Your Pledged Contribution ($)</label>
            <input
              type="number"
              step="0.01"
              required
              value={joinShareAmount}
              onChange={(e) => setJoinShareAmount(e.target.value)}
              className="w-full px-3 py-2.5 rounded-xl bg-[#0E0E0E] border border-[#242424] text-white focus:outline-none focus:border-[#F04400]"
            />
            <p className="text-[10px] text-[#A3A3A3] mt-1 font-mono">
              Note: Pledging records your verified commitment. Payment execution occurs through the PayPal Gateway when the target threshold is met.
            </p>
          </div>

          <div className="pt-3 flex justify-end gap-2.5 border-t border-[#242424]">
            <button
              type="button"
              onClick={() => setIsJoinModalOpen(false)}
              className="px-4 py-2 rounded-xl bg-[#242424] hover:bg-[#333333] text-white transition uppercase font-bold"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="px-5 py-2 rounded-xl bg-[#FFD91A] hover:bg-[#ffe040] text-black font-black uppercase transition flex items-center gap-2 shadow-lg"
            >
              {isSubmitting && <Loader2 className="w-3.5 h-3.5 animate-spin text-black" />}
              <span>{isSubmitting ? 'Joining...' : 'Confirm Pledge'}</span>
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
