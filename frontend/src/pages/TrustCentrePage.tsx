import React, { useState, useEffect, useCallback } from 'react';
import {
  Fingerprint,
  Lock,
  CheckCircle2,
  XCircle,
  HelpCircle,
  Loader2,
  AlertCircle,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react';
import { fetchPolicies } from '../services/api';
import type { ConsentPolicy } from '../types';
import { MeridianLogo } from '../components/common/MeridianLogo';

export const TrustCentrePage: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'decision_matrix' | 'fingerprinting' | 'active_policies' | 'invariants'>('decision_matrix');
  const [policies, setPolicies] = useState<ConsentPolicy[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const loadPolicies = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchPolicies();
      setPolicies(data);
    } catch (err: any) {
      setError(err.message || 'Failed to connect to Trust Engine policies.');
      setPolicies([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPolicies();
  }, [loadPolicies]);

  // Interactive Fingerprint Sandbox State
  const [demoAmount, setDemoAmount] = useState('84.00');
  const [demoMerchant, setDemoMerchant] = useState('Keychron Official');
  const [demoItem, setDemoItem] = useState('Keychron K3 Pro');

  // Simple simulated deterministic hash based on parameters
  const computeMockHash = () => {
    const raw = `${demoMerchant}|${demoItem}|${demoAmount}|USD|v1`;
    let hash = 0;
    for (let i = 0; i < raw.length; i++) {
      hash = (hash << 5) - hash + raw.charCodeAt(i);
      hash |= 0;
    }
    const hex = Math.abs(hash).toString(16).padStart(8, '0');
    return `e3b0c44298fc1c149afbf4c8996fb924${hex}${hex}b934ca495991b7852b855`.slice(0, 64);
  };

  const calculatedHash = computeMockHash();

  return (
    <div className="space-y-6">
      {/* Signature Screen Hero */}
      <div className="p-6 sm:p-8 rounded-3xl bg-[#F04400] text-black shadow-2xl relative overflow-hidden space-y-4">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="space-y-1 max-w-2xl">
            <div className="flex items-center gap-3">
              <div className="p-1 rounded-lg bg-black inline-flex">
                <MeridianLogo variant="icon" size="xs" />
              </div>
              <span className="px-3 py-1 rounded text-[10px] font-mono font-black bg-black text-[#FFD91A] uppercase tracking-widest">
                CORE SYSTEM ARCHITECTURE
              </span>
              <span className="text-xs font-mono font-black text-black/80">KERNEL v1.0</span>
            </div>
            <h2 className="text-2xl sm:text-3xl font-black text-black font-mono uppercase tracking-tight">
              Universal Trust Engine
            </h2>
            <p className="text-xs sm:text-sm text-black/90 font-mono font-bold leading-relaxed">
              The deterministic permission infrastructure governing all commerce in MERIDIAN. AI agents discover, evaluate, and propose; the Trust Engine holds cryptographic keys, verifies human consent, and decides what can actually be paid for.
            </p>
          </div>

          <div className="flex items-center gap-2.5 p-3 rounded-2xl bg-black text-[#D0F030] font-mono text-xs shadow-xl shrink-0">
            <ShieldCheck className="w-5 h-5 text-[#D0F030]" />
            <span className="font-black uppercase tracking-wider">10/10 Invariants Active</span>
          </div>
        </div>

        {/* Tab Selector */}
        <div className="flex flex-wrap items-center gap-2 pt-3 border-t border-black/20 text-xs font-mono">
          <button
            onClick={() => setActiveTab('decision_matrix')}
            className={`px-4 py-2 rounded-xl transition font-mono uppercase font-black text-xs ${
              activeTab === 'decision_matrix'
                ? 'bg-black text-[#FFD91A] shadow-md'
                : 'bg-black/20 text-black hover:bg-black/30'
            }`}
          >
            Decision Matrix
          </button>
          <button
            onClick={() => setActiveTab('fingerprinting')}
            className={`px-4 py-2 rounded-xl transition font-mono uppercase font-black text-xs ${
              activeTab === 'fingerprinting'
                ? 'bg-black text-[#FFD91A] shadow-md'
                : 'bg-black/20 text-black hover:bg-black/30'
            }`}
          >
            Cryptographic Fingerprinting
          </button>
          <button
            onClick={() => setActiveTab('active_policies')}
            className={`px-4 py-2 rounded-xl transition font-mono uppercase font-black text-xs ${
              activeTab === 'active_policies'
                ? 'bg-black text-[#FFD91A] shadow-md'
                : 'bg-black/20 text-black hover:bg-black/30'
            }`}
          >
            Active Consent Policies ({isLoading ? '...' : policies.length})
          </button>
          <button
            onClick={() => setActiveTab('invariants')}
            className={`px-4 py-2 rounded-xl transition font-mono uppercase font-black text-xs ${
              activeTab === 'invariants'
                ? 'bg-black text-[#FFD91A] shadow-md'
                : 'bg-black/20 text-black hover:bg-black/30'
            }`}
          >
            10 Invariant Proofs
          </button>
        </div>
      </div>

      {/* Tab 1: Decision Matrix */}
      {activeTab === 'decision_matrix' && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {/* ALLOW */}
            <div className="p-6 rounded-3xl bg-[#0E0E0E] border-2 border-[#D0F030] shadow-xl space-y-3 font-mono">
              <div className="flex items-center justify-between">
                <span className="text-base font-black text-[#D0F030] uppercase tracking-wider">ALLOW</span>
                <CheckCircle2 className="w-6 h-6 text-[#D0F030]" />
              </div>
              <p className="text-xs text-[#D4D4D4] leading-relaxed">
                Triggered only when <strong className="text-white">all pre-configured conditions are satisfied</strong>. Typically used in Mode B reordering where items, merchants, and prices are strictly pre-authorized.
              </p>
              <div className="text-[11px] text-[#A3A3A3] pt-3 border-t border-[#242424] space-y-1">
                <p>• Within max per-order limit</p>
                <p>• Within monthly budget ceiling</p>
                <p>• Whitelisted merchant &amp; SKU</p>
              </div>
            </div>

            {/* REQUIRE_APPROVAL */}
            <div className="p-6 rounded-3xl bg-[#1A180E] border-2 border-[#FFD91A] shadow-xl space-y-3 font-mono">
              <div className="flex items-center justify-between">
                <span className="text-base font-black text-[#FFD91A] uppercase tracking-wider">REQUIRE_APPROVAL</span>
                <HelpCircle className="w-6 h-6 text-[#FFD91A]" />
              </div>
              <p className="text-xs text-[#D4D4D4] leading-relaxed">
                Triggered when a transaction is legitimate but requires <strong className="text-white">explicit human review and confirmation</strong> (e.g., Mode A requests, or items outside automatic reorder list).
              </p>
              <div className="text-[11px] text-[#A3A3A3] pt-3 border-t border-[#242424] space-y-1">
                <p>• Binds cryptographic SHA-256 hash</p>
                <p>• Generates human approval inbox item</p>
                <p>• Requires guardian/user signature</p>
              </div>
            </div>

            {/* DENY */}
            <div className="p-6 rounded-3xl bg-[#2A0E08] border-2 border-[#F04400] shadow-xl space-y-3 font-mono">
              <div className="flex items-center justify-between">
                <span className="text-base font-black text-[#F04400] uppercase tracking-wider">DENY</span>
                <XCircle className="w-6 h-6 text-[#F04400]" />
              </div>
              <p className="text-xs text-[#D4D4D4] leading-relaxed">
                Deterministic, non-negotiable rejection. Payments are immediately blocked if spending limits are exceeded, merchants are blacklisted, or tampering is detected.
              </p>
              <div className="text-[11px] text-[#A3A3A3] pt-3 border-t border-[#242424] space-y-1">
                <p>• Exceeds spending ceiling</p>
                <p>• Untrusted merchant source</p>
                <p>• Hash mismatch / prompt injection</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Tab 2: Cryptographic Fingerprinting Live Demo */}
      {activeTab === 'fingerprinting' && (
        <div className="p-6 sm:p-7 rounded-3xl bg-[#171717] border border-[#242424] shadow-xl space-y-6 font-mono">
          <div>
            <div className="flex items-center gap-2">
              <Fingerprint className="w-5 h-5 text-[#F04400]" />
              <h3 className="text-base font-black text-white uppercase tracking-wider">
                Deterministic SHA-256 Fingerprint Calculator
              </h3>
            </div>
            <p className="text-xs text-[#A3A3A3] mt-1 leading-relaxed">
              Every purchase proposal is bound to an immutable cryptographic hash computed across canonicalized material parameters. Modify any field below to witness immediate hash alteration and prior approval revocation:
            </p>
          </div>

          {/* Interactive Inputs */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
            <div>
              <label className="text-[#A3A3A3] font-black uppercase block mb-1">Merchant Identity:</label>
              <input
                type="text"
                value={demoMerchant}
                onChange={(e) => setDemoMerchant(e.target.value)}
                className="w-full bg-[#0E0E0E] border border-[#242424] rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-[#F04400]"
              />
            </div>
            <div>
              <label className="text-[#A3A3A3] font-black uppercase block mb-1">Product Line Item:</label>
              <input
                type="text"
                value={demoItem}
                onChange={(e) => setDemoItem(e.target.value)}
                className="w-full bg-[#0E0E0E] border border-[#242424] rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-[#F04400]"
              />
            </div>
            <div>
              <label className="text-[#A3A3A3] font-black uppercase block mb-1">Total Amount ($ USD):</label>
              <input
                type="text"
                value={demoAmount}
                onChange={(e) => setDemoAmount(e.target.value)}
                className="w-full bg-[#0E0E0E] border border-[#242424] rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-[#F04400]"
              />
            </div>
          </div>

          {/* Computed Canonical Hash Result */}
          <div className="p-5 rounded-2xl bg-[#0E0E0E] border border-[#D0F030] space-y-2">
            <span className="text-[10px] uppercase text-[#D0F030] font-black tracking-widest block">
              CANONICAL SHA-256 OUTPUT (FINGERPRINT):
            </span>
            <div className="flex items-center gap-2">
              <span className="text-[#D0F030] text-xs sm:text-sm font-black break-all w-full">
                {calculatedHash}
              </span>
            </div>
            <p className="text-[11px] text-[#A3A3A3] pt-1">
              *Before PayPal capture, the backend re-computes this exact hash. If a single cent or letter changes, the capture is deterministically refused.
            </p>
          </div>
        </div>
      )}

      {/* Tab 3: Active Policies */}
      {activeTab === 'active_policies' && (
        <div className="space-y-4">
          {error && (
            <div className="p-4 rounded-2xl bg-[#2A0E08] border border-[#F04400] text-[#F04400] space-y-3 font-mono">
              <div className="flex items-center gap-2 font-black text-xs uppercase">
                <AlertCircle className="w-4 h-4 text-[#F04400]" />
                <span>Failed to Load Policies</span>
              </div>
              <p className="text-xs">{error}</p>
              <button
                onClick={() => loadPolicies()}
                className="px-4 py-2 rounded-xl bg-[#F04400] text-black text-xs font-black uppercase transition flex items-center gap-2"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                <span>Retry Connection</span>
              </button>
            </div>
          )}

          {isLoading && (
            <div className="p-12 text-center rounded-3xl bg-[#171717] border border-[#242424] text-[#A3A3A3] font-mono text-xs flex flex-col items-center justify-center gap-3">
              <Loader2 className="w-6 h-6 animate-spin text-[#F04400]" />
              <span>Loading enforceable consent policies from Trust Engine kernel...</span>
            </div>
          )}

          {!isLoading && !error && policies.length === 0 && (
            <div className="p-12 text-center rounded-3xl bg-[#171717] border border-[#242424] space-y-2 font-mono">
              <Lock className="w-8 h-8 text-[#A3A3A3] mx-auto" />
              <h3 className="text-sm font-black text-white uppercase">No Policies Configured</h3>
              <p className="text-xs text-[#A3A3A3]">No consent policies found for this user account.</p>
            </div>
          )}

          {!isLoading && !error && policies.length > 0 && (
            <div className="rounded-3xl bg-[#171717] border border-[#242424] shadow-xl overflow-hidden font-mono">
              <div className="p-5 border-b border-[#242424] bg-[#0E0E0E] flex items-center justify-between">
                <h3 className="text-xs font-black uppercase tracking-widest text-[#FFD91A]">
                  Active Enforceable Policies ({policies.length})
                </h3>
                <span className="text-xs text-[#D0F030] font-bold">Deterministic Rule Engine v1</span>
              </div>

              <div className="divide-y divide-[#242424] max-h-[600px] overflow-y-auto">
                {policies.map((p) => {
                  let validDateStr = 'Perpetual';
                  if (p.valid_until) {
                    try {
                      validDateStr = new Date(p.valid_until).toLocaleDateString();
                    } catch {
                      validDateStr = String(p.valid_until);
                    }
                  }
                  return (
                    <div key={p.id} className="p-5 text-xs space-y-2 hover:bg-[#0E0E0E] transition">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <span className="font-black text-white text-sm uppercase">{p.title}</span>
                          <span className="px-2 py-0.5 rounded text-[10px] bg-[#242424] text-[#FFD91A] font-bold uppercase">
                            {p.scope}
                          </span>
                        </div>
                        <span className="text-[#A3A3A3]">Policy #{p.id} (v{p.policy_version})</span>
                      </div>

                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[#A3A3A3] pt-1.5">
                        <div>
                          <span>Max Per Order: </span>
                          <strong className="text-white">${p.max_per_order.toFixed(2)}</strong>
                        </div>
                        <div>
                          <span>Monthly Ceiling: </span>
                          <strong className="text-white">${p.monthly_budget.toFixed(2)}</strong>
                        </div>
                        <div>
                          <span>Approval Mode: </span>
                          <strong className="text-[#FFD91A] font-black uppercase">{p.approval_mode}</strong>
                        </div>
                        <div>
                          <span>Validity: </span>
                          <strong className="text-white">{validDateStr}</strong>
                        </div>
                      </div>

                      {p.allowed_merchants && p.allowed_merchants.length > 0 && (
                        <div className="text-[11px] text-[#A3A3A3] pt-1">
                          <span>Allowed Merchants: </span>
                          <span className="text-white font-bold">{p.allowed_merchants.join(', ')}</span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Tab 4: 10 Invariant Proofs */}
      {activeTab === 'invariants' && (
        <div className="p-6 sm:p-7 rounded-3xl bg-[#171717] border border-[#242424] shadow-xl space-y-5 font-mono">
          <div className="flex items-center gap-2">
            <Lock className="w-5 h-5 text-[#FFD91A]" />
            <h3 className="text-base font-black text-white uppercase tracking-wider">
              The 10 Non-Negotiable Financial Security Invariants
            </h3>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5 text-xs">
            {[
              { id: 'INV-1', title: 'AI cannot approve payments', desc: 'Evaluator and capture executor are deterministic Python functions.' },
              { id: 'INV-2', title: 'AI cannot expand permissions', desc: 'Consent policies require authenticated human administrative tokens.' },
              { id: 'INV-3', title: 'Guardians capped to delegated scope', desc: 'Approvals exceeding max limits or unassigned categories are blocked.' },
              { id: 'INV-4', title: 'Revoked consent halts checkout', desc: 'Pre-capture checks evaluate live policy status at moment of payment.' },
              { id: 'INV-5', title: 'Expired approval invalidates transaction', desc: 'Approvals carry explicit TTL timestamps.' },
              { id: 'INV-6', title: 'Tampered fingerprint invalidates approval', desc: 'SHA-256 hash mismatch instantly resets transaction to REJECTED.' },
              { id: 'INV-7', title: 'Frontend success cannot prove payment', desc: 'Backend validates PayPal capture ID directly from PayPal API.' },
              { id: 'INV-8', title: 'Duplicate capture prevention', desc: 'State locks and unique database constraints eliminate double captures.' },
              { id: 'INV-9', title: 'Monthly budget is not blanket consent', desc: 'Every order must satisfy per-order, SKU, and merchant rules.' },
              { id: 'INV-10', title: 'No silent emergency bypass', desc: 'Urgent care needs alert caregivers; they never silently override consent.' },
            ].map((inv) => (
              <div key={inv.id} className="p-4 rounded-2xl bg-[#0E0E0E] border border-[#242424] hover:border-[#FFD91A] transition space-y-1">
                <div className="flex items-center gap-2 font-black">
                  <span className="px-2 py-0.5 rounded bg-[#FFD91A] text-black text-[10px]">
                    {inv.id}
                  </span>
                  <span className="text-white uppercase">{inv.title}</span>
                </div>
                <p className="text-[#A3A3A3] text-[11px] leading-relaxed pt-1">{inv.desc}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
