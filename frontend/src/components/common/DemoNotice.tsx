import React from 'react';
import { ShieldAlert, Info } from 'lucide-react';

interface DemoNoticeProps {
  message?: string;
  variant?: 'banner' | 'pill' | 'inline';
}

export const DemoNotice: React.FC<DemoNoticeProps> = ({
  message = 'SIMULATED DEMO ENVIRONMENT — Synthetic test fixtures active. No live banking credentials or real funds transferred.',
  variant = 'banner',
}) => {
  if (variant === 'pill') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded text-[10px] font-black bg-[#FFD91A] text-black border border-[#FFD91A] tracking-wider font-mono">
        <span className="w-1.5 h-1.5 rounded-full bg-black animate-pulse" />
        DEMO MODE
      </span>
    );
  }

  if (variant === 'inline') {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-[#FFD91A] font-mono font-medium">
        <Info className="w-3.5 h-3.5 text-[#FFD91A] shrink-0" />
        <span>{message}</span>
      </span>
    );
  }

  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 px-4 py-3 rounded-2xl bg-[#171717] border border-[#242424] hover:border-[#F04400]/60 transition text-xs shadow-md">
      <div className="flex items-center gap-2.5">
        <div className="w-6 h-6 rounded-lg bg-[#F04400] flex items-center justify-center shrink-0">
          <ShieldAlert className="w-3.5 h-3.5 text-black stroke-[3]" />
        </div>
        <span className="font-mono text-[#D4D4D4] text-[11px] font-medium leading-relaxed">
          {message}
        </span>
      </div>
      <span className="px-2.5 py-1 rounded text-[10px] font-mono font-black bg-[#FFD91A] text-black tracking-wider shrink-0 self-start sm:self-auto uppercase">
        SYNTHETIC DATA
      </span>
    </div>
  );
};
