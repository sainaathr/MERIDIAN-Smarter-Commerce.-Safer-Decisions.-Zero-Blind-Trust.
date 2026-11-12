import React from 'react';
import { Link } from 'react-router-dom';

export interface MeridianLogoProps {
  variant?: 'icon' | 'full' | 'wordmark';
  size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl';
  showTagline?: boolean;
  linkTo?: string | null;
  className?: string;
  glow?: boolean;
}

export const BRAND_ASSETS = {
  logoMaster: '/branding/meridian-logo-master.png',
  logoFull: '/branding/meridian-logo.png',
  logoTransparent: '/branding/meridian-logo-transparent.png',
  logoSmall: '/branding/meridian-logo-small.png',
  icon: '/branding/meridian-icon.png',
  iconDark: '/branding/meridian-icon-dark.png',
  iconTransparent: '/branding/meridian-icon-transparent.png',
  faviconSvg: '/favicon.svg',
  tagline: 'COMMERCE WITHOUT BLIND TRUST',
  name: 'MERIDIAN',
  description: 'Intelligent commerce autonomy powered by deterministic trust.',
} as const;

export const MeridianLogo: React.FC<MeridianLogoProps> = ({
  variant = 'icon',
  size = 'md',
  showTagline = false,
  linkTo = null,
  className = '',
  glow = false,
}) => {
  // Dimension mappings
  const iconSizes = {
    xs: 'w-6 h-6',
    sm: 'w-8 h-8',
    md: 'w-10 h-10',
    lg: 'w-14 h-14',
    xl: 'w-20 h-20',
  };

  const fullSizes = {
    xs: 'h-6',
    sm: 'h-8',
    md: 'h-10',
    lg: 'h-14',
    xl: 'h-20',
  };

  const glowClass = glow ? 'drop-shadow-[0_0_12px_rgba(240,68,0,0.35)]' : '';

  let content: React.ReactNode;

  if (variant === 'full') {
    content = (
      <div className={`inline-flex flex-col items-start ${className}`}>
        <img
          src={BRAND_ASSETS.logoTransparent}
          alt="MERIDIAN — Commerce Without Blind Trust"
          className={`${fullSizes[size]} w-auto object-contain ${glowClass}`}
          loading="eager"
        />
      </div>
    );
  } else if (variant === 'wordmark') {
    content = (
      <div className={`inline-flex items-center gap-3 ${className}`}>
        <img
          src={BRAND_ASSETS.icon}
          alt="MERIDIAN"
          className={`${iconSizes[size]} shrink-0 object-contain ${glowClass}`}
          loading="eager"
        />
        <div className="flex flex-col">
          <div className="flex items-center font-mono font-black tracking-widest uppercase leading-none">
            <span className="text-white text-base sm:text-lg">M</span>
            <span className="text-[#F04400] text-base sm:text-lg">E</span>
            <span className="text-white text-base sm:text-lg">RIDIAN</span>
          </div>
          {showTagline && (
            <span className="text-[9px] sm:text-[10px] text-[#A3A3A3] font-mono tracking-tight uppercase leading-tight mt-1">
              {BRAND_ASSETS.tagline}
            </span>
          )}
        </div>
      </div>
    );
  } else {
    // Default 'icon'
    content = (
      <img
        src={BRAND_ASSETS.icon}
        alt="MERIDIAN Emblem"
        className={`${iconSizes[size]} object-contain shrink-0 ${glowClass} ${className}`}
        loading="eager"
      />
    );
  }

  if (linkTo) {
    return (
      <Link to={linkTo} className="inline-flex focus:outline-hidden hover:opacity-95 transition">
        {content}
      </Link>
    );
  }

  return <>{content}</>;
};

export default MeridianLogo;
