import React, { useState } from 'react';
import {
  Key,
  Server,
  Database,
  RefreshCw,
  CheckCircle2,
  Sparkles,
} from 'lucide-react';
import { MeridianLogo, BRAND_ASSETS } from '../components/common/MeridianLogo';

export const SettingsPage: React.FC = () => {
  const [resetSuccess, setResetSuccess] = useState(false);

  const handleResetDemoState = () => {
    setResetSuccess(true);
    setTimeout(() => setResetSuccess(false), 3000);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 p-6 sm:p-7 rounded-3xl bg-[#171717] border border-[#242424] shadow-xl">
        <div className="space-y-1">
          <div className="flex items-center gap-2.5">
            <MeridianLogo variant="icon" size="xs" />
            <span className="px-2.5 py-0.5 rounded text-[10px] font-mono font-black bg-[#FFD91A] text-black uppercase tracking-widest">
              SYSTEM CONFIG
            </span>
            <span className="text-xs font-mono text-[#D0F030] font-bold">CREDENTIALS ISOLATION</span>
          </div>
          <h2 className="text-xl sm:text-2xl font-black text-white font-mono uppercase tracking-tight">
            System Configuration &amp; Credentials State
          </h2>
          <p className="text-xs text-[#A3A3A3] max-w-2xl font-mono leading-relaxed">
            Inspect integration endpoints, security policies, and environment variables. Secrets are isolated on the backend.
          </p>
        </div>
        <span className="px-3.5 py-1.5 rounded-xl text-xs font-mono font-black bg-[#FFD91A] text-black uppercase tracking-wider shrink-0">
          ENV: DEVELOPMENT
        </span>
      </div>

      {/* Integration Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5 text-xs font-mono">
        {/* PayPal Sandbox Status */}
        <div className="p-6 rounded-3xl bg-[#171717] border border-[#242424] shadow-xl space-y-3">
          <div className="flex items-center justify-between">
            <span className="font-black text-white flex items-center gap-2 text-sm uppercase">
              <Key className="w-4 h-4 text-[#FFD91A]" />
              PayPal Sandbox REST API v2
            </span>
            <span className="px-2.5 py-0.5 rounded text-[10px] bg-[#FFD91A] text-black font-black uppercase">
              SANDBOX MODE
            </span>
          </div>
          <p className="text-[#D4D4D4] leading-relaxed">
            Endpoints operate against the official PayPal Developer Sandbox. Payments are processed with simulated test buyers.
          </p>
          <div className="p-4 rounded-2xl bg-[#0E0E0E] border border-[#242424] space-y-2 text-[#A3A3A3]">
            <div className="flex justify-between">
              <span>Base URL:</span>
              <span className="text-white font-bold">https://api-m.sandbox.paypal.com</span>
            </div>
            <div className="flex justify-between">
              <span>Client Secret:</span>
              <span className="text-[#D0F030] font-black uppercase">Protected (Backend Only)</span>
            </div>
            <div className="flex justify-between">
              <span>Payment Capture Flow:</span>
              <span className="text-white font-bold">Orders v2 (/v2/checkout/orders)</span>
            </div>
          </div>
        </div>

        {/* Gemini AI Status */}
        <div className="p-6 rounded-3xl bg-[#171717] border border-[#242424] shadow-xl space-y-3">
          <div className="flex items-center justify-between">
            <span className="font-black text-white flex items-center gap-2 text-sm uppercase">
              <Server className="w-4 h-4 text-[#F04400]" />
              Google Gemini 2.0 Flash
            </span>
            <span className="px-2.5 py-0.5 rounded text-[10px] bg-[#F04400] text-black font-black uppercase">
              PROPOSAL ENGINE
            </span>
          </div>
          <p className="text-[#D4D4D4] leading-relaxed">
            AI language engine for intent interpretation and financial summarization. Zero direct execution authority.
          </p>
          <div className="p-4 rounded-2xl bg-[#0E0E0E] border border-[#242424] space-y-2 text-[#A3A3A3]">
            <div className="flex justify-between">
              <span>Active Model:</span>
              <span className="text-white font-bold">gemini-2.0-flash</span>
            </div>
            <div className="flex justify-between">
              <span>Prompt Quarantine:</span>
              <span className="text-[#D0F030] font-black uppercase">Active &amp; Neutralized</span>
            </div>
            <div className="flex justify-between">
              <span>Direct Payment Authority:</span>
              <span className="text-[#F04400] font-black uppercase">Blocked (Invariant INV-1)</span>
            </div>
          </div>
        </div>
      </div>

      {/* Official Brand Identity & Assets */}
      <div className="rounded-3xl bg-[#171717] border border-[#242424] shadow-xl p-6 sm:p-7 space-y-4">
        <div className="flex items-center justify-between border-b border-[#242424] pb-3">
          <h3 className="text-xs font-mono font-black uppercase tracking-widest text-[#FFD91A] flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-[#F04400]" />
            Official Brand Identity &amp; System Assets
          </h3>
          <span className="px-2.5 py-0.5 rounded text-[10px] font-mono font-black bg-[#D0F030] text-black uppercase">
            ACTIVE BRAND
          </span>
        </div>

        <div className="flex flex-col md:flex-row items-center justify-between gap-6 p-4 rounded-2xl bg-[#000000] border border-[#242424]">
          <div className="flex items-center gap-4">
            <MeridianLogo variant="icon" size="lg" glow={true} />
            <div className="space-y-1">
              <div className="flex items-center font-mono font-black tracking-widest uppercase text-xl">
                <span className="text-white">M</span>
                <span className="text-[#F04400]">E</span>
                <span className="text-white">RIDIAN</span>
              </div>
              <p className="text-xs text-[#FFD91A] font-mono tracking-widest font-black uppercase">
                {BRAND_ASSETS.tagline}
              </p>
              <p className="text-[11px] text-[#A3A3A3] font-mono">
                Source: <code>/branding/meridian-logo-master.png</code>
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <a
              href="/branding/meridian-logo.png"
              target="_blank"
              rel="noopener noreferrer"
              className="px-3.5 py-2 rounded-xl bg-[#242424] hover:bg-[#333333] text-white text-xs font-mono font-bold uppercase transition"
            >
              Full Logo
            </a>
            <a
              href="/branding/meridian-icon.png"
              target="_blank"
              rel="noopener noreferrer"
              className="px-3.5 py-2 rounded-xl bg-[#242424] hover:bg-[#333333] text-white text-xs font-mono font-bold uppercase transition"
            >
              Icon PNG
            </a>
            <a
              href="/favicon.svg"
              target="_blank"
              rel="noopener noreferrer"
              className="px-3.5 py-2 rounded-xl bg-[#F04400] text-black text-xs font-mono font-black uppercase transition shadow-md"
            >
              Favicon SVG
            </a>
          </div>
        </div>
      </div>

      {/* Global Defaults & Demo Controls */}
      <div className="rounded-3xl bg-[#171717] border border-[#242424] shadow-xl p-6 sm:p-7 space-y-4">
        <h3 className="text-xs font-mono font-black uppercase tracking-widest text-[#FFD91A] flex items-center gap-2">
          <Database className="w-4 h-4 text-[#F04400]" />
          Test Fixtures &amp; Demo Controls
        </h3>
        <p className="text-xs text-[#D4D4D4] font-mono leading-relaxed">
          Reset all proposal states, tamper simulations, and audit logs to the default synthetic baseline for testing and presentation recording.
        </p>

        <div className="flex items-center gap-3 pt-2">
          <button
            onClick={handleResetDemoState}
            className="px-5 py-2.5 rounded-xl bg-[#F04400] hover:bg-[#ff5714] text-black text-xs font-mono font-black uppercase tracking-wider transition flex items-center gap-2 shadow-lg shadow-[#F04400]/20"
          >
            <RefreshCw className="w-4 h-4 text-black stroke-[3]" />
            <span>Reset Demo Fixtures</span>
          </button>
          {resetSuccess && (
            <span className="text-xs font-mono text-[#D0F030] flex items-center gap-1.5 font-bold uppercase">
              <CheckCircle2 className="w-4 h-4 text-[#D0F030]" />
              Fixtures re-initialized!
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
