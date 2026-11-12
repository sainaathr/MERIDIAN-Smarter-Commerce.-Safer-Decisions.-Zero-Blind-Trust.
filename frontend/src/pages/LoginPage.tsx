import React, { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { ShieldCheck, Lock, Mail, Eye, EyeOff, Loader2, ArrowRight, Sparkles, AlertCircle } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { MeridianLogo } from '../components/common/MeridianLogo';

export const LoginPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { login } = useAuth();

  const [email, setEmail] = useState('alex@meridian.demo');
  const [password, setPassword] = useState('meridian2026');
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const from = (location.state as any)?.from?.pathname || '/';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password) {
      setError('Please provide email and password.');
      return;
    }
    setError(null);
    setIsLoading(true);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err: any) {
      setError(err.message || 'Invalid credentials. Please verify your demo login.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleQuickDemoLogin = async () => {
    setEmail('alex@meridian.demo');
    setPassword('meridian2026');
    setError(null);
    setIsLoading(true);
    try {
      await login('alex@meridian.demo', 'meridian2026');
      navigate(from, { replace: true });
    } catch (err: any) {
      setError(err.message || 'Demo authentication failed.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#000000] flex flex-col justify-center py-12 sm:px-6 lg:px-8 selection:bg-[#F04400] selection:text-black">
      <div className="sm:mx-auto sm:w-full sm:max-w-md text-center space-y-3">
        {/* Official Brand Emblem & Wordmark */}
        <div className="flex flex-col items-center justify-center space-y-2">
          <MeridianLogo variant="icon" size="xl" glow={true} className="mx-auto mb-2" />
          <h2 className="text-3xl font-black tracking-widest text-white font-mono uppercase">
            M<span className="text-[#F04400]">E</span>RIDIAN
          </h2>
          <p className="text-xs text-[#FFD91A] font-mono tracking-widest font-black uppercase">
            COMMERCE WITHOUT BLIND TRUST
          </p>
          <p className="text-[11px] text-[#A3A3A3] font-mono tracking-wide">
            Universal Trust Engine &amp; Commerce Autonomy
          </p>
        </div>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md px-4 sm:px-0">
        <div className="bg-[#171717] py-8 px-6 sm:px-10 rounded-3xl border border-[#242424] shadow-2xl space-y-6">
          <div className="border-b border-[#242424] pb-4">
            <span className="px-2.5 py-0.5 rounded text-[10px] font-mono font-black bg-[#FFD91A] text-black uppercase tracking-wider inline-block mb-2">
              Authentication Portal
            </span>
            <h3 className="text-lg font-black text-white font-mono uppercase tracking-tight">
              Sign In to Your Workspace
            </h3>
            <p className="text-xs text-[#A3A3A3] font-mono mt-1">
              Deterministic consent policies, approvals, and AI execution.
            </p>
          </div>

          {error && (
            <div className="p-3.5 rounded-xl bg-[#2A0E08] border border-[#F04400] text-[#F04400] text-xs font-mono flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-[#F04400] shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4 font-mono">
            <div>
              <label className="block text-xs font-black text-[#D4D4D4] uppercase tracking-wider mb-1.5">
                Email Address
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-[#A3A3A3]">
                  <Mail className="w-4 h-4 text-[#F04400]" />
                </div>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="block w-full pl-10 pr-3 py-2.5 bg-[#0E0E0E] border border-[#242424] rounded-xl text-xs text-white placeholder-[#737373] focus:outline-none focus:border-[#F04400] transition"
                  placeholder="alex@meridian.demo"
                  required
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-black text-[#D4D4D4] uppercase tracking-wider mb-1.5">
                Password
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-[#A3A3A3]">
                  <Lock className="w-4 h-4 text-[#F04400]" />
                </div>
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="block w-full pl-10 pr-10 py-2.5 bg-[#0E0E0E] border border-[#242424] rounded-xl text-xs text-white placeholder-[#737373] focus:outline-none focus:border-[#F04400] transition"
                  placeholder="••••••••••••"
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute inset-y-0 right-0 pr-3 flex items-center text-[#A3A3A3] hover:text-white"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={isLoading}
              className="w-full mt-2 py-3 px-4 rounded-xl bg-[#F04400] hover:bg-[#ff5714] text-black text-xs font-black font-mono uppercase tracking-wider shadow-lg shadow-[#F04400]/20 transition flex items-center justify-center gap-2 disabled:opacity-50"
            >
              {isLoading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin text-black" />
                  <span>Authenticating...</span>
                </>
              ) : (
                <>
                  <span>Sign In To Platform</span>
                  <ArrowRight className="w-4 h-4 stroke-[3]" />
                </>
              )}
            </button>
          </form>

          {/* Quick Demo Login Pill */}
          <div className="pt-2">
            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t border-[#242424]" />
              </div>
              <div className="relative flex justify-center text-xs">
                <span className="px-2 bg-[#171717] text-[#A3A3A3] font-mono text-[10px] uppercase tracking-widest font-black">
                  Competition Fast-Track
                </span>
              </div>
            </div>

            <div className="mt-4 p-4 rounded-2xl bg-[#0E0E0E] border border-[#FFD91A]/50 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-xs font-black text-[#FFD91A] font-mono">
                  <Sparkles className="w-3.5 h-3.5 text-[#FFD91A]" />
                  <span>Demo Primary Guardian</span>
                </div>
                <span className="text-[10px] font-mono font-bold text-black bg-[#FFD91A] px-2 py-0.5 rounded">
                  usr-alex-01
                </span>
              </div>
              <p className="text-[11px] text-[#D4D4D4] font-mono">
                Credentials: <code className="text-[#FFD91A] font-bold">alex@meridian.demo</code> / <code className="text-[#FFD91A] font-bold">meridian2026</code>
              </p>
              <button
                type="button"
                onClick={handleQuickDemoLogin}
                disabled={isLoading}
                className="w-full py-2.5 px-3 rounded-xl bg-[#FFD91A] hover:bg-[#ffe040] text-black text-xs font-black font-mono uppercase tracking-wider shadow-md transition flex items-center justify-center gap-2"
              >
                <span>Instant Demo Login (Alex Rivera)</span>
                <ArrowRight className="w-4 h-4 stroke-[3]" />
              </button>
            </div>
          </div>
        </div>

        {/* Security Invariant Footer */}
        <div className="mt-6 text-center text-xs text-[#A3A3A3] flex items-center justify-center gap-2 font-mono">
          <ShieldCheck className="w-4 h-4 text-[#D0F030]" />
          <span>Deterministic policy authorization enforced independently of identity</span>
        </div>
      </div>
    </div>
  );
};
