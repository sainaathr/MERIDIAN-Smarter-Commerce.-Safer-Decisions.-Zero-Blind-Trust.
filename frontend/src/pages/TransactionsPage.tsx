import React, { useState, useEffect, useCallback } from 'react';
import {
  Search,
  Filter,
  Eye,
  RefreshCw,
  Loader2,
  AlertTriangle,
  CreditCard,
} from 'lucide-react';
import { fetchProposals, fetchTransactions, type PaymentDetailResponse } from '../services/api';
import { StatusBadge } from '../components/common/StatusBadge';
import { FingerprintPill } from '../components/trust/FingerprintPill';
import { Modal } from '../components/common/Modal';
import { MeridianLogo } from '../components/common/MeridianLogo';
import type { TransactionProposal } from '../types';

export const TransactionsPage: React.FC = () => {
  const [transactions, setTransactions] = useState<TransactionProposal[]>([]);
  const [paymentDetails, setPaymentDetails] = useState<Record<string, PaymentDetailResponse>>({});
  const [selectedTx, setSelectedTx] = useState<TransactionProposal | null>(null);
  const [moduleFilter, setModuleFilter] = useState<string>('ALL');
  const [searchTerm, setSearchTerm] = useState<string>('');
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [liveProposals, livePayments] = await Promise.all([
        fetchProposals(),
        fetchTransactions().catch((err) => {
          console.warn('Failed to fetch live transactions metadata:', err);
          return [];
        }),
      ]);

      if (livePayments && livePayments.length > 0) {
        const pMap: Record<string, PaymentDetailResponse> = {};
        for (const p of livePayments) {
          pMap[p.proposal_id] = p;
        }
        setPaymentDetails(pMap);
      }

      setTransactions(liveProposals);
    } catch (err: any) {
      console.error('Error loading transaction history:', err);
      setError(err.message || 'Failed to load transaction history.');
      setTransactions([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const filteredTransactions = transactions.filter((tx) => {
    const matchModule = moduleFilter === 'ALL' || tx.module === moduleFilter;
    const matchSearch =
      tx.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
      tx.merchant_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      tx.id.toLowerCase().includes(searchTerm.toLowerCase());
    return matchModule && matchSearch;
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 p-6 sm:p-7 rounded-3xl bg-[#171717] border border-[#242424] shadow-xl">
        <div className="space-y-1">
          <div className="flex items-center gap-2.5">
            <MeridianLogo variant="icon" size="xs" />
            <span className="px-2.5 py-0.5 rounded text-[10px] font-mono font-black bg-[#F04400] text-black uppercase tracking-widest">
              LEDGER
            </span>
            <span className="text-xs font-mono text-[#FFD91A] font-bold">AUTHORITATIVE</span>
          </div>
          <h2 className="text-xl sm:text-2xl font-black text-white font-mono uppercase tracking-tight">
            Transaction Lifecycle History
          </h2>
          <p className="text-xs text-[#A3A3A3] max-w-2xl font-mono leading-relaxed">
            Authoritative backend record of prepared, approved, captured, and reconciled transactions across all commerce modules.
          </p>
        </div>
        <div className="flex items-center gap-2.5 shrink-0">
          <button
            onClick={() => loadData()}
            disabled={isLoading}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-mono bg-[#0E0E0E] hover:bg-[#242424] disabled:opacity-50 text-white border border-[#242424] transition font-bold uppercase"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin text-[#F04400]' : 'text-[#FFD91A]'}`} />
            <span>Refresh</span>
          </button>
          <span className="px-3 py-2 rounded-xl text-xs font-mono font-black bg-[#FFD91A] text-black uppercase tracking-wider">
            ALL AUDITED
          </span>
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="p-4 rounded-2xl bg-[#2A0E08] border border-[#F04400] text-[#F04400] text-xs font-mono flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-[#F04400] shrink-0" />
            <span>Failed to connect to Universal Trust Engine: {error}</span>
          </div>
          <button
            onClick={() => loadData()}
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
            placeholder="Search by merchant, ID, item..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full bg-[#171717] border border-[#242424] rounded-2xl pl-10 pr-4 py-2.5 text-white placeholder-[#737373] focus:outline-none focus:border-[#F04400] shadow-md transition"
          />
        </div>

        <div className="flex items-center gap-2.5 w-full sm:w-auto">
          <Filter className="w-4 h-4 text-[#FFD91A]" />
          <span className="text-[#A3A3A3] uppercase font-bold text-[11px]">Filter Module:</span>
          <select
            value={moduleFilter}
            onChange={(e) => setModuleFilter(e.target.value)}
            className="bg-[#171717] border border-[#242424] rounded-xl px-3 py-2 text-white focus:outline-none focus:border-[#F04400] shadow-md font-bold uppercase text-xs"
          >
            <option value="ALL">All Modules</option>
            <option value="PERSONAL">Personal / PACT</option>
            <option value="BUSINESS">Business / FLOW</option>
            <option value="COLLECTIVE">Collective / COMMON</option>
            <option value="CARE">Care / Assisted</option>
          </select>
        </div>
      </div>

      {/* Transactions Table */}
      <div className="rounded-3xl bg-[#171717] border border-[#242424] shadow-xl overflow-hidden">
        {isLoading ? (
          <div className="p-12 text-center text-[#A3A3A3] text-xs font-mono flex flex-col items-center justify-center gap-3">
            <Loader2 className="w-6 h-6 animate-spin text-[#F04400]" />
            <span>Loading authoritative transaction history from Universal Trust Engine...</span>
          </div>
        ) : error ? (
          <div className="p-12 text-center text-[#A3A3A3] text-xs font-mono flex flex-col items-center justify-center gap-3">
            <AlertTriangle className="w-6 h-6 text-[#F04400]" />
            <span>Failed to load transaction records from backend. Please use the Retry button above.</span>
          </div>
        ) : filteredTransactions.length === 0 ? (
          <div className="p-12 text-center text-[#A3A3A3] text-xs font-mono">
            No transactions found matching the selected filter criteria.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead className="bg-[#0E0E0E] border-b border-[#242424] text-[#FFD91A] uppercase text-[10px] tracking-wider">
                <tr>
                  <th className="px-5 py-3.5">Proposal / Tx ID</th>
                  <th className="px-5 py-3.5">Module</th>
                  <th className="px-5 py-3.5">Merchant</th>
                  <th className="px-5 py-3.5">Total Amount</th>
                  <th className="px-5 py-3.5">Payment Provider</th>
                  <th className="px-5 py-3.5">Date</th>
                  <th className="px-5 py-3.5">Status</th>
                  <th className="px-5 py-3.5 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#242424]">
                {filteredTransactions.map((tx) => {
                  const payment = paymentDetails[tx.id];
                  const isSimulated = tx.is_simulated ?? payment?.is_simulated;
                  const hasOrder = !!(tx.paypal_order_id || payment?.paypal_order_id);

                  return (
                    <tr key={tx.id} className="hover:bg-[#0E0E0E] transition">
                      <td className="px-5 py-4 font-black text-white">
                        {tx.id}
                        <span className="block text-[10px] font-normal text-[#A3A3A3] truncate max-w-xs font-mono mt-0.5">
                          {tx.title}
                        </span>
                      </td>
                      <td className="px-5 py-4">
                        <span className="px-2.5 py-0.5 rounded text-[10px] font-bold bg-[#0E0E0E] text-[#D4D4D4] border border-[#242424] uppercase">
                          {tx.module}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-white font-medium">{tx.merchant_name}</td>
                      <td className="px-5 py-4 font-black text-[#D0F030]">
                        ${tx.total.toFixed(2)} {tx.currency}
                      </td>
                      <td className="px-5 py-4">
                        {isSimulated ? (
                          <span className="px-2 py-0.5 rounded text-[9px] font-black bg-[#1A180E] text-[#FFD91A] border border-[#FFD91A]/40 uppercase">
                            SIMULATED
                          </span>
                        ) : hasOrder ? (
                          <span className="px-2 py-0.5 rounded text-[9px] font-black bg-[#0E0E0E] text-[#D0F030] border border-[#D0F030]/40 uppercase">
                            PAYPAL SANDBOX
                          </span>
                        ) : (
                          <span className="text-[10px] text-[#737373]">—</span>
                        )}
                      </td>
                      <td className="px-5 py-4 text-[#A3A3A3] text-[11px] whitespace-nowrap">
                        {tx.created_at?.slice(0, 19).replace('T', ' ')}
                      </td>
                      <td className="px-5 py-4">
                        <StatusBadge state={tx.state} />
                      </td>
                      <td className="px-5 py-4 text-right">
                        <button
                          onClick={() => setSelectedTx(tx)}
                          className="px-3 py-1.5 rounded-xl bg-[#242424] hover:bg-[#333333] text-white border border-[#333333] transition inline-flex items-center gap-1.5 text-[11px] font-bold uppercase"
                        >
                          <Eye className="w-3.5 h-3.5 text-[#FFD91A]" />
                          <span>Inspect</span>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Details Modal */}
      {selectedTx && (
        <Modal
          isOpen={!!selectedTx}
          onClose={() => setSelectedTx(null)}
          title={`Transaction Audit Record #${selectedTx.id}`}
          subtitle={`Module: ${selectedTx.module} • Created: ${selectedTx.created_at}`}
        >
          <div className="space-y-4 text-xs font-mono">
            {/* Status & Fingerprint */}
            <div className="p-4 rounded-2xl bg-[#0E0E0E] border border-[#242424] space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-[#A3A3A3] uppercase font-bold">Lifecycle State:</span>
                <StatusBadge state={selectedTx.state} />
              </div>
              <div className="pt-2 border-t border-[#242424]">
                <span className="text-[#FFD91A] text-[10px] block mb-1 font-black uppercase">
                  CRYPTOGRAPHIC SHA-256 FINGERPRINT:
                </span>
                <FingerprintPill hash={selectedTx.fingerprint_sha256} />
              </div>
            </div>

            {/* Merchant & Pricing */}
            <div className="grid grid-cols-2 gap-3 p-4 rounded-2xl bg-[#0E0E0E] border border-[#242424]">
              <div>
                <span className="text-[#A3A3A3] uppercase">Merchant Identity:</span>
                <p className="text-white font-black text-sm">{selectedTx.merchant_name}</p>
                <span className="text-[10px] text-[#737373]">ID: {selectedTx.merchant_id}</span>
              </div>
              <div>
                <span className="text-[#A3A3A3] uppercase">Final Total:</span>
                <p className="text-[#D0F030] font-black text-base">
                  ${selectedTx.total.toFixed(2)} {selectedTx.currency}
                </p>
                <span className="text-[10px] text-[#A3A3A3]">
                  Subtotal: ${selectedTx.subtotal.toFixed(2)} + Tax: ${selectedTx.tax.toFixed(2)}
                </span>
              </div>
            </div>

            {/* Payment Subsystem Verification */}
            {(selectedTx.paypal_order_id || paymentDetails[selectedTx.id]?.paypal_order_id || selectedTx.is_simulated) && (
              <div className="p-4 rounded-2xl bg-[#0E0E0E] border border-[#242424] space-y-2">
                <div className="flex items-center gap-1.5 text-[#FFD91A] font-black uppercase text-[10px]">
                  <CreditCard className="w-4 h-4" />
                  <span>Payment Gateway Execution Record</span>
                </div>
                <div className="grid grid-cols-2 gap-2 text-[11px] pt-1.5 border-t border-[#242424]">
                  <div>
                    <span className="text-[#A3A3A3] block">Gateway Order ID:</span>
                    <span className="text-white font-bold">
                      {selectedTx.paypal_order_id || paymentDetails[selectedTx.id]?.paypal_order_id || 'N/A'}
                    </span>
                  </div>
                  <div>
                    <span className="text-[#A3A3A3] block">Capture Reference:</span>
                    <span className="text-white font-bold">
                      {selectedTx.paypal_capture_id || paymentDetails[selectedTx.id]?.paypal_capture_id || 'N/A'}
                    </span>
                  </div>
                  <div>
                    <span className="text-[#A3A3A3] block">Simulation Mode:</span>
                    <span className={selectedTx.is_simulated ? 'text-[#FFD91A] font-bold' : 'text-[#D0F030] font-bold'}>
                      {selectedTx.is_simulated ? 'SIMULATED (No funds moved)' : 'PAYPAL SANDBOX LIVE'}
                    </span>
                  </div>
                  <div>
                    <span className="text-[#A3A3A3] block">Payment State:</span>
                    <span className="text-white font-bold uppercase">
                      {selectedTx.paypal_status || paymentDetails[selectedTx.id]?.paypal_status || selectedTx.state}
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* Items */}
            <div className="space-y-2">
              <span className="text-[#FFD91A] font-black uppercase text-[10px] tracking-wider">
                Authorized Line Items ({selectedTx.items.length})
              </span>
              <div className="p-4 rounded-2xl bg-[#0E0E0E] border border-[#242424] divide-y divide-[#242424]">
                {selectedTx.items.map((item) => (
                  <div key={item.id} className="py-2.5 first:pt-0 last:pb-0 flex justify-between">
                    <div>
                      <p className="text-white font-bold">{item.name}</p>
                      <span className="text-[10px] text-[#A3A3A3]">Qty: {item.quantity}</span>
                    </div>
                    <span className="font-black text-[#D0F030]">${item.total.toFixed(2)}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* AI Reasoning */}
            <div className="p-4 rounded-2xl bg-[#0E0E0E] border border-[#242424] space-y-1">
              <span className="text-[#FFD91A] font-black uppercase text-[10px]">AI Rationale</span>
              <p className="text-[#D4D4D4] font-sans">{selectedTx.ai_reasoning}</p>
            </div>

            {/* Policy Verification */}
            <div className="p-4 rounded-2xl bg-[#0E0E0E] border border-[#242424] space-y-1">
              <span className="text-[#D0F030] font-black uppercase text-[10px]">
                Trust Engine Policy Evaluation
              </span>
              <p className="text-white font-mono">{selectedTx.policy_notes || 'All checks passed.'}</p>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
};
