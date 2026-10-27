"use client";

import React, { useEffect, useState, useMemo, useCallback } from "react";
import { adminFetch } from "@/lib/api";
import {
  Receipt,
  Search,
  RefreshCw,
  Phone,
  Clock,
  Sparkles,
  ChevronLeft,
  ChevronRight,
  DollarSign,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  HelpCircle,
  Layers,
  Cpu,
  Bot,
  Volume2,
  Mic,
  RotateCcw,
} from "lucide-react";

interface CustomerOption {
  id: number;
  company_name: string;
  contact_email: string;
  contact_name: string;
  status: string;
  plan: string;
  currency: string;
  total_calls: number;
  total_duration_seconds: number;
  total_revenue_inr: number;
}

interface CallItem {
  id: number;
  dograh_run_id: number;
  customer_id: number;
  agent_id?: number | null;
  called_at: string | null;
  duration_seconds: number;
  cost_to_customer_paise: number;
  revenue_inr: number;
  caller_number: string | null;
  direction: string;
  plan?: string | null;
  tts_provider?: string | null;
  status: string;
}

interface CostBreakdown {
  plivo: {
    minutes: number;
    rate_inr: number;
    cost_inr: number;
  };
  stt: {
    provider: string;
    model: string;
    seconds: number;
    rate_usd_min?: number | null;
    rate_per_min?: number;
    cost_inr: number;
  };
  llm_conversation: {
    provider: string;
    model: string;
    prompt_tokens: number;
    completion_tokens: number;
    cached_tokens: number;
    cost_inr: number;
  };
  llm_extraction: {
    provider: string;
    model: string;
    prompt_tokens: number;
    completion_tokens: number;
    cost_inr: number;
  };
  tts: {
    provider: string;
    model: string;
    characters: number;
    rate_usd_per_10k?: number | null;
    rate_per_1k?: number;
    cost_inr: number;
  };
}

interface AuditRunResponse {
  dograh_run_id: number;
  customer_id: number | null;
  company_name: string | null;
  call_duration_seconds: number;
  billed_minutes: number;
  revenue_inr: number;
  effective_rate_per_min_inr: number;
  exchange_rate_used: number;
  cost_breakdown: CostBreakdown;
  total_cost_inr: number;
  cost_per_minute_inr: number;
  profit_inr: number;
  margin_pct: number;
  margin_status: "healthy" | "warning" | "loss" | "free_trial" | string;
  post_call_breakdown?: Record<string, unknown>;
  post_call_cost_inr?: number;
  total_all_in_cost_inr?: number;
  notices?: string[];
}

interface SimulationDelta {
  total_cost_delta_inr: number;
  cost_per_minute_delta_inr: number;
  profit_delta_inr: number;
  margin_pct_delta: number;
}

interface SimulationResponse {
  dograh_run_id: number;
  baseline: AuditRunResponse;
  simulated: AuditRunResponse;
  delta: SimulationDelta;
}

const inr = (v: number | undefined | null) =>
  `₹${(v || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const usd = (v: number | undefined | null) =>
  `$${(v || 0).toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 4 })}`;

const formatDuration = (seconds: number) => {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (mins === 0) return `${secs}s`;
  return `${mins}m ${secs.toString().padStart(2, "0")}s`;
};

export default function CallCostAuditPage() {
  // State: Customers
  const [customers, setCustomers] = useState<CustomerOption[]>([]);
  const [selectedCustomerId, setSelectedCustomerId] = useState<number | null>(null);
  const [loadingCustomers, setLoadingCustomers] = useState(true);

  // State: Exchange Rate
  const [forexMode, setForexMode] = useState<"88" | "95.10" | "custom">("88");
  const [customForex, setCustomForex] = useState("88.00");

  const effectiveForex = useMemo(() => {
    if (forexMode === "88") return 88.0;
    if (forexMode === "95.10") return 95.10;
    const parsed = parseFloat(customForex);
    return isNaN(parsed) || parsed <= 0 ? 88.0 : parsed;
  }, [forexMode, customForex]);

  // State: Call List
  const [calls, setCalls] = useState<CallItem[]>([]);
  const [totalCalls, setTotalCalls] = useState(0);
  const [callPage, setCallPage] = useState(1);
  const [callSearch, setCallSearch] = useState("");
  const [loadingCalls, setLoadingCalls] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);

  // State: Audit Detail
  const [auditDetail, setAuditDetail] = useState<AuditRunResponse | null>(null);
  const [loadingAudit, setLoadingAudit] = useState(false);

  // State: What-If Simulation
  const [simTTS, setSimTTS] = useState<string>("");
  const [simLLM, setSimLLM] = useState<string>("");
  const [simSTT, setSimSTT] = useState<string>("");
  const [simulation, setSimulation] = useState<SimulationResponse | null>(null);
  const [loadingSim, setLoadingSim] = useState(false);

  // 1. Fetch Customers
  const fetchCustomers = useCallback(async () => {
    setLoadingCustomers(true);
    try {
      const res = await adminFetch("/admin/call-audit/customers?limit=500");
      if (res.ok) {
        const data = await res.json();
        setCustomers(data.customers || []);
        if (data.customers && data.customers.length > 0) {
          setSelectedCustomerId((prev) => prev ?? data.customers[0].id);
        }
      }
    } catch (err) {
      console.error("Failed to fetch customers:", err);
    } finally {
      setLoadingCustomers(false);
    }
  }, []);

  useEffect(() => {
    let ignore = false;
    async function init() {
      try {
        const res = await adminFetch("/admin/call-audit/customers?limit=500");
        if (res.ok && !ignore) {
          const data = await res.json();
          setCustomers(data.customers || []);
          if (data.customers && data.customers.length > 0) {
            setSelectedCustomerId((prev) => prev ?? data.customers[0].id);
          }
        }
      } catch (err) {
        console.error("Failed to load customers:", err);
      } finally {
        if (!ignore) setLoadingCustomers(false);
      }
    }
    init();
    return () => {
      ignore = true;
    };
  }, []);

  // 2. Fetch Calls for Selected Customer
  const fetchCalls = useCallback(async () => {
    if (!selectedCustomerId) return;
    setLoadingCalls(true);
    try {
      let url = `/admin/call-audit/calls?customer_id=${selectedCustomerId}&page=${callPage}&limit=15`;
      if (callSearch.trim()) {
        url += `&search=${encodeURIComponent(callSearch.trim())}`;
      }
      const res = await adminFetch(url);
      if (res.ok) {
        const data = await res.json();
        setCalls(data.calls || []);
        setTotalCalls(data.total || 0);
        if (data.calls && data.calls.length > 0) {
          setSelectedRunId((prev) => prev ?? data.calls[0].dograh_run_id);
        }
      }
    } catch (err) {
      console.error("Failed to fetch calls:", err);
    } finally {
      setLoadingCalls(false);
    }
  }, [selectedCustomerId, callPage, callSearch]);

  useEffect(() => {
    if (!selectedCustomerId) return;
    let ignore = false;
    async function loadCalls() {
      try {
        let url = `/admin/call-audit/calls?customer_id=${selectedCustomerId}&page=${callPage}&limit=15`;
        if (callSearch.trim()) {
          url += `&search=${encodeURIComponent(callSearch.trim())}`;
        }
        const res = await adminFetch(url);
        if (res.ok && !ignore) {
          const data = await res.json();
          setCalls(data.calls || []);
          setTotalCalls(data.total || 0);
          if (data.calls && data.calls.length > 0) {
            setSelectedRunId((prev) => prev ?? data.calls[0].dograh_run_id);
          }
        }
      } catch (err) {
        console.error("Failed to load calls:", err);
      }
    }
    loadCalls();
    return () => {
      ignore = true;
    };
  }, [selectedCustomerId, callPage, callSearch]);

  // 3. Fetch Single Run Audit Itemized Receipt
  const fetchAuditDetail = useCallback(async () => {
    if (!selectedRunId) return;
    setLoadingAudit(true);
    setSimulation(null);
    setSimTTS("");
    setSimLLM("");
    setSimSTT("");
    try {
      const res = await adminFetch(
        `/admin/call-audit/run/${selectedRunId}?exchange_rate=${effectiveForex}`
      );
      if (res.ok) {
        const data = await res.json();
        setAuditDetail(data);
      }
    } catch (err) {
      console.error("Failed to fetch audit detail:", err);
    } finally {
      setLoadingAudit(false);
    }
  }, [selectedRunId, effectiveForex]);

  useEffect(() => {
    if (!selectedRunId) return;
    let ignore = false;
    async function loadAudit() {
      try {
        const res = await adminFetch(
          `/admin/call-audit/run/${selectedRunId}?exchange_rate=${effectiveForex}`
        );
        if (res.ok && !ignore) {
          const data = await res.json();
          setAuditDetail(data);
        }
      } catch (err) {
        console.error("Failed to load audit:", err);
      }
    }
    loadAudit();
    return () => {
      ignore = true;
    };
  }, [selectedRunId, effectiveForex]);

  // 4. Run Simulation when model switches
  const handleSimulate = async (tts?: string, llm?: string, stt?: string) => {
    if (!selectedRunId) return;
    setLoadingSim(true);
    try {
      const payload: Record<string, string | number | boolean> = {
        dograh_run_id: selectedRunId,
        override_usd_inr: effectiveForex,
      };
      if (tts) payload.override_tts_provider = tts;
      if (llm) payload.override_llm_model = llm;
      if (stt) payload.override_stt_provider = stt;

      const res = await adminFetch("/admin/call-audit/simulate", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data: SimulationResponse = await res.json();
        setSimulation(data);
      }
    } catch (err) {
      console.error("Simulation failed:", err);
    } finally {
      setLoadingSim(false);
    }
  };

  const handleResetSimulation = () => {
    setSimulation(null);
    setSimTTS("");
    setSimLLM("");
    setSimSTT("");
  };

  const selectedCustomer = useMemo(
    () => customers.find((c) => c.id === selectedCustomerId),
    [customers, selectedCustomerId]
  );

  // Calculations for Stacked Bar
  const displayAudit = simulation ? simulation.simulated : auditDetail;
  const cb = displayAudit?.cost_breakdown;
  const totalCost = displayAudit?.total_cost_inr || 0;

  const costSegments = useMemo(() => {
    if (!cb || totalCost <= 0) return [];
    return [
      {
        id: "plivo",
        label: "Plivo Telephony",
        cost: cb.plivo.cost_inr,
        pct: (cb.plivo.cost_inr / totalCost) * 100,
        color: "#3b82f6", // blue
        bg: "bg-blue-500",
      },
      {
        id: "stt",
        label: "Speech-to-Text",
        cost: cb.stt.cost_inr,
        pct: (cb.stt.cost_inr / totalCost) * 100,
        color: "#a855f7", // purple
        bg: "bg-purple-500",
      },
      {
        id: "llm_conv",
        label: "Conversational LLM",
        cost: cb.llm_conversation.cost_inr,
        pct: (cb.llm_conversation.cost_inr / totalCost) * 100,
        color: "#10b981", // emerald
        bg: "bg-emerald-500",
      },
      {
        id: "llm_ext",
        label: "Extraction LLM",
        cost: cb.llm_extraction.cost_inr,
        pct: (cb.llm_extraction.cost_inr / totalCost) * 100,
        color: "#06b6d4", // cyan
        bg: "bg-cyan-500",
      },
      {
        id: "tts",
        label: "Text-to-Speech",
        cost: cb.tts.cost_inr,
        pct: (cb.tts.cost_inr / totalCost) * 100,
        color: "#f59e0b", // amber
        bg: "bg-amber-500",
      },
    ].filter((s) => s.cost > 0);
  }, [cb, totalCost]);

  // Margin status styles
  const marginBadge = useMemo(() => {
    if (!displayAudit) return null;
    const m = displayAudit.margin_pct;
    const profit = displayAudit.profit_inr;
    const status = displayAudit.margin_status;

    if (status === "free_trial" || displayAudit.revenue_inr <= 0) {
      return {
        bg: "bg-sky-50 border-sky-200 text-sky-800",
        label: "FREE TRIAL / INTERNAL TEST",
        sub: `Operating Cost: ${inr(displayAudit.total_cost_inr)} (Zero Billed Revenue)`,
        icon: HelpCircle,
      };
    }
    if (m >= 40.0) {
      return {
        bg: "bg-emerald-50 border-emerald-200 text-emerald-800",
        label: `+${m.toFixed(1)}% GROSS MARGIN`,
        sub: `${inr(profit)} Net Profit`,
        icon: CheckCircle2,
      };
    }
    if (m >= 15.0) {
      return {
        bg: "bg-amber-50 border-amber-200 text-amber-800",
        label: `+${m.toFixed(1)}% SLIM MARGIN`,
        sub: `${inr(profit)} Net Profit`,
        icon: AlertTriangle,
      };
    }
    return {
      bg: "bg-red-50 border-red-200 text-red-800",
      label: `${m.toFixed(1)}% MARGIN LOSS`,
      sub: `Operating at -${inr(Math.abs(profit))} Loss`,
      icon: XCircle,
    };
  }, [displayAudit]);

  return (
    <div className="space-y-6 pb-12">
      {/* ── 1. TOP CONTROL BAR ────────────────────────────────────── */}
      <div className="bg-white rounded-2xl border border-zinc-200/80 p-4 shadow-sm space-y-4">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          {/* Organization Combobox & Title */}
          <div className="flex flex-wrap items-center gap-3">
            <div className="h-10 w-10 rounded-xl bg-[#fe6905]/10 border border-[#fe6905]/20 flex items-center justify-center text-[#fe6905]">
              <Receipt className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-base font-bold text-zinc-900 tracking-tight">
                  Call Cost Audit & Margin Inspector
                </h1>
                <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
                  Zero Guesswork Standard
                </span>
              </div>
              <p className="text-xs text-zinc-500">
                Audited actuals, per-pulse carrier margins, and real-time model simulator
              </p>
            </div>
          </div>

          {/* Controls: Org Select & Forex Picker */}
          <div className="flex flex-wrap items-center gap-3">
            {/* Organization Selector */}
            <div className="relative min-w-[240px]">
              <select
                aria-label="Select Customer Organization"
                value={selectedCustomerId || ""}
                onChange={(e) => {
                  setSelectedCustomerId(Number(e.target.value));
                  setCallPage(1);
                  setSelectedRunId(null);
                }}
                className="w-full bg-zinc-50 border border-zinc-200 rounded-lg px-3 py-2 text-xs font-medium text-zinc-800 focus:outline-none focus:ring-2 focus:ring-[#fe6905]/30 focus:border-[#fe6905] transition-all cursor-pointer"
              >
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.company_name} ({c.total_calls} calls • {c.plan})
                  </option>
                ))}
              </select>
            </div>

            {/* Exchange Rate Selector */}
            <div className="flex items-center bg-zinc-100/80 p-1 rounded-xl border border-zinc-200/70 text-xs">
              <span className="text-[11px] font-semibold text-zinc-500 px-2 flex items-center gap-1">
                <DollarSign className="w-3.5 h-3.5 text-zinc-400" />
                FX:
              </span>
              <button
                type="button"
                onClick={() => setForexMode("88")}
                className={`px-2.5 py-1 rounded-lg font-medium transition-all ${
                  forexMode === "88"
                    ? "bg-white text-zinc-900 shadow-sm border border-zinc-200/80"
                    : "text-zinc-500 hover:text-zinc-800"
                }`}
              >
                ₹88.00 (Base)
              </button>
              <button
                type="button"
                onClick={() => setForexMode("95.10")}
                className={`px-2.5 py-1 rounded-lg font-medium transition-all ${
                  forexMode === "95.10"
                    ? "bg-white text-zinc-900 shadow-sm border border-zinc-200/80"
                    : "text-zinc-500 hover:text-zinc-800"
                }`}
              >
                ₹95.10 (Live)
              </button>
              <button
                type="button"
                onClick={() => setForexMode("custom")}
                className={`px-2.5 py-1 rounded-lg font-medium transition-all ${
                  forexMode === "custom"
                    ? "bg-white text-zinc-900 shadow-sm border border-zinc-200/80"
                    : "text-zinc-500 hover:text-zinc-800"
                }`}
              >
                Custom
              </button>

              {forexMode === "custom" && (
                <input
                  type="number"
                  step="0.1"
                  value={customForex}
                  onChange={(e) => setCustomForex(e.target.value)}
                  className="w-16 ml-1 px-1.5 py-0.5 bg-white border border-zinc-300 rounded text-xs font-semibold text-zinc-900 focus:outline-none focus:ring-1 focus:ring-[#fe6905]"
                  placeholder="88.0"
                />
              )}
            </div>

            {/* Refresh Button */}
            <button
              onClick={() => {
                fetchCustomers();
                fetchCalls();
                fetchAuditDetail();
              }}
              className="p-2 rounded-lg border border-zinc-200 bg-zinc-50 hover:bg-zinc-100 text-zinc-600 transition-colors"
              title="Refresh Audit Data"
            >
              <RefreshCw
                className={`w-4 h-4 ${loadingCustomers || loadingCalls || loadingAudit ? "animate-spin" : ""}`}
              />
            </button>
          </div>
        </div>

        {/* Selected Org Snapshot Strip */}
        {selectedCustomer && (
          <div className="pt-3 border-t border-zinc-100 flex flex-wrap items-center justify-between text-xs text-zinc-500 gap-2">
            <div className="flex items-center gap-3">
              <span className="font-semibold text-zinc-800">{selectedCustomer.company_name}</span>
              <span className="text-zinc-300">•</span>
              <span>Account #{selectedCustomer.id}</span>
              <span className="text-zinc-300">•</span>
              <span className="px-2 py-0.5 rounded bg-zinc-100 text-zinc-700 font-semibold uppercase text-[10px]">
                {selectedCustomer.plan} Plan
              </span>
              <span className="text-zinc-300">•</span>
              <span>Billing Currency: {selectedCustomer.currency || "INR"}</span>
            </div>
            <div className="flex items-center gap-4">
              <span>
                Total Logged Calls: <strong className="text-zinc-800">{selectedCustomer.total_calls}</strong>
              </span>
              <span>
                Audited Revenue:{" "}
                <strong className="text-zinc-800">{inr(selectedCustomer.total_revenue_inr)}</strong>
              </span>
            </div>
          </div>
        )}
      </div>

      {/* ── 2. DUAL PANEL LAYOUT: CALL LOGS (LEFT) vs ITEMIZED RECEIPT (RIGHT) ── */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* LEFT PANEL: CALL HISTORY TABLE (5 Cols) */}
        <div className="lg:col-span-5 bg-white rounded-2xl border border-zinc-200/80 p-4 shadow-sm space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-bold uppercase tracking-wider text-zinc-500 flex items-center gap-2">
              <Phone className="w-3.5 h-3.5 text-zinc-400" />
              Call Runs ({totalCalls})
            </h2>
            <span className="text-[11px] text-zinc-400">Click row to inspect</span>
          </div>

          {/* Search Box */}
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-2.5 text-zinc-400" />
            <input
              type="text"
              placeholder="Search by Run ID or Phone Number..."
              value={callSearch}
              onChange={(e) => {
                setCallSearch(e.target.value);
                setCallPage(1);
              }}
              className="w-full bg-zinc-50 border border-zinc-200 rounded-lg pl-9 pr-3 py-2 text-xs text-zinc-800 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-[#fe6905]/30 focus:border-[#fe6905] transition-all"
            />
          </div>

          {/* Call List */}
          <div className="divide-y divide-zinc-100 overflow-y-auto max-h-[620px] rounded-xl border border-zinc-100">
            {loadingCalls ? (
              <div className="py-12 text-center text-xs text-zinc-400">
                <RefreshCw className="w-5 h-5 animate-spin mx-auto mb-2 text-zinc-300" />
                Loading call logs...
              </div>
            ) : calls.length === 0 ? (
              <div className="py-12 text-center text-xs text-zinc-400">
                No calls found for this account.
              </div>
            ) : (
              calls.map((call) => {
                const isSelected = selectedRunId === call.dograh_run_id;
                const isConnected = call.duration_seconds > 0;
                return (
                  <div
                    key={call.id}
                    onClick={() => setSelectedRunId(call.dograh_run_id)}
                    className={`p-3 transition-all cursor-pointer relative ${
                      isSelected
                        ? "bg-[#fe6905]/5 border-l-4 border-l-[#fe6905]"
                        : "hover:bg-zinc-50/80 border-l-4 border-l-transparent"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-bold text-zinc-900">
                          #{call.dograh_run_id}
                        </span>
                        <span
                          className={`text-[9px] px-1.5 py-0.5 rounded font-bold uppercase ${
                            isConnected
                              ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                              : "bg-zinc-100 text-zinc-600 border border-zinc-200"
                          }`}
                        >
                          {isConnected ? "Connected" : "Unanswered"}
                        </span>
                      </div>
                      <span className="text-xs font-bold text-zinc-900">
                        {inr(call.revenue_inr)}
                      </span>
                    </div>

                    <div className="flex items-center justify-between text-[11px] text-zinc-500">
                      <span>{call.caller_number || "Direct WebRTC / Inbound"}</span>
                      <span className="flex items-center gap-1 font-medium text-zinc-600">
                        <Clock className="w-3 h-3 text-zinc-400" />
                        {formatDuration(call.duration_seconds)}
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Pagination Controls */}
          {totalCalls > 15 && (
            <div className="flex items-center justify-between pt-2 border-t border-zinc-100 text-xs text-zinc-500">
              <span>
                Page {callPage} of {Math.ceil(totalCalls / 15)}
              </span>
              <div className="flex items-center gap-1">
                <button
                  disabled={callPage <= 1}
                  onClick={() => setCallPage((p) => Math.max(p - 1, 1))}
                  className="p-1.5 rounded border border-zinc-200 disabled:opacity-40 hover:bg-zinc-100 transition-colors"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <button
                  disabled={callPage >= Math.ceil(totalCalls / 15)}
                  onClick={() => setCallPage((p) => p + 1)}
                  className="p-1.5 rounded border border-zinc-200 disabled:opacity-40 hover:bg-zinc-100 transition-colors"
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          )}
        </div>

        {/* RIGHT PANEL: ITEMIZED AUDIT & MARGIN CARD (7 Cols) */}
        <div className="lg:col-span-7 space-y-6">
          {loadingAudit ? (
            <div className="bg-white rounded-2xl border border-zinc-200/80 p-12 text-center text-xs text-zinc-400">
              <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-3 text-[#fe6905]" />
              Calculating exact unit economics and audited telemetry...
            </div>
          ) : !displayAudit ? (
            <div className="bg-white rounded-2xl border border-zinc-200/80 p-12 text-center text-xs text-zinc-400">
              Select a call run on the left to inspect its vendor receipt.
            </div>
          ) : (
            <>
              {/* HERO MARGIN CARD */}
              <div className="bg-white rounded-2xl border border-zinc-200/80 p-6 shadow-sm space-y-5">
                {/* Margin Hero Banner */}
                {marginBadge && (
                  <div
                    className={`rounded-xl border p-4 flex items-center justify-between ${marginBadge.bg}`}
                  >
                    <div className="flex items-center gap-3">
                      <marginBadge.icon className="w-6 h-6 shrink-0" />
                      <div>
                        <div className="text-base font-extrabold tracking-tight">
                          {marginBadge.label}
                        </div>
                        <div className="text-xs font-medium opacity-90">{marginBadge.sub}</div>
                      </div>
                    </div>
                    {simulation && (
                      <span className="px-2 py-1 bg-amber-500 text-white rounded text-[10px] font-bold uppercase tracking-wider shadow-sm">
                        Simulated
                      </span>
                    )}
                  </div>
                )}

                {/* 4 KPI Metric Cards */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <div className="p-3 bg-zinc-50 border border-zinc-100 rounded-xl">
                    <span className="text-[10px] uppercase font-bold text-zinc-400 block mb-0.5">
                      Billed Revenue
                    </span>
                    <span className="text-base font-black text-zinc-900 block">
                      {inr(displayAudit.revenue_inr)}
                    </span>
                    <span className="text-[10px] text-zinc-500">
                      {displayAudit.billed_minutes} mins @ {inr(displayAudit.effective_rate_per_min_inr)}/m
                    </span>
                  </div>

                  <div className="p-3 bg-zinc-50 border border-zinc-100 rounded-xl">
                    <span className="text-[10px] uppercase font-bold text-zinc-400 block mb-0.5">
                      Total AI+Carrier Cost
                    </span>
                    <span className="text-base font-black text-zinc-900 block">
                      {inr(displayAudit.total_cost_inr)}
                    </span>
                    <span className="text-[10px] text-zinc-500">
                      {inr(displayAudit.cost_per_minute_inr)} / minute
                    </span>
                  </div>

                  <div className="p-3 bg-zinc-50 border border-zinc-100 rounded-xl">
                    <span className="text-[10px] uppercase font-bold text-zinc-400 block mb-0.5">
                      Duration & Pulse
                    </span>
                    <span className="text-base font-black text-zinc-900 block">
                      {formatDuration(displayAudit.call_duration_seconds)}
                    </span>
                    <span className="text-[10px] text-zinc-500">
                      {displayAudit.billed_minutes} billable Plivo pulse
                    </span>
                  </div>

                  <div className="p-3 bg-zinc-50 border border-zinc-100 rounded-xl">
                    <span className="text-[10px] uppercase font-bold text-zinc-400 block mb-0.5">
                      Net Profit
                    </span>
                    <span
                      className={`text-base font-black block ${
                        displayAudit.profit_inr >= 0 ? "text-emerald-600" : "text-red-600"
                      }`}
                    >
                      {inr(displayAudit.profit_inr)}
                    </span>
                    <span className="text-[10px] text-zinc-500">
                      Margin: {displayAudit.margin_pct.toFixed(1)}%
                    </span>
                  </div>
                </div>

                {/* Stacked Proportional Cost Bar */}
                <div className="space-y-2 pt-2 border-t border-zinc-100">
                  <div className="flex items-center justify-between text-xs text-zinc-500">
                    <span className="font-semibold text-zinc-700">Cost Distribution by Layer</span>
                    <span>Total: {inr(totalCost)}</span>
                  </div>

                  <div className="h-3 w-full bg-zinc-100 rounded-full overflow-hidden flex shadow-inner">
                    {costSegments.map((seg) => (
                      <div
                        key={seg.id}
                        style={{ width: `${seg.pct}%`, backgroundColor: seg.color }}
                        className="h-full transition-all duration-300"
                        title={`${seg.label}: ${inr(seg.cost)} (${seg.pct.toFixed(1)}%)`}
                      />
                    ))}
                  </div>

                  {/* Legend underneath bar */}
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 pt-1 text-[11px] text-zinc-600">
                    {costSegments.map((seg) => (
                      <div key={seg.id} className="flex items-center gap-1.5">
                        <span
                          className="w-2.5 h-2.5 rounded-full shrink-0"
                          style={{ backgroundColor: seg.color }}
                        />
                        <span>{seg.label}:</span>
                        <strong className="text-zinc-800">{inr(seg.cost)}</strong>
                        <span className="text-zinc-400 text-[10px]">({seg.pct.toFixed(0)}%)</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* ITEMIZED VENDOR RECEIPT TABLE */}
              <div className="bg-white rounded-2xl border border-zinc-200/80 p-6 shadow-sm space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-zinc-700 flex items-center gap-2">
                    <Layers className="w-4 h-4 text-[#fe6905]" />
                    Itemized Vendor Wholesale Receipt
                  </h3>
                  <span className="text-[11px] text-zinc-400">Audited Measured Telemetry</span>
                </div>

                {cb && (
                  <div className="border border-zinc-200/80 rounded-xl overflow-hidden text-xs">
                    <table className="w-full text-left divide-y divide-zinc-200/80">
                      <thead className="bg-zinc-50 text-[10px] font-bold uppercase text-zinc-500 tracking-wider">
                        <tr>
                          <th className="px-3 py-2.5">Component Layer</th>
                          <th className="px-3 py-2.5">Provider & Model</th>
                          <th className="px-3 py-2.5">Audited Usage</th>
                          <th className="px-3 py-2.5">Wholesale Unit Rate</th>
                          <th className="px-3 py-2.5 text-right">Cost (INR)</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-zinc-100 bg-white">
                        {/* 1. Telephony */}
                        <tr>
                          <td className="px-3 py-3 font-semibold text-zinc-900 flex items-center gap-1.5">
                            <Phone className="w-3.5 h-3.5 text-blue-500" />
                            Telephony
                          </td>
                          <td className="px-3 py-3 text-zinc-700">Plivo India Outbound</td>
                          <td className="px-3 py-3 text-zinc-600 font-mono">
                            {cb.plivo.minutes} billed min ({displayAudit.call_duration_seconds}s)
                          </td>
                          <td className="px-3 py-3 text-zinc-500 font-mono">₹{cb.plivo.rate_inr.toFixed(2)}/min</td>
                          <td className="px-3 py-3 text-right font-bold text-zinc-900">
                            {inr(cb.plivo.cost_inr)}
                          </td>
                        </tr>

                        {/* 2. STT */}
                        <tr>
                          <td className="px-3 py-3 font-semibold text-zinc-900 flex items-center gap-1.5">
                            <Mic className="w-3.5 h-3.5 text-purple-500" />
                            Speech-to-Text
                          </td>
                          <td className="px-3 py-3 text-zinc-700 capitalize">
                            {cb.stt.provider} ({cb.stt.model})
                          </td>
                          <td className="px-3 py-3 text-zinc-600 font-mono">{cb.stt.seconds.toFixed(1)}s audio</td>
                          <td className="px-3 py-3 text-zinc-500 font-mono">
                            {cb.stt.rate_usd_min ? `${usd(cb.stt.rate_usd_min)}/m` : `₹0.50/m`}
                          </td>
                          <td className="px-3 py-3 text-right font-bold text-zinc-900">
                            {inr(cb.stt.cost_inr)}
                          </td>
                        </tr>

                        {/* 3. Conversational LLM */}
                        <tr>
                          <td className="px-3 py-3 font-semibold text-zinc-900 flex items-center gap-1.5">
                            <Bot className="w-3.5 h-3.5 text-emerald-500" />
                            Conversational LLM
                          </td>
                          <td className="px-3 py-3 text-zinc-700 capitalize">
                            {cb.llm_conversation.provider} ({cb.llm_conversation.model})
                          </td>
                          <td className="px-3 py-3 text-zinc-600 font-mono">
                            {cb.llm_conversation.prompt_tokens.toLocaleString()} in /{" "}
                            {cb.llm_conversation.completion_tokens.toLocaleString()} out
                            {cb.llm_conversation.cached_tokens > 0 && (
                              <span className="text-[10px] text-emerald-600 block">
                                ({cb.llm_conversation.cached_tokens} cached)
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-3 text-zinc-500 font-mono text-[11px]">
                            PAYG Tier Rate
                          </td>
                          <td className="px-3 py-3 text-right font-bold text-zinc-900">
                            {inr(cb.llm_conversation.cost_inr)}
                          </td>
                        </tr>

                        {/* 4. Extraction LLM */}
                        <tr>
                          <td className="px-3 py-3 font-semibold text-zinc-900 flex items-center gap-1.5">
                            <Cpu className="w-3.5 h-3.5 text-cyan-500" />
                            Extraction LLM
                          </td>
                          <td className="px-3 py-3 text-zinc-700 capitalize">
                            {cb.llm_extraction.provider} ({cb.llm_extraction.model})
                          </td>
                          <td className="px-3 py-3 text-zinc-600 font-mono">
                            {cb.llm_extraction.prompt_tokens.toLocaleString()} in /{" "}
                            {cb.llm_extraction.completion_tokens.toLocaleString()} out
                          </td>
                          <td className="px-3 py-3 text-zinc-500 font-mono text-[11px]">
                            Extraction Frame Rate
                          </td>
                          <td className="px-3 py-3 text-right font-bold text-zinc-900">
                            {inr(cb.llm_extraction.cost_inr)}
                          </td>
                        </tr>

                        {/* 5. Text-to-Speech */}
                        <tr>
                          <td className="px-3 py-3 font-semibold text-zinc-900 flex items-center gap-1.5">
                            <Volume2 className="w-3.5 h-3.5 text-amber-500" />
                            Text-to-Speech
                          </td>
                          <td className="px-3 py-3 text-zinc-700 capitalize">
                            {cb.tts.provider} ({cb.tts.model})
                          </td>
                          <td className="px-3 py-3 text-zinc-600 font-mono">
                            {cb.tts.characters.toLocaleString()} chars
                          </td>
                          <td className="px-3 py-3 text-zinc-500 font-mono text-[11px]">
                            {cb.tts.rate_usd_per_10k ? `$0.175/10k` : `Standard 1k tier`}
                          </td>
                          <td className="px-3 py-3 text-right font-bold text-zinc-900">
                            {inr(cb.tts.cost_inr)}
                          </td>
                        </tr>

                        {/* Total Footer */}
                        <tr className="bg-zinc-50/80 font-bold border-t-2 border-zinc-200">
                          <td colSpan={4} className="px-3 py-3 text-zinc-900 text-right uppercase text-[10px] tracking-wider">
                            Total Audited Call Cost:
                          </td>
                          <td className="px-3 py-3 text-right font-black text-zinc-900 text-sm">
                            {inr(displayAudit.total_cost_inr)}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                )}

                {/* Audit Notices & Pills */}
                {displayAudit.notices && displayAudit.notices.length > 0 && (
                  <div className="space-y-1.5 pt-2">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-400 block">
                      Audit Trail & Telemetry Flags:
                    </span>
                    <div className="flex flex-wrap gap-2">
                      {displayAudit.notices.map((n, idx) => (
                        <span
                          key={idx}
                          className="px-2 py-0.5 rounded-full bg-zinc-100 text-zinc-700 border border-zinc-200/80 text-[10px] font-medium"
                        >
                          {n}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* ── 3. INTERACTIVE "WHAT-IF" SIMULATION DRAWER ───────────── */}
              <div className="bg-gradient-to-br from-zinc-900 via-zinc-900 to-black text-white rounded-2xl p-6 shadow-xl border border-zinc-800 space-y-5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Sparkles className="w-4 h-4 text-[#fe6905]" />
                    <h3 className="text-sm font-bold tracking-tight">
                      Interactive &ldquo;What-If&rdquo; Simulation Sandbox
                    </h3>
                  </div>
                  {simulation && (
                    <button
                      onClick={handleResetSimulation}
                      className="inline-flex items-center gap-1 px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs transition-colors cursor-pointer"
                    >
                      <RotateCcw className="w-3.5 h-3.5" />
                      Reset to Actuals
                    </button>
                  )}
                </div>
                <p className="text-xs text-zinc-400">
                  Dynamically simulate this exact call with alternate TTS or LLM models to analyze margin impact:
                </p>

                {/* Model Selectors Grid */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
                  {/* TTS Switcher */}
                  <div className="space-y-1.5">
                    <label className="text-[10px] uppercase font-bold text-zinc-400 block">
                      Switch TTS Model:
                    </label>
                    <select
                      value={simTTS}
                      onChange={(e) => {
                        setSimTTS(e.target.value);
                        handleSimulate(e.target.value, simLLM, simSTT);
                      }}
                      className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-[#fe6905] transition-all cursor-pointer"
                    >
                      <option value="">Keep Actual TTS</option>
                      <option value="smallest_ai_lightning">Smallest AI Lightning V3.1 ($0.0175/1k)</option>
                      <option value="sarvam_bulbul_v4">Sarvam Bulbul v4 Flash (₹2.00/1k)</option>
                      <option value="sarvam_bulbul_v3">Sarvam Bulbul v3 (₹3.00/1k)</option>
                      <option value="deepgram_aura_2">Deepgram Aura-2 ($0.030/1k)</option>
                      <option value="deepgram_aura_1">Deepgram Aura-1 ($0.015/1k)</option>
                      <option value="elevenlabs_flash">ElevenLabs Flash / Turbo ($0.040/1k)</option>
                      <option value="elevenlabs_multilingual">ElevenLabs Multilingual ($0.080/1k)</option>
                    </select>
                  </div>

                  {/* LLM Switcher */}
                  <div className="space-y-1.5">
                    <label className="text-[10px] uppercase font-bold text-zinc-400 block">
                      Switch Conversational LLM:
                    </label>
                    <select
                      value={simLLM}
                      onChange={(e) => {
                        setSimLLM(e.target.value);
                        handleSimulate(simTTS, e.target.value, simSTT);
                      }}
                      className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-[#fe6905] transition-all cursor-pointer"
                    >
                      <option value="">Keep Actual LLM</option>
                      <option value="sarvam-105b">Sarvam-105B (₹15 in / ₹60 out)</option>
                      <option value="gpt-4.1-mini">GPT-4.1-mini ($0.40 in / $1.60 out)</option>
                      <option value="gpt-4o-mini">GPT-4o-mini ($0.15 in / $0.60 out)</option>
                      <option value="gpt-4o">GPT-4o ($2.50 in / $10.00 out)</option>
                    </select>
                  </div>

                  {/* STT Switcher */}
                  <div className="space-y-1.5">
                    <label className="text-[10px] uppercase font-bold text-zinc-400 block">
                      Switch STT Provider:
                    </label>
                    <select
                      value={simSTT}
                      onChange={(e) => {
                        setSimSTT(e.target.value);
                        handleSimulate(simTTS, simLLM, e.target.value);
                      }}
                      className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-[#fe6905] transition-all cursor-pointer"
                    >
                      <option value="">Keep Actual STT</option>
                      <option value="deepgram_nova_3">Deepgram Nova-3 ($0.0048/m)</option>
                      <option value="sarvam_saaras">Sarvam Saaras (₹0.50/m)</option>
                    </select>
                  </div>
                </div>

                {/* Simulation Result Comparison Box */}
                {loadingSim ? (
                  <div className="py-6 text-center text-xs text-zinc-400">
                    <RefreshCw className="w-4 h-4 animate-spin mx-auto mb-2 text-[#fe6905]" />
                    Simulating alternative vendor pricing...
                  </div>
                ) : simulation ? (
                  <div className="bg-zinc-800/80 rounded-xl p-4 border border-zinc-700 space-y-3">
                    <div className="flex items-center justify-between text-xs border-b border-zinc-700/60 pb-2">
                      <span className="text-zinc-400 font-medium">Comparison vs Audited Baseline:</span>
                      <span className="font-bold text-white">Delta Summary</span>
                    </div>

                    <div className="grid grid-cols-3 gap-3 text-center">
                      <div>
                        <span className="text-[10px] uppercase text-zinc-400 block mb-0.5">
                          Simulated Cost
                        </span>
                        <span className="text-sm font-black text-white block">
                          {inr(simulation.simulated.total_cost_inr)}
                        </span>
                        <span
                          className={`text-[10px] font-bold ${
                            simulation.delta.total_cost_delta_inr <= 0
                              ? "text-emerald-400"
                              : "text-red-400"
                          }`}
                        >
                          {simulation.delta.total_cost_delta_inr >= 0 ? "+" : ""}
                          {inr(simulation.delta.total_cost_delta_inr)}
                        </span>
                      </div>

                      <div>
                        <span className="text-[10px] uppercase text-zinc-400 block mb-0.5">
                          Simulated Profit
                        </span>
                        <span className="text-sm font-black text-white block">
                          {inr(simulation.simulated.profit_inr)}
                        </span>
                        <span
                          className={`text-[10px] font-bold ${
                            simulation.delta.profit_delta_inr >= 0
                              ? "text-emerald-400"
                              : "text-red-400"
                          }`}
                        >
                          {simulation.delta.profit_delta_inr >= 0 ? "+" : ""}
                          {inr(simulation.delta.profit_delta_inr)}
                        </span>
                      </div>

                      <div>
                        <span className="text-[10px] uppercase text-zinc-400 block mb-0.5">
                          Simulated Margin
                        </span>
                        <span className="text-sm font-black text-white block">
                          {simulation.simulated.margin_pct.toFixed(1)}%
                        </span>
                        <span
                          className={`text-[10px] font-bold ${
                            simulation.delta.margin_pct_delta >= 0
                              ? "text-emerald-400"
                              : "text-red-400"
                          }`}
                        >
                          {simulation.delta.margin_pct_delta >= 0 ? "+" : ""}
                          {simulation.delta.margin_pct_delta.toFixed(1)}%
                        </span>
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
