import React from 'react';
import type { TransactionLifecycleState, PolicyDecisionType } from '../../types';

interface StatusBadgeProps {
  state?: TransactionLifecycleState;
  decision?: PolicyDecisionType;
  severity?: 'INFO' | 'SUCCESS' | 'WARNING' | 'ALERT';
  text?: string;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ state, decision, severity, text }) => {
  let color = 'bg-[#242424] text-[#D4D4D4] border-[#333333]';
  let dotColor = 'bg-[#A3A3A3]';
  let label = text || state || decision || 'UNKNOWN';

  if (state === 'PAID') {
    color = 'bg-[#D0F030] text-black border-[#D0F030] font-black';
    dotColor = 'bg-black';
  } else if (state === 'APPROVED' || decision === 'ALLOW') {
    color = 'bg-[#171717] text-[#D0F030] border-[#D0F030]/60 font-black';
    dotColor = 'bg-[#D0F030]';
  } else if (state === 'AWAITING_APPROVAL' || decision === 'REQUIRE_APPROVAL') {
    color = 'bg-[#FFD91A] text-black border-[#FFD91A] font-black';
    dotColor = 'bg-black';
  } else if (state === 'REJECTED' || state === 'FAILED' || decision === 'DENY' || severity === 'ALERT') {
    color = 'bg-[#F04400] text-black border-[#F04400] font-black';
    dotColor = 'bg-black';
  } else if (state === 'PAYMENT_PENDING' || state === 'POLICY_CHECK' || state === 'PROPOSED') {
    color = 'bg-[#171717] text-[#FFD91A] border-[#FFD91A]/60 font-bold';
    dotColor = 'bg-[#FFD91A]';
  } else if (severity === 'WARNING') {
    color = 'bg-[#171717] text-[#FFD91A] border-[#FFD91A] font-bold';
    dotColor = 'bg-[#FFD91A]';
  } else if (severity === 'SUCCESS') {
    color = 'bg-[#171717] text-[#D0F030] border-[#D0F030] font-bold';
    dotColor = 'bg-[#D0F030]';
  }

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] uppercase font-mono tracking-wider border shadow-xs ${color}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${dotColor} shrink-0`} />
      <span>{label}</span>
    </span>
  );
};
