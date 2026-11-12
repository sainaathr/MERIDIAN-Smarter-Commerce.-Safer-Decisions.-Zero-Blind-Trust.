import React from 'react';
import type { LucideIcon } from 'lucide-react';

interface MetricCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  change?: string;
  changeType?: 'positive' | 'negative' | 'neutral';
  icon: LucideIcon;
  badge?: string;
  accent?: 'orange' | 'yellow' | 'lime' | 'default';
}

export const MetricCard: React.FC<MetricCardProps> = ({
  title,
  value,
  subtitle,
  change,
  changeType = 'neutral',
  icon: Icon,
  badge,
  accent = 'default',
}) => {
  // Bold creative editorial styling
  let iconBg = 'bg-[#F04400] text-black';
  let borderHighlight = 'border-[#242424] hover:border-[#F04400]';

  if (accent === 'yellow') {
    iconBg = 'bg-[#FFD91A] text-black';
    borderHighlight = 'border-[#242424] hover:border-[#FFD91A]';
  } else if (accent === 'lime') {
    iconBg = 'bg-[#D0F030] text-black';
    borderHighlight = 'border-[#242424] hover:border-[#D0F030]';
  }

  return (
    <div
      className={`relative overflow-hidden rounded-2xl bg-[#171717] border ${borderHighlight} p-5 transition duration-200 shadow-lg group`}
    >
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[11px] font-black uppercase tracking-widest text-[#A3A3A3] font-mono">
            {title}
          </p>
          <h3 className="mt-2 text-2xl sm:text-3xl font-black tracking-tight text-white font-mono">
            {value}
          </h3>
        </div>
        <div className={`p-3 rounded-xl ${iconBg} shadow-md font-bold transition group-hover:scale-105`}>
          <Icon className="w-5 h-5 stroke-[2.5]" />
        </div>
      </div>

      <div className="mt-4 flex items-center justify-between text-xs pt-3 border-t border-[#242424]">
        {subtitle && <span className="text-[#A3A3A3] font-mono text-[11px]">{subtitle}</span>}
        {change && (
          <span
            className={`font-bold font-mono text-[11px] ${
              changeType === 'positive'
                ? 'text-[#D0F030]'
                : changeType === 'negative'
                ? 'text-[#F04400]'
                : 'text-[#FFD91A]'
            }`}
          >
            {change}
          </span>
        )}
        {badge && (
          <span className="px-2 py-0.5 rounded text-[10px] font-mono font-black bg-[#242424] text-[#FFD91A] border border-[#333333]">
            {badge}
          </span>
        )}
      </div>
    </div>
  );
};
