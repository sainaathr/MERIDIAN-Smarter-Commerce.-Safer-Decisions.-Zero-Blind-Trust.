import React, { useState } from 'react';
import {
  ShieldCheck,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  X,
  Lock,
  ArrowRight,
} from 'lucide-react';
import {
  capturePayPalOrder,
  type PayPalOrderResponse,
  type PayPalCaptureResponse,
} from '../../services/api';
import { FingerprintPill } from '../trust/FingerprintPill';
import { MeridianLogo } from './MeridianLogo';

interface PayPalCheckoutModalProps {
  order: PayPalOrderResponse;
  proposalTitle: string;
  merchantName: string;
  itemsCount: number;
  onClose: () => void;
  onSuccess: (capture: PayPalCaptureResponse) => void;
}

export const PayPalCheckoutModal: React.FC<PayPalCheckoutModalProps> = ({
  order,
  proposalTitle,
  merchantName,
  itemsCount,
  onClose,
  onSuccess,
}) => {
  const [isCapturing, setIsCapturing] = useState(false);
  const [captureResult, setCaptureResult] = useState<PayPalCaptureResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleCapture = async () => {
    setIsCapturing(true);
    setErrorMsg(null);
    try {
      const result = await capturePayPalOrder(order.proposal_id, order.order_id);
      setCaptureResult(result);
      onSuccess(result);
    } catch (err: any) {
      setErrorMsg(err.message || 'Payment capture failed.');
    } finally {
      setIsCapturing(false);
    }
  };

  const formattedAmount = (order.total_cents / 100).toFixed(2);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/85 backdrop-blur-md animate-in fade-in duration-200">
      <div className="relative w-full max-w-lg rounded-3xl bg-[#171717] border border-[#242424] shadow-2xl p-6 sm:p-7 space-y-5 font-mono text-xs">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[#242424] pb-3">
          <div className="flex items-center gap-3">
            <MeridianLogo variant="icon" size="sm" glow={true} />
            <div>
              <h3 className="text-sm font-black text-white uppercase tracking-wider">
                MERIDIAN // PayPal Gateway
              </h3>
              <div className="flex items-center gap-2 mt-0.5">
                {order.is_simulated ? (
                  <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded bg-[#1A180E] text-[#FFD91A] border border-[#FFD91A]/50 font-black uppercase">
                    SIMULATED — NO PAYPAL TRANSACTION
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 text-[10px] px-2.5 py-0.5 rounded bg-[#0E0E0E] text-[#D0F030] border border-[#D0F030]/50 font-black uppercase">
                    <span className="w-1.5 h-1.5 rounded-full bg-[#D0F030] animate-pulse" />
                    PAYPAL SANDBOX (INTENT: CAPTURE)
                  </span>
                )}
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-[#A3A3A3] hover:text-white hover:bg-[#242424] transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Success / Result State */}
        {captureResult ? (
          <div
            className={`p-5 rounded-2xl ${
              captureResult.is_simulated
                ? 'bg-[#1A180E] border border-[#FFD91A]'
                : 'bg-[#0E0E0E] border border-[#D0F030]'
            } space-y-3`}
          >
            {captureResult.is_simulated ? (
              <>
                <div className="flex items-center gap-2 text-[#FFD91A]">
                  <AlertTriangle className="w-5 h-5 text-[#FFD91A]" />
                  <span className="font-black text-sm uppercase">
                    SIMULATED — NO PAYPAL TRANSACTION
                  </span>
                </div>
                <div className="p-3 rounded-xl bg-[#000000] border border-[#FFD91A]/40 text-[#D4D4D4] text-[11px] font-mono leading-relaxed">
                  Simulation completed. No PayPal transaction was created and no funds were captured.
                </div>
                <p className="text-[#A3A3A3] text-[11px] leading-relaxed">
                  Running in offline demonstration mode. The transaction has not been submitted to PayPal and remains in non-paid simulated state.
                </p>
              </>
            ) : (
              <>
                <div className="flex items-center gap-2 text-[#D0F030]">
                  <CheckCircle2 className="w-5 h-5 text-[#D0F030]" />
                  <span className="font-black text-sm uppercase">
                    PAYPAL SANDBOX — TEST CAPTURE VERIFIED
                  </span>
                </div>
                <p className="text-[#D4D4D4] text-[11px] leading-relaxed">
                  PayPal captured funds for Order <strong className="text-white">#{captureResult.order_id}</strong>. The Universal Trust Engine has verified and advanced transaction lifecycle to <strong className="text-[#D0F030]">PAID</strong>.
                </p>
              </>
            )}

            <div className="p-3.5 rounded-xl bg-[#000000] border border-[#242424] space-y-2 text-[11px]">
              <div className="flex justify-between">
                <span className="text-[#A3A3A3]">Capture ID:</span>
                <span className={captureResult.is_simulated ? "text-[#FFD91A] font-black" : "text-[#D0F030] font-black"}>
                  {captureResult.capture_id}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#A3A3A3]">Amount:</span>
                <span className="text-white font-black">${(captureResult.total_cents / 100).toFixed(2)} {captureResult.currency}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#A3A3A3]">Timestamp:</span>
                <span className="text-[#D4D4D4]">{new Date(captureResult.captured_at).toLocaleTimeString()}</span>
              </div>
              {captureResult.is_simulated && (
                <div className="flex justify-between pt-1 border-t border-[#242424]">
                  <span className="text-[#A3A3A3]">Mode:</span>
                  <span className="text-[#FFD91A] font-black">Simulated Offline Demo</span>
                </div>
              )}
            </div>

            <div className="pt-2 flex justify-end">
              <button
                onClick={onClose}
                className="px-5 py-2.5 rounded-xl bg-[#FFD91A] hover:bg-[#ffe040] text-black font-black uppercase tracking-wider text-xs shadow-md"
              >
                Close &amp; Return
              </button>
            </div>
          </div>
        ) : (
          <>
            {/* Order Summary Box */}
            <div className="p-4 rounded-2xl bg-[#0E0E0E] border border-[#242424] space-y-3">
              <div className="flex items-start justify-between">
                <div>
                  <span className="text-[10px] text-[#A3A3A3] uppercase tracking-wider block font-black">Proposal</span>
                  <p className="text-white font-black text-sm uppercase">{proposalTitle}</p>
                  <p className="text-[11px] text-[#A3A3A3]">Merchant: {merchantName} • {itemsCount} Item(s)</p>
                </div>
                <div className="text-right">
                  <span className="text-[10px] text-[#A3A3A3] uppercase tracking-wider block font-black">Authoritative Total</span>
                  <p className="text-base font-black text-[#D0F030]">${formattedAmount} {order.currency}</p>
                </div>
              </div>

              {/* Order ID & Fingerprint */}
              <div className="pt-2 border-t border-[#242424] space-y-2 text-[11px]">
                <div className="flex items-center justify-between">
                  <span className="text-[#A3A3A3]">PayPal Sandbox Order ID:</span>
                  <span className="text-[#FFD91A] font-bold">{order.order_id}</span>
                </div>
                <div>
                  <span className="text-[#A3A3A3] block mb-1 uppercase font-black text-[10px]">Cryptographic Fingerprint Bound:</span>
                  <FingerprintPill hash={order.fingerprint_sha256} compact />
                </div>
              </div>
            </div>

            {/* Simulation Warning Banner if Simulated */}
            {order.is_simulated && (
              <div className="flex items-center gap-2.5 p-3 rounded-xl bg-[#1A180E] border border-[#FFD91A] text-[11px] text-[#FFD91A]">
                <AlertTriangle className="w-4 h-4 text-[#FFD91A] shrink-0" />
                <div>
                  <span className="font-black uppercase tracking-wider block">
                    SIMULATION MODE — NO REAL PAYPAL TRANSACTION
                  </span>
                  <span className="text-[#D4D4D4] text-[10px]">
                    PayPal Sandbox credentials are unconfigured. The transaction will run as an offline demo.
                  </span>
                </div>
              </div>
            )}

            {/* Invariant Verification Banner */}
            <div className="flex items-center gap-2.5 p-3 rounded-xl bg-[#0E0E0E] border border-[#242424] text-[11px] text-[#D4D4D4]">
              <ShieldCheck className="w-4 h-4 text-[#D0F030] shrink-0" />
              <span>
                Deterministic Trust Invariant: Server recomputes SHA-256 fingerprint and validates policy before capture.
              </span>
            </div>

            {/* Error Message */}
            {errorMsg && (
              <div className="p-3 rounded-xl bg-[#2A0E08] border border-[#F04400] text-[#F04400] text-[11px] flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-[#F04400] shrink-0" />
                <span>{errorMsg}</span>
              </div>
            )}

            {/* Actions */}
            <div className="flex items-center justify-between pt-2">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2.5 rounded-xl bg-[#242424] hover:bg-[#333333] text-white transition text-xs font-bold uppercase"
              >
                Cancel
              </button>

              <button
                type="button"
                onClick={handleCapture}
                disabled={isCapturing}
                className={`px-5 py-2.5 rounded-xl font-black text-xs uppercase tracking-wider shadow-lg flex items-center gap-2 transition disabled:opacity-50 ${
                  order.is_simulated
                    ? 'bg-[#FFD91A] hover:bg-[#ffe040] text-black'
                    : 'bg-[#D0F030] hover:bg-[#d9f548] text-black'
                }`}
              >
                {isCapturing ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin text-black" />
                    <span>{order.is_simulated ? 'Executing Simulation...' : 'Executing Sandbox Capture...'}</span>
                  </>
                ) : (
                  <>
                    <Lock className="w-3.5 h-3.5" />
                    <span>{order.is_simulated ? 'Execute Simulation (Demo)' : 'Execute Sandbox Payment'}</span>
                    <ArrowRight className="w-3.5 h-3.5 stroke-[3]" />
                  </>
                )}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
