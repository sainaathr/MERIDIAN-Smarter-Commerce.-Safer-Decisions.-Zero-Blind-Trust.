import React, { useState, useEffect, useCallback } from 'react';
import {
  TrendingUp,
  Receipt,
  Calendar,
  Sparkles,
  ArrowUpRight,
  ArrowDownLeft,
  DollarSign,
  ShieldCheck,
  Loader2,
  AlertTriangle,
  PlusCircle,
  CheckCircle2,
  Lock,
  X,
  RefreshCw,
  ShoppingCart,
  ShieldAlert,
} from 'lucide-react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
} from 'recharts';
import { MetricCard } from '../components/common/MetricCard';
import { StatusBadge } from '../components/common/StatusBadge';
import { FingerprintPill } from '../components/trust/FingerprintPill';
import { PayPalCheckoutModal } from '../components/common/PayPalCheckoutModal';
import { MeridianLogo } from '../components/common/MeridianLogo';
import {
  fetchBusinessProfile,
  fetchBusinessInvoices,
  fetchCashflowForecast,
  checkBusinessAffordability,
  createBusinessProposal,
  explainBusinessCashflow,
  fetchProposals,
  submitHumanApproval,
  createPayPalOrder,
  type BusinessProfileResponse,
  type InvoiceResponse,
  type CashflowForecastResponse,
  type BusinessAffordabilityCheckResponse,
  type BusinessCashflowResponse,
  type PayPalOrderResponse,
  type PayPalCaptureResponse,
} from '../services/api';
import type { TransactionProposal } from '../types';

const PRESET_SUPPLIERS = [
  {
    id: 'm-aws-01',
    name: 'AWS Cloud Sandbox',
    category: 'Infrastructure',
    defaultTitle: 'EC2 Reserved Compute Capacity (1 Month)',
    defaultAmount: 150.0,
    unitPriceCents: 15000,
    sku: 'aws-ec2-res',
  },
  {
    id: 'm-github-01',
    name: 'GitHub Enterprise',
    category: 'Dev Tools',
    defaultTitle: 'GitHub Copilot Enterprise Tier Seats',
    defaultAmount: 95.0,
    unitPriceCents: 9500,
    sku: 'gh-copilot-ent',
  },
  {
    id: 'm-figma-01',
    name: 'Figma Inc',
    category: 'Dev Tools',
    defaultTitle: 'Figma Design Team Organization License',
    defaultAmount: 75.0,
    unitPriceCents: 7500,
    sku: 'figma-org-01',
  },
  {
    id: 'custom',
    name: 'Custom / Affordability Test',
    category: 'Infrastructure',
    defaultTitle: 'High-Performance GPU Cluster',
    defaultAmount: 9000.0,
    unitPriceCents: 900000,
    sku: 'custom-exp-01',
  },
];

export const BusinessPage: React.FC = () => {
  // Authoritative Backend State
  const [profile, setProfile] = useState<BusinessProfileResponse | null>(null);
  const [invoices, setInvoices] = useState<InvoiceResponse[]>([]);
  const [forecast, setForecast] = useState<CashflowForecastResponse | null>(null);
  const [proposals, setProposals] = useState<TransactionProposal[]>([]);

  // UI & Loading State
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [invoiceTypeFilter, setInvoiceTypeFilter] = useState<'ALL' | 'RECEIVABLE' | 'PAYABLE'>('ALL');
  const [invoiceStatusFilter, setInvoiceStatusFilter] = useState<'ALL' | 'PENDING' | 'OVERDUE' | 'PAID'>('ALL');

  // Explainable AI State
  const [query, setQuery] = useState('Can we afford to upgrade AWS servers for $150 while maintaining our reserve?');
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisResult, setAnalysisResult] = useState<BusinessCashflowResponse | null>(null);

  // Propose Purchase Modal State
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedSupplierIdx, setSelectedSupplierIdx] = useState(0);
  const [customTitle, setCustomTitle] = useState(PRESET_SUPPLIERS[0].defaultTitle);
  const [customAmountStr, setCustomAmountStr] = useState(PRESET_SUPPLIERS[0].defaultAmount.toString());
  const [customCategory, setCustomCategory] = useState(PRESET_SUPPLIERS[0].category);
  const [isCheckingAffordability, setIsCheckingAffordability] = useState(false);
  const [affordabilityStatus, setAffordabilityStatus] = useState<BusinessAffordabilityCheckResponse | null>(null);
  const [isSubmittingProposal, setIsSubmittingProposal] = useState(false);
  const [proposalSubmitError, setProposalSubmitError] = useState<string | null>(null);

  // Proposal Action & Checkout State
  const [approvingId, setApprovingId] = useState<string | null>(null);
  const [checkingOutId, setCheckingOutId] = useState<string | null>(null);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);
  const [activeCheckoutOrder, setActiveCheckoutOrder] = useState<PayPalOrderResponse | null>(null);
  const [activeProposalDetails, setActiveProposalDetails] = useState<{ title: string; merchant: string; itemsCount: number } | null>(null);

  // Authoritative Data Fetching
  const loadBusinessData = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const [profData, invData, foreData, propData] = await Promise.all([
        fetchBusinessProfile(),
        fetchBusinessInvoices(),
        fetchCashflowForecast(30),
        fetchProposals('BUSINESS'),
      ]);
      setProfile(profData);
      setInvoices(invData);
      setForecast(foreData);
      setProposals(propData);
    } catch (err: any) {
      console.error('Failed to load business data from backend:', err);
      setLoadError(err.message || 'Unable to connect to FLOW Intelligence Engine.');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadBusinessData();
  }, [loadBusinessData]);

  // AI Analysis Query Handler
  const handleRunAnalysis = async () => {
    if (!query.trim()) return;
    setIsAnalyzing(true);
    try {
      const res = await explainBusinessCashflow(query);
      setAnalysisResult(res);
    } catch (err) {
      console.error('Explainable AI failed:', err);
    } finally {
      setIsAnalyzing(false);
    }
  };

  // Affordability Check Handler for Modal
  const handleCheckAffordability = async (amountCents: number) => {
    if (isNaN(amountCents) || amountCents <= 0) {
      setAffordabilityStatus(null);
      return;
    }
    setIsCheckingAffordability(true);
    try {
      const res = await checkBusinessAffordability(amountCents);
      setAffordabilityStatus(res);
    } catch (err) {
      console.error('Affordability check failed:', err);
    } finally {
      setIsCheckingAffordability(false);
    }
  };

  const handleOpenProposalModal = () => {
    setSelectedSupplierIdx(0);
    setCustomTitle(PRESET_SUPPLIERS[0].defaultTitle);
    setCustomAmountStr(PRESET_SUPPLIERS[0].defaultAmount.toString());
    setCustomCategory(PRESET_SUPPLIERS[0].category);
    setProposalSubmitError(null);
    setIsModalOpen(true);
    handleCheckAffordability(PRESET_SUPPLIERS[0].unitPriceCents);
  };

  const handleSupplierChange = (idx: number) => {
    setSelectedSupplierIdx(idx);
    const supplier = PRESET_SUPPLIERS[idx];
    setCustomTitle(supplier.defaultTitle);
    setCustomAmountStr(supplier.defaultAmount.toString());
    setCustomCategory(supplier.category);
    handleCheckAffordability(supplier.unitPriceCents);
  };

  const handleAmountInputChange = (valStr: string) => {
    setCustomAmountStr(valStr);
    const numeric = parseFloat(valStr);
    if (!isNaN(numeric) && numeric > 0) {
      handleCheckAffordability(Math.round(numeric * 100));
    } else {
      setAffordabilityStatus(null);
    }
  };

  const handleCreateProposalSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const amountVal = parseFloat(customAmountStr);
    if (isNaN(amountVal) || amountVal <= 0) {
      setProposalSubmitError('Please enter a valid expense amount.');
      return;
    }
    const unitPriceCents = Math.round(amountVal * 100);
    setIsSubmittingProposal(true);
    setProposalSubmitError(null);

    try {
      const supplier = PRESET_SUPPLIERS[selectedSupplierIdx];
      const payload = {
        title: customTitle,
        merchant_name: supplier.id === 'custom' ? 'Custom Supplier' : supplier.name,
        merchant_id: supplier.id === 'custom' ? 'm-custom-01' : supplier.id,
        items: [
          {
            product_id: supplier.id === 'custom' ? 'sku-custom' : supplier.sku,
            name: customTitle,
            quantity: 1,
            unit_price_cents: unitPriceCents,
            category: customCategory,
            sku: supplier.sku,
          },
        ],
        subtotal_cents: unitPriceCents,
        tax_cents: 0,
        shipping_cents: 0,
        total_amount_cents: unitPriceCents,
        currency: 'USD',
        ai_reasoning: `Supplier purchase proposed via FLOW Intelligence. Evaluated against business safety reserve ($8,000.00 floor).`,
      };

      await createBusinessProposal(payload);
      setIsModalOpen(false);
      await loadBusinessData();
    } catch (err: any) {
      console.error('Failed to submit business proposal:', err);
      setProposalSubmitError(err.message || 'Universal Trust Engine rejected proposal.');
    } finally {
      setIsSubmittingProposal(false);
    }
  };

  // Proposal Approval (Mode A explicit sign-off)
  const handleApproveProposal = async (prop: TransactionProposal) => {
    setApprovingId(prop.id);
    setCheckoutError(null);
    try {
      await submitHumanApproval(prop.id, prop.fingerprint_sha256);
      await loadBusinessData();
    } catch (err: any) {
      console.error('Failed to approve proposal:', err);
      setCheckoutError(err.message || 'Failed to authorize proposal.');
    } finally {
      setApprovingId(null);
    }
  };

  // PayPal Sandbox Checkout Flow
  const handleProceedToPayPal = async (prop: TransactionProposal) => {
    setCheckingOutId(prop.id);
    setCheckoutError(null);
    try {
      const order = await createPayPalOrder(prop.id);
      setActiveCheckoutOrder(order);
      setActiveProposalDetails({
        title: prop.title,
        merchant: prop.merchant_name,
        itemsCount: prop.items.length,
      });
      await loadBusinessData();
    } catch (err: any) {
      console.error('Failed to initiate PayPal order:', err);
      setCheckoutError(err.message || 'PayPal order creation rejected.');
    } finally {
      setCheckingOutId(null);
    }
  };

  const handleCaptureSuccess = (_capture: PayPalCaptureResponse) => {
    loadBusinessData();
  };

  // Filter Invoices
  const filteredInvoices = invoices.filter((inv) => {
    const matchType = invoiceTypeFilter === 'ALL' || inv.invoice_type === invoiceTypeFilter;
    const matchStatus = invoiceStatusFilter === 'ALL' || inv.status === invoiceStatusFilter;
    return matchType && matchStatus;
  });

  return (
    <div className="space-y-6">
      {/* Header & Concept Banner */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 p-6 sm:p-7 rounded-3xl bg-[#171717] border border-[#242424] shadow-xl">
        <div className="space-y-1">
          <div className="flex items-center gap-2.5">
            <MeridianLogo variant="icon" size="xs" />
            <span className="px-2.5 py-0.5 rounded text-[10px] font-mono font-black bg-[#FFD91A] text-black uppercase tracking-widest">
              BUSINESS MODULE
            </span>
            <span className="text-xs font-mono text-[#D0F030] font-bold">FLOW AUTONOMY</span>
          </div>
          <h2 className="text-xl sm:text-2xl font-black text-white font-mono uppercase tracking-tight">
            FLOW Intelligence — B2B Financial Autonomy
          </h2>
          <p className="text-xs text-[#A3A3A3] max-w-2xl font-mono leading-relaxed">
            Autonomous runway forecasting, supplier obligation scheduling, and deterministic pre-dispatch affordability guards ($8,000.00 safety reserve).
          </p>
        </div>

        <div className="flex items-center gap-2.5 shrink-0">
          <button
            onClick={() => loadBusinessData()}
            disabled={isLoading}
            className="p-2.5 rounded-xl text-xs font-mono bg-[#0E0E0E] hover:bg-[#242424] text-white border border-[#242424] transition"
            title="Refresh business data"
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin text-[#F04400]' : 'text-[#FFD91A]'}`} />
          </button>
          <button
            onClick={handleOpenProposalModal}
            disabled={isLoading || !profile}
            className="px-5 py-2.5 rounded-xl bg-[#F04400] hover:bg-[#ff5714] text-black text-xs font-mono font-black uppercase tracking-wider transition flex items-center gap-2 shadow-lg shadow-[#F04400]/20"
          >
            <PlusCircle className="w-4 h-4 stroke-[3]" />
            <span>Propose Purchase</span>
          </button>
        </div>
      </div>

      {/* Global Error Banner */}
      {loadError && (
        <div className="p-4 rounded-2xl bg-[#2A0E08] border border-[#F04400] text-[#F04400] text-xs font-mono flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 text-[#F04400] flex-shrink-0" />
            <span>{loadError}</span>
          </div>
          <button
            onClick={() => loadBusinessData()}
            className="px-3 py-1.5 rounded-lg bg-[#F04400] text-black font-bold uppercase"
          >
            Retry Connection
          </button>
        </div>
      )}

      {/* Checkout Error Banner */}
      {checkoutError && (
        <div className="p-4 rounded-2xl bg-[#2A0E08] border border-[#F04400] text-[#F04400] text-xs font-mono flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-[#F04400] flex-shrink-0" />
            <span>{checkoutError}</span>
          </div>
          <button onClick={() => setCheckoutError(null)} className="text-white hover:text-[#FFD91A]">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Metrics Row (Live Authoritative Figures) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="Current Cash Buffer"
          value={isLoading || !profile ? '—' : `$${profile.operating_cash.toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
          subtitle={isLoading || !profile ? 'Loading...' : `Minimum Reserve: $${profile.minimum_safety_reserve.toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
          change={isLoading || !profile ? '' : `Runway: ${profile.estimated_runway_days ?? 0} days`}
          changeType="positive"
          icon={DollarSign}
          accent="orange"
        />
        <MetricCard
          title="Incoming Receivables"
          value={isLoading || !profile ? '—' : `$${(profile.total_unpaid_receivables_cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
          subtitle="Next 30 days"
          change={isLoading || !profile ? '' : `${invoices.filter((i) => i.invoice_type === 'RECEIVABLE' && i.status !== 'PAID').length} client invoices`}
          changeType="positive"
          icon={ArrowDownLeft}
          accent="lime"
        />
        <MetricCard
          title="Committed Payables"
          value={isLoading || !profile ? '—' : `$${(profile.total_unpaid_payables_cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
          subtitle="Next 30 days"
          change={isLoading || !profile ? '' : `${invoices.filter((i) => i.invoice_type === 'PAYABLE' && i.status !== 'PAID').length} obligations`}
          changeType="negative"
          icon={ArrowUpRight}
          accent="yellow"
        />
        <MetricCard
          title="Net Projected Liquidity"
          value={isLoading || !profile ? '—' : `$${(profile.projected_net_liquidity_cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
          subtitle="30-Day forecast outcome"
          change={
            isLoading || !forecast
              ? ''
              : `Safety margin: $${(forecast.safety_reserve_margin_cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2 })}`
          }
          changeType={forecast && forecast.safety_reserve_margin_cents >= 0 ? 'positive' : 'negative'}
          icon={TrendingUp}
          accent="default"
        />
      </div>

      {/* AI Affordability Query & Recommendation Callout */}
      <div className="rounded-3xl bg-[#171717] border border-[#242424] p-6 space-y-4 shadow-xl">
        <div className="flex items-center justify-between">
          <label className="text-xs font-mono font-black uppercase tracking-widest text-[#FFD91A] flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-[#F04400]" />
            Explainable AI Affordability Advisor
          </label>
          <span className="text-[10px] font-mono text-[#A3A3A3]">Powered by deterministic scenario modeling</span>
        </div>

        <div className="flex flex-col sm:flex-row gap-2.5">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="flex-1 bg-[#0E0E0E] border border-[#242424] rounded-xl px-4 py-3 text-xs text-white font-mono focus:outline-none focus:border-[#F04400] transition"
            placeholder="Ask a financial question regarding business runway or upcoming payments..."
          />
          <button
            onClick={handleRunAnalysis}
            disabled={isAnalyzing}
            className="px-5 py-3 rounded-xl bg-[#FFD91A] hover:bg-[#ffe040] text-black text-xs font-mono font-black uppercase tracking-wider transition flex items-center justify-center gap-2 disabled:opacity-50 shadow-md shrink-0"
          >
            {isAnalyzing ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin text-black" />
                <span>Calculating Runway...</span>
              </>
            ) : (
              <>
                <Sparkles className="w-4 h-4 text-black" />
                <span>Analyze Runway</span>
              </>
            )}
          </button>
        </div>

        {analysisResult && (
          <div className="p-5 rounded-2xl bg-[#0E0E0E] border border-[#FFD91A]/60 text-xs font-mono space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-[#D0F030] font-black uppercase">
                <ShieldCheck className="w-4 h-4" />
                <span>Deterministic FLOW Intelligence Assessment</span>
              </div>
              <span className="text-[10px] px-2.5 py-0.5 rounded bg-[#FFD91A] text-black font-black uppercase">
                Source: {analysisResult.source}
              </span>
            </div>

            <p className="text-[#D4D4D4] leading-relaxed font-sans text-xs">
              <strong className="text-[#FFD91A] font-mono uppercase">Executive Summary:</strong> {analysisResult.summary}
            </p>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-3 border-t border-[#242424] text-[11px]">
              <div>
                <span className="text-[#A3A3A3] block">Starting Balance:</span>
                <span className="text-white font-bold">{analysisResult.deterministic_figures.starting_balance_usd}</span>
              </div>
              <div>
                <span className="text-[#A3A3A3] block">Upcoming Payables:</span>
                <span className="text-[#F04400] font-bold">
                  {analysisResult.deterministic_figures.total_upcoming_payables_usd}
                </span>
              </div>
              <div>
                <span className="text-[#A3A3A3] block">Expected Receivables:</span>
                <span className="text-[#D0F030] font-bold">
                  {analysisResult.deterministic_figures.total_expected_receivables_usd}
                </span>
              </div>
              <div>
                <span className="text-[#A3A3A3] block">Estimated Runway:</span>
                <span className="text-[#FFD91A] font-black text-sm">{analysisResult.runway_days_projection} Days</span>
              </div>
            </div>

            {analysisResult.risks.length > 0 && (
              <div className="p-3 rounded-xl bg-[#2A0E08] border border-[#F04400] text-[#F04400] text-[11px] space-y-1">
                <span className="font-bold flex items-center gap-1.5 uppercase">
                  <AlertTriangle className="w-3.5 h-3.5 text-[#F04400]" /> Identified Financial Risks:
                </span>
                <ul className="list-disc list-inside text-[#D4D4D4]">
                  {analysisResult.risks.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </div>
            )}

            <p className="text-[10px] text-[#A3A3A3] italic">{analysisResult.disclaimer}</p>
          </div>
        )}
      </div>

      {/* Cashflow Chart & Invoices Split */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left 2 Cols: 30-Day Liquidity Projection */}
        <div className="lg:col-span-2 rounded-3xl bg-[#171717] border border-[#242424] p-6 shadow-xl space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-black text-white font-mono uppercase tracking-wider flex items-center gap-2">
                <Calendar className="w-4 h-4 text-[#F04400]" />
                30-Day Projected Cashflow Forecast
              </h3>
              <p className="text-xs text-[#A3A3A3] font-mono mt-0.5">
                Deterministic calculation combining liquid cash + scheduled invoices
              </p>
            </div>
            {forecast && (
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono px-2.5 py-1 rounded bg-[#0E0E0E] text-[#D4D4D4] border border-[#242424]">
                  Min: ${forecast.projected_minimum_cash_cents / 100}
                </span>
                <span
                  className={`text-[11px] font-mono px-2.5 py-1 rounded border font-black uppercase ${
                    forecast.safety_reserve_margin_cents >= 0
                      ? 'bg-[#0E0E0E] text-[#D0F030] border-[#D0F030]'
                      : 'bg-[#2A0E08] text-[#F04400] border-[#F04400]'
                  }`}
                >
                  Margin: ${forecast.safety_reserve_margin_cents / 100}
                </span>
              </div>
            )}
          </div>

          <div className="h-64 w-full pt-2">
            {isLoading ? (
              <div className="h-full flex items-center justify-center text-[#A3A3A3] font-mono text-xs">
                <Loader2 className="w-5 h-5 animate-spin mr-2 text-[#F04400]" />
                <span>Computing 30-Day Forecast...</span>
              </div>
            ) : forecast && forecast.forecast_days.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={forecast.forecast_days} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="balanceGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#F04400" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#F04400" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#242424" />
                  <XAxis dataKey="day" stroke="#A3A3A3" fontSize={11} fontFamily="monospace" />
                  <YAxis
                    stroke="#A3A3A3"
                    fontSize={11}
                    fontFamily="monospace"
                    tickFormatter={(val) => `$${val / 1000}k`}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#0E0E0E',
                      borderColor: '#242424',
                      color: '#FFFFFF',
                      fontSize: '12px',
                      fontFamily: 'monospace',
                      borderRadius: '12px',
                    }}
                    formatter={(value: any) => [`$${Number(value).toLocaleString()}`, 'Projected Balance']}
                  />
                  <ReferenceLine
                    y={forecast.minimum_safety_reserve}
                    stroke="#FFD91A"
                    strokeDasharray="4 4"
                    label={{
                      value: `Safety Floor ($${forecast.minimum_safety_reserve / 1000}k)`,
                      fill: '#FFD91A',
                      fontSize: 10,
                      position: 'insideTopLeft',
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="projected_balance"
                    stroke="#F04400"
                    strokeWidth={2.5}
                    fillOpacity={1}
                    fill="url(#balanceGrad)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full flex items-center justify-center text-[#A3A3A3] font-mono text-xs">
                No forecast data available
              </div>
            )}
          </div>
        </div>

        {/* Right Col: Invoices & Obligations */}
        <div className="rounded-3xl bg-[#171717] border border-[#242424] p-6 shadow-xl space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-black text-white font-mono uppercase tracking-wider flex items-center gap-2">
              <Receipt className="w-4 h-4 text-[#FFD91A]" />
              Invoice Runway
            </h3>
            <span className="text-xs font-mono text-[#A3A3A3]">{filteredInvoices.length} Items</span>
          </div>

          {/* Filter Pills */}
          <div className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-[#242424] text-[11px] font-mono">
            <div className="flex items-center gap-1">
              {(['ALL', 'RECEIVABLE', 'PAYABLE'] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setInvoiceTypeFilter(t)}
                  className={`px-2.5 py-1 rounded-lg transition font-mono uppercase text-[10px] ${
                    invoiceTypeFilter === t
                      ? 'bg-[#F04400] text-black font-black'
                      : 'text-[#A3A3A3] hover:text-white hover:bg-[#242424]'
                  }`}
                >
                  {t === 'ALL' ? 'All' : t === 'RECEIVABLE' ? 'Receivables' : 'Payables'}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1">
              {(['ALL', 'PENDING', 'OVERDUE', 'PAID'] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setInvoiceStatusFilter(s)}
                  className={`px-2 py-0.5 rounded text-[10px] transition font-mono uppercase ${
                    invoiceStatusFilter === s
                      ? 'bg-[#FFD91A] text-black font-black'
                      : 'text-[#A3A3A3] hover:text-white'
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
            {isLoading ? (
              <div className="py-8 text-center text-[#A3A3A3] font-mono text-xs">
                <Loader2 className="w-4 h-4 animate-spin mx-auto mb-2 text-[#F04400]" />
                <span>Loading Invoices...</span>
              </div>
            ) : filteredInvoices.length === 0 ? (
              <div className="py-8 text-center text-[#A3A3A3] font-mono text-xs">
                No invoices found matching criteria.
              </div>
            ) : (
              filteredInvoices.map((inv) => (
                <div
                  key={inv.id}
                  className="p-3.5 rounded-2xl bg-[#0E0E0E] border border-[#242424] text-xs font-mono space-y-2"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-white">{inv.counterparty}</span>
                    <StatusBadge
                      severity={inv.status === 'OVERDUE' ? 'ALERT' : inv.status === 'PAID' ? 'SUCCESS' : 'WARNING'}
                      text={inv.status}
                    />
                  </div>
                  <div className="flex items-center justify-between text-[#A3A3A3]">
                    <span>
                      {inv.invoice_type === 'RECEIVABLE' ? '+ Client Due' : '- Payable obligation'}
                    </span>
                    <span
                      className={`font-black ${
                        inv.invoice_type === 'RECEIVABLE' ? 'text-[#D0F030]' : 'text-white'
                      }`}
                    >
                      ${inv.amount.toFixed(2)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-[#A3A3A3] pt-1.5 border-t border-[#242424]">
                    <span>Due: {inv.due_date.substring(0, 10)}</span>
                    <span className="text-[#FFD91A] font-bold">{inv.category}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Supplier Purchase Proposals Section */}
      <div className="rounded-3xl bg-[#171717] border border-[#242424] p-6 shadow-xl space-y-4">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-black text-white font-mono uppercase tracking-wider flex items-center gap-2">
              <ShoppingCart className="w-4 h-4 text-[#D0F030]" />
              Supplier Purchase Proposals (FLOW Autonomy)
            </h3>
            <p className="text-xs text-[#A3A3A3] font-mono mt-0.5">
              Proposals evaluated under policy pol-003. Explicit human approval required before staging PayPal payment.
            </p>
          </div>
          <button
            onClick={handleOpenProposalModal}
            className="px-4 py-2 rounded-xl bg-[#F04400] hover:bg-[#ff5714] text-black text-xs font-mono font-black uppercase tracking-wider transition flex items-center gap-2 shadow-md"
          >
            <PlusCircle className="w-4 h-4 stroke-[3]" />
            <span>New Supplier Purchase</span>
          </button>
        </div>

        {/* Proposals List */}
        <div className="space-y-3.5 pt-2">
          {isLoading ? (
            <div className="py-8 text-center text-[#A3A3A3] font-mono text-xs">
              <Loader2 className="w-4 h-4 animate-spin mx-auto mb-2 text-[#F04400]" />
              <span>Loading Business Proposals...</span>
            </div>
          ) : proposals.length === 0 ? (
            <div className="p-8 rounded-2xl bg-[#0E0E0E] border border-dashed border-[#242424] text-center font-mono space-y-2">
              <ShoppingCart className="w-6 h-6 text-[#A3A3A3] mx-auto" />
              <p className="text-xs text-white font-bold uppercase">No active supplier purchase proposals</p>
              <p className="text-[11px] text-[#A3A3A3] max-w-md mx-auto">
                Use the &apos;Propose Purchase&apos; button above to evaluate a cloud infrastructure or dev-tool expense against
                the $8,000.00 safety reserve floor and policy pol-003.
              </p>
            </div>
          ) : (
            proposals.map((prop) => {
              const isAwaitingApproval = prop.state === 'AWAITING_APPROVAL';
              const isApproved = prop.state === 'APPROVED';
              const isPendingPayment = prop.state === 'PAYMENT_PENDING';
              const isPaid = prop.state === 'PAID';
              const isRejected = prop.state === 'REJECTED' || prop.policy_decision === 'DENY';

              return (
                <div
                  key={prop.id}
                  className="p-5 rounded-2xl bg-[#0E0E0E] border border-[#242424] text-xs font-mono space-y-3"
                >
                  <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
                    <div>
                      <div className="flex items-center gap-2.5">
                        <span className="font-black text-white text-sm uppercase">{prop.title}</span>
                        <StatusBadge
                          severity={
                            isPaid
                              ? 'SUCCESS'
                              : isApproved
                              ? 'SUCCESS'
                              : isRejected
                              ? 'ALERT'
                              : isAwaitingApproval
                              ? 'WARNING'
                              : 'INFO'
                          }
                          text={prop.state}
                        />
                      </div>
                      <p className="text-[11px] text-[#A3A3A3] mt-0.5">
                        Supplier: <strong className="text-white">{prop.merchant_name}</strong> • Module: {prop.module}
                      </p>
                    </div>
                    <div className="text-right">
                      <span className="text-base font-black text-[#D0F030]">${(prop.total ?? 0).toFixed(2)}</span>
                      <span className="text-[10px] text-[#A3A3A3] block uppercase">{prop.currency}</span>
                    </div>
                  </div>

                  {/* Fingerprint & Policy Status */}
                  <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-[#242424] text-[11px]">
                    <FingerprintPill hash={prop.fingerprint_sha256} />
                    {prop.policy_decision && (
                      <span
                        className={`px-2.5 py-0.5 rounded text-[10px] font-black uppercase ${
                          prop.policy_decision === 'DENY'
                            ? 'bg-[#2A0E08] text-[#F04400] border border-[#F04400]'
                            : 'bg-[#FFD91A] text-black'
                        }`}
                      >
                        POLICY: {prop.policy_decision}
                      </span>
                    )}
                    {prop.paypal_order_id && (
                      <span className="px-2.5 py-0.5 rounded text-[10px] font-bold bg-[#171717] text-white border border-[#242424]">
                        PayPal: #{prop.paypal_order_id}
                      </span>
                    )}
                  </div>

                  {/* Action Banners */}
                  {isAwaitingApproval && (
                    <div className="p-3.5 rounded-xl bg-[#1A180E] border border-[#FFD91A] flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
                      <div className="text-[#FFD91A] text-[11px] space-y-0.5">
                        <span className="font-black flex items-center gap-1.5 uppercase">
                          <Lock className="w-3.5 h-3.5 text-[#FFD91A]" />
                          Explicit Human Authorization Required (Mode A)
                        </span>
                        <p className="text-[#D4D4D4] text-[10px]">
                          Affordability verified. Conforms to pol-003. Ready for human administrator confirmation.
                        </p>
                      </div>
                      <button
                        onClick={() => handleApproveProposal(prop)}
                        disabled={approvingId === prop.id}
                        className="px-4 py-2 rounded-xl bg-[#FFD91A] hover:bg-[#ffe040] text-black font-black text-xs uppercase tracking-wider transition flex items-center gap-2 flex-shrink-0 disabled:opacity-50 shadow-md"
                      >
                        {approvingId === prop.id ? (
                          <Loader2 className="w-4 h-4 animate-spin text-black" />
                        ) : (
                          <CheckCircle2 className="w-4 h-4 stroke-[2.5]" />
                        )}
                        <span>Approve Expense</span>
                      </button>
                    </div>
                  )}

                  {isApproved && (
                    <div className="p-3.5 rounded-xl bg-[#171717] border border-[#F04400] flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
                      <div className="text-white text-[11px] space-y-0.5">
                        <span className="font-black flex items-center gap-1.5 text-[#F04400] uppercase">
                          <ShieldCheck className="w-4 h-4 text-[#F04400]" />
                          Approved by Administrator • Ready for PayPal Sandbox
                        </span>
                        <p className="text-[#A3A3A3] text-[10px]">
                          Cryptographic SHA-256 fingerprint bound. Proceed to stage verified payment order.
                        </p>
                      </div>
                      <button
                        onClick={() => handleProceedToPayPal(prop)}
                        disabled={checkingOutId === prop.id}
                        className="px-4 py-2 rounded-xl bg-[#F04400] hover:bg-[#ff5714] text-black font-black text-xs uppercase tracking-wider transition flex items-center gap-2 flex-shrink-0 disabled:opacity-50 shadow-md"
                      >
                        {checkingOutId === prop.id ? (
                          <Loader2 className="w-4 h-4 animate-spin text-black" />
                        ) : (
                          <ShoppingCart className="w-4 h-4 stroke-[2.5]" />
                        )}
                        <span>PayPal Sandbox Checkout</span>
                      </button>
                    </div>
                  )}

                  {isPendingPayment && (
                    <div className="p-3.5 rounded-xl bg-[#171717] border border-[#FFD91A] flex items-center justify-between">
                      <span className="text-[11px] text-[#FFD91A] font-bold flex items-center gap-2">
                        <Loader2 className="w-4 h-4 animate-spin text-[#FFD91A]" />
                        Payment Order Active (#{prop.paypal_order_id})
                      </span>
                      <button
                        onClick={() => handleProceedToPayPal(prop)}
                        className="px-4 py-1.5 rounded-xl bg-[#FFD91A] text-black text-xs font-black uppercase shadow-md"
                      >
                        Complete Sandbox Checkout
                      </button>
                    </div>
                  )}

                  {isPaid && (
                    <div className="p-3 rounded-xl bg-[#0E0E0E] border border-[#D0F030] text-[11px] text-[#D0F030] flex items-center gap-2 font-bold uppercase">
                      <CheckCircle2 className="w-4 h-4 text-[#D0F030] flex-shrink-0" />
                      <span>
                        Verified Payment Captured (#{prop.paypal_capture_id || 'CAP-VERIFIED'}) • Deducted from operating liquidity.
                      </span>
                    </div>
                  )}

                  {isRejected && (
                    <div className="p-3 rounded-xl bg-[#2A0E08] border border-[#F04400] text-[11px] text-[#F04400] flex items-center gap-2 font-bold uppercase">
                      <ShieldAlert className="w-4 h-4 text-[#F04400] flex-shrink-0" />
                      <span>
                        SECURITY DENIAL: Prohibited by Policy pol-003 or Gate A Affordability. Execution blocked.
                      </span>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Propose Business Purchase Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/85 backdrop-blur-md">
          <div className="bg-[#171717] border border-[#242424] rounded-3xl max-w-lg w-full p-6 space-y-4 shadow-2xl">
            <div className="flex items-center justify-between border-b border-[#242424] pb-3">
              <div className="flex items-center gap-2">
                <ShoppingCart className="w-5 h-5 text-[#F04400]" />
                <h3 className="text-sm font-black text-white font-mono uppercase">Propose Supplier Purchase (FLOW)</h3>
              </div>
              <button
                onClick={() => setIsModalOpen(false)}
                className="text-[#A3A3A3] hover:text-white transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleCreateProposalSubmit} className="space-y-4 text-xs font-mono">
              {/* Supplier Dropdown */}
              <div className="space-y-1">
                <label className="text-white block font-black uppercase">Approved Supplier (Policy pol-003):</label>
                <select
                  value={selectedSupplierIdx}
                  onChange={(e) => handleSupplierChange(parseInt(e.target.value))}
                  className="w-full bg-[#0E0E0E] border border-[#242424] rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-[#F04400] transition"
                >
                  {PRESET_SUPPLIERS.map((s, idx) => (
                    <option key={s.id} value={idx}>
                      {s.name} ({s.category})
                    </option>
                  ))}
                </select>
              </div>

              {/* Purchase Description */}
              <div className="space-y-1">
                <label className="text-white block font-black uppercase">Purchase Description / SKU Name:</label>
                <input
                  type="text"
                  value={customTitle}
                  onChange={(e) => setCustomTitle(e.target.value)}
                  required
                  className="w-full bg-[#0E0E0E] border border-[#242424] rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-[#F04400] transition"
                />
              </div>

              {/* Amount & Category Grid */}
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="text-white block font-black uppercase">Amount (USD):</label>
                  <div className="relative">
                    <span className="absolute left-3 top-2.5 text-[#A3A3A3] font-bold">$</span>
                    <input
                      type="number"
                      step="0.01"
                      min="1.00"
                      value={customAmountStr}
                      onChange={(e) => handleAmountInputChange(e.target.value)}
                      required
                      className="w-full bg-[#0E0E0E] border border-[#242424] rounded-xl pl-7 pr-3 py-2.5 text-white focus:outline-none focus:border-[#F04400] transition"
                    />
                  </div>
                </div>

                <div className="space-y-1">
                  <label className="text-white block font-black uppercase">Category:</label>
                  <input
                    type="text"
                    value={customCategory}
                    onChange={(e) => setCustomCategory(e.target.value)}
                    required
                    className="w-full bg-[#0E0E0E] border border-[#242424] rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-[#F04400] transition"
                  />
                </div>
              </div>

              {/* Live Gate A Affordability Simulation Callout */}
              <div
                className={`p-3.5 rounded-2xl border text-[11px] space-y-1.5 ${
                  isCheckingAffordability
                    ? 'bg-[#0E0E0E] border-[#242424] text-[#A3A3A3]'
                    : affordabilityStatus && !affordabilityStatus.violates_safety_reserve
                    ? 'bg-[#0E0E0E] border-[#D0F030] text-[#D0F030]'
                    : 'bg-[#2A0E08] border-[#F04400] text-[#F04400]'
                }`}
              >
                <div className="flex items-center justify-between font-black uppercase">
                  <span className="flex items-center gap-1.5">
                    {isCheckingAffordability ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : affordabilityStatus && !affordabilityStatus.violates_safety_reserve ? (
                      <ShieldCheck className="w-4 h-4 text-[#D0F030]" />
                    ) : (
                      <ShieldAlert className="w-4 h-4 text-[#F04400]" />
                    )}
                    Gate A: Deterministic Affordability Verification
                  </span>
                  <span className="text-[10px] font-mono text-white">
                    Floor: $8,000.00
                  </span>
                </div>

                {affordabilityStatus && (
                  <p className="text-[10px] leading-relaxed font-sans text-[#D4D4D4]">
                    {affordabilityStatus.reason}
                  </p>
                )}
              </div>

              {proposalSubmitError && (
                <div className="p-3 rounded-xl bg-[#2A0E08] border border-[#F04400] text-[#F04400] text-[11px]">
                  {proposalSubmitError}
                </div>
              )}

              {/* Submit Buttons */}
              <div className="flex items-center justify-end gap-2.5 pt-3 border-t border-[#242424]">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="px-4 py-2 rounded-xl bg-[#242424] hover:bg-[#333333] text-white font-bold transition text-xs uppercase"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={
                    isSubmittingProposal ||
                    isCheckingAffordability ||
                    (affordabilityStatus !== null && affordabilityStatus.violates_safety_reserve)
                  }
                  className="px-5 py-2 rounded-xl bg-[#F04400] hover:bg-[#ff5714] disabled:opacity-40 text-black font-black transition flex items-center gap-2 text-xs uppercase tracking-wider shadow-lg"
                >
                  {isSubmittingProposal ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin text-black" />
                      <span>Evaluating Policy...</span>
                    </>
                  ) : (
                    <>
                      <ShieldCheck className="w-3.5 h-3.5 stroke-[2.5]" />
                      <span>Submit for Policy Check</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* PayPal Sandbox Checkout Modal */}
      {activeCheckoutOrder && (
        <PayPalCheckoutModal
          order={activeCheckoutOrder}
          proposalTitle={activeProposalDetails?.title || 'Business Software Proposal'}
          merchantName={activeProposalDetails?.merchant || 'Supplier'}
          itemsCount={activeProposalDetails?.itemsCount || 1}
          onClose={() => setActiveCheckoutOrder(null)}
          onSuccess={handleCaptureSuccess}
        />
      )}
    </div>
  );
};
