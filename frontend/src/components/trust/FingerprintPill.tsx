import React, { useState } from 'react';
import { Fingerprint, Check, Copy, ChevronDown, ChevronUp } from 'lucide-react';

interface FingerprintPillProps {
  hash?: string | null;
  label?: string;
  compact?: boolean;
  allowExpand?: boolean;
}

export const FingerprintPill: React.FC<FingerprintPillProps> = ({
  hash,
  label = 'Deterministic Fingerprint',
  compact = true,
  allowExpand = true,
}) => {
  const [copied, setCopied] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);

  if (!hash || typeof hash !== 'string' || hash.trim().length === 0) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-[10px] font-mono font-bold bg-[#171717] text-[#A3A3A3] border border-[#242424]">
        <Fingerprint className="w-3.5 h-3.5 text-[#A3A3A3]" />
        <span>UNBOUND / PENDING</span>
      </span>
    );
  }

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(hash);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const toggleExpand = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsExpanded((prev) => !prev);
  };

  const truncated =
    hash.length > 16
      ? `${hash.slice(0, 8)}...${hash.slice(-8)}`
      : hash;

  return (
    <div className="inline-flex flex-col gap-1.5 max-w-full">
      <div
        className={`inline-flex items-center gap-2 rounded-lg bg-[#171717] border border-[#242424] hover:border-[#D0F030]/60 text-white transition text-xs font-mono shadow-xs select-none ${
          compact ? 'px-2 py-0.5' : 'px-2.5 py-1'
        }`}
      >
        <Fingerprint className="w-3.5 h-3.5 text-[#F04400] shrink-0" />
        <span className="text-[10px] uppercase font-bold text-[#A3A3A3] tracking-wider truncate">
          {label}:
        </span>
        <span className="text-[#D0F030] font-bold text-[11px] tracking-wider">
          {truncated}
        </span>

        {/* Copy Button */}
        <button
          onClick={handleCopy}
          type="button"
          title="Copy full 64-character SHA-256 fingerprint"
          className="p-1 rounded hover:bg-[#242424] text-[#A3A3A3] hover:text-white transition cursor-pointer"
        >
          {copied ? (
            <Check className="w-3.5 h-3.5 text-[#D0F030]" />
          ) : (
            <Copy className="w-3.5 h-3.5" />
          )}
        </button>

        {/* Optional Expand Toggle */}
        {allowExpand && (
          <button
            onClick={toggleExpand}
            type="button"
            title={isExpanded ? 'Hide full hash' : 'Show full 64-character hash'}
            className="p-1 rounded hover:bg-[#242424] text-[#A3A3A3] hover:text-[#FFD91A] transition cursor-pointer"
          >
            {isExpanded ? (
              <ChevronUp className="w-3.5 h-3.5 text-[#FFD91A]" />
            ) : (
              <ChevronDown className="w-3.5 h-3.5" />
            )}
          </button>
        )}
      </div>

      {/* Expanded full hash block */}
      {isExpanded && (
        <div className="p-2.5 rounded-lg bg-[#0E0E0E] border border-[#242424] text-[10px] font-mono text-[#D4D4D4] break-all flex items-center justify-between gap-3 animate-in fade-in duration-150">
          <span className="text-[#D0F030] font-mono">{hash}</span>
          <button
            onClick={handleCopy}
            type="button"
            className="px-2 py-0.5 rounded bg-[#242424] hover:bg-[#333333] text-white text-[9px] uppercase font-bold shrink-0 transition"
          >
            {copied ? 'Copied!' : 'Copy Full'}
          </button>
        </div>
      )}
    </div>
  );
};
