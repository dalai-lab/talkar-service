"use client";

import { adminFetch } from "@/lib/api";
import React, { useEffect, useState, useMemo } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  TrendingUp,
  TrendingDown,
  DollarSign,
  Phone,
  PhoneCall,
  Clock,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  SlidersHorizontal,
  X,
  Search,
  ArrowUpDown,
  Wallet,
  Percent,
  Layers,
  Cpu,
  Bot,
  Volume2,
  Mic,
  Brain,
  Sparkles,
  Check,
  AlertTriangle,
  Info,
  ChevronRight,
} from "lucide-react";

interface ModelUsageItem {
  provider: string;
  model: string;
  calls: number;
  minutes?: number;
  characters?: number;
  tokens?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  cached_tokens?: number;
  cost_inr: number;
}

interface ModelsUsedInventory {
  stt?: ModelUsageItem[];
  tts?: ModelUsageItem[];
  llm?: ModelUsageItem[];
  post_call?: ModelUsageItem[];
}

interface PlanBucket {
  plan: string;
  tts_provider: string;
  calls: number;
  total_minutes: number;
  revenue_inr: number;
  cost_inr: number;
  profit_inr: number;
  margin_pct: number;
  breakdown: {
    plivo_inr: number;
    stt_inr: number;
    tts_inr: number;
    llm_inr: number;
    post_call_inr?: number;
    stt_vendors?: Record<string, number>;
    tts_vendors?: Record<string, number>;
    llm_vendors?: Record<string, number>;
    post_call_vendors?: Record<string, number>;
  };
  models_used?: ModelsUsedInventory;
}

interface CustomerRow {
  customer_id: number;
  company_name: string;
  contact_email: string | null;
  status: string | null;
  plan: string;
  calls: number;
  total_minutes: number;
  revenue_inr: number;
  cost_inr: number;
  profit_inr: number;
  margin_pct: number;
  breakdown: {
    plivo_inr: number;
    stt_inr: number;
    tts_inr: number;
    llm_inr: number;
    post_call_inr?: number;
    stt_vendors?: Record<string, number>;
    tts_vendors?: Record<string, number>;
    llm_vendors?: Record<string, number>;
    post_call_vendors?: Record<string, number>;
  };
  models_used?: ModelsUsedInventory;
  plan_breakdown: PlanBucket[];
}

interface Summary {
  total_calls: number;
  total_topups_inr: number;
  total_revenue_inr: number;
  total_cost_inr: number;
  gross_profit_inr: number;
  gross_margin_pct: number;
}

interface ProfitData {
  period: string;
  summary: Summary;
  customers: CustomerRow[];
  cost_assumptions: Record<string, unknown>;
}

const VENDOR_DISPLAY_NAMES: Record<string, string> = {
  deepgram: "Deepgram",
  smallest_ai: "Smallest AI",
  smallest: "Smallest AI",
  elevenlabs: "ElevenLabs",
  eleven: "ElevenLabs",
  cartesia: "Cartesia",
  sarvam: "Sarvam AI",
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google Gemini",
  gemini: "Google Gemini",
  deepseek: "DeepSeek",
  groq: "Groq",
  azure: "Azure Speech",
  assemblyai: "AssemblyAI",
  gladia: "Gladia",
  speechmatics: "Speechmatics",
};

function formatVendorName(name: string): string {
  const clean = (name || "").toLowerCase().replace(/[-_]/g, " ").trim();
  for (const [key, label] of Object.entries(VENDOR_DISPLAY_NAMES)) {
    if (clean === key || clean.includes(key)) return label;
  }
  return clean.replace(/\b\w/g, (c) => c.toUpperCase());
}

function getVendorBadge(
  type: "stt" | "tts" | "llm" | "post_call",
  vendors?: Record<string, number>,
  fallbackProvider?: string
): { label: string; tooltip?: string; isMixed: boolean; primaryVendor: string } {
  const typeLabel =
    type === "stt" ? "STT" : type === "tts" ? "TTS" : type === "post_call" ? "Post-Call" : "LLM";
  const vendorEntries = vendors ? Object.entries(vendors).filter(([_, cost]) => cost > 0) : [];

  if (vendorEntries.length === 0) {
    const fallback = fallbackProvider
      ? formatVendorName(fallbackProvider)
      : type === "stt"
        ? "Deepgram"
        : type === "tts"
          ? "Smallest AI"
          : "OpenAI";
    return { label: fallback, isMixed: false, primaryVendor: fallback };
  }

  if (vendorEntries.length === 1) {
    const [name] = vendorEntries[0];
    const formatted = formatVendorName(name);
    return { label: formatted, isMixed: false, primaryVendor: formatted };
  }

  const tooltip = vendorEntries
    .map(([name, cost]) => `${formatVendorName(name)}: ₹${cost.toFixed(2)}`)
    .join(" • ");

  return {
    label: `Mixed (${vendorEntries.length})`,
    tooltip,
    isMixed: true,
    primaryVendor: "Mixed",
  };
}

const inr = (v: number | undefined | null) =>
  `₹${(v || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const pct = (v: number | undefined | null) => `${(v || 0).toFixed(1)}%`;

function getInitials(name: string): string {
  if (!name) return "??";
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

function getAvatarColor(name: string): string {
  const palettes = [
    "bg-indigo-100 text-indigo-700 border-indigo-200",
    "bg-sky-100 text-sky-700 border-sky-200",
    "bg-emerald-100 text-emerald-700 border-emerald-200",
    "bg-amber-100 text-amber-800 border-amber-200",
    "bg-violet-100 text-violet-700 border-violet-200",
    "bg-teal-100 text-teal-800 border-teal-200",
    "bg-rose-100 text-rose-700 border-rose-200",
  ];
  let hash = 0;
  for (let i = 0; i < (name || "").length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return palettes[Math.abs(hash) % palettes.length];
}

const PLAN_STYLES: Record<string, { label: string; badge: string }> = {
  starter: {
    label: "Starter",
    badge: "bg-slate-100 text-slate-700 border-slate-200",
  },
  growth: {
    label: "Growth",
    badge: "bg-emerald-50 text-emerald-800 border-emerald-200",
  },
  pro: {
    label: "Pro",
    badge: "bg-blue-50 text-blue-800 border-blue-200",
  },
  elite: {
    label: "Elite",
    badge: "bg-purple-50 text-purple-800 border-purple-200",
  },
  custom: {
    label: "Custom",
    badge: "bg-amber-50 text-amber-800 border-amber-200",
  },
};

const formatTokens = (tok?: number) => {
  if (!tok) return "0 tok";
  if (tok >= 1000000) return `${(tok / 1000000).toFixed(1)}M tok`;
  if (tok >= 1000) return `${(tok / 1000).toFixed(1)}k tok`;
  return `${tok} tok`;
};

const formatChars = (chars?: number) => {
  if (!chars) return "0 chars";
  if (chars >= 1000000) return `${(chars / 1000000).toFixed(2)}M chars`;
  if (chars >= 1000) return `${(chars / 1000).toFixed(1)}k chars`;
  return `${chars} chars`;
};

const formatMinutes = (min?: number) => {
  if (min === undefined || min === null) return "0.0m";
  return `${min.toFixed(1)}m`;
};

function MarginPill({ margin }: { margin: number }) {
  if (margin >= 50) {
    return (
      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
        {pct(margin)}
      </span>
    );
  }
  if (margin >= 30) {
    return (
      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-amber-50 text-amber-800 border border-amber-200">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
        {pct(margin)}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-rose-50 text-rose-700 border border-rose-200">
      <span className="w-1.5 h-1.5 rounded-full bg-rose-500" />
      {pct(margin)}
    </span>
  );
}

function CostBar({
  breakdown,
  total,
}: {
  breakdown: CustomerRow["breakdown"];
  total: number;
}) {
  if (total <= 0) {
    return (
      <div className="text-[11px] text-slate-400 italic py-1">
        No billable cost incurred for this customer in this window.
      </div>
    );
  }

  const segments = [
    {
      label: "Plivo Telephony",
      short: "Plivo",
      value: breakdown.plivo_inr || 0,
      color: "bg-blue-500",
      dot: "bg-blue-500",
    },
    {
      label: "Speech (STT)",
      short: "STT",
      value: breakdown.stt_inr || 0,
      color: "bg-purple-500",
      dot: "bg-purple-500",
    },
    {
      label: "Voice (TTS)",
      short: "TTS",
      value: breakdown.tts_inr || 0,
      color: "bg-amber-500",
      dot: "bg-amber-500",
    },
    {
      label: "In-Call LLM",
      short: "LLM",
      value: breakdown.llm_inr || 0,
      color: "bg-emerald-500",
      dot: "bg-emerald-500",
    },
  ];

  if (breakdown.post_call_inr && breakdown.post_call_inr > 0) {
    segments.push({
      label: "Post-Call & QA",
      short: "Post-Call",
      value: breakdown.post_call_inr,
      color: "bg-indigo-500",
      dot: "bg-indigo-500",
    });
  }

  const activeSegments = segments.filter((s) => s.value > 0);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs">
        <span className="text-[11px] font-semibold text-slate-600 uppercase tracking-wider flex items-center gap-1.5">
          <Layers className="w-3.5 h-3.5 text-slate-400" />
          Cost Allocation by Layer
        </span>
        <span className="text-xs text-slate-500">
          Total COGS: <strong className="text-slate-900 font-semibold">{inr(total)}</strong>
        </span>
      </div>

      <div className="flex h-3 w-full rounded-full overflow-hidden gap-0.5 bg-slate-100/80 p-0.5 border border-slate-200/60 shadow-inner">
        {activeSegments.map((s) => {
          const ratio = (s.value / total) * 100;
          return (
            <div
              key={s.label}
              className={`${s.color} h-full rounded-full transition-all duration-300 relative group`}
              style={{ width: `${Math.max(ratio, 1.5)}%` }}
              title={`${s.label}: ${inr(s.value)} (${ratio.toFixed(1)}%)`}
            />
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-slate-600">
        {segments.map((s) => {
          const share = total > 0 ? (s.value / total) * 100 : 0;
          return (
            <div key={s.label} className="flex items-center gap-1.5">
              <span className={`w-2 h-2 rounded-full ${s.dot} shrink-0`} />
              <span className="text-slate-500 font-medium">{s.short}:</span>
              <strong className="text-slate-800 font-semibold">{inr(s.value)}</strong>
              <span className="text-[10px] text-slate-400">({share.toFixed(0)}%)</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function InfrastructureBento({
  breakdown,
  ttsProvider,
  totalCost,
}: {
  breakdown: CustomerRow["breakdown"];
  ttsProvider?: string;
  totalCost: number;
}) {
  const sttBadge = getVendorBadge("stt", breakdown.stt_vendors, "deepgram");
  const ttsBadge = getVendorBadge("tts", breakdown.tts_vendors, ttsProvider);
  const llmBadge = getVendorBadge("llm", breakdown.llm_vendors, "openai");
  const hasPostCall = Boolean(breakdown.post_call_inr && breakdown.post_call_inr > 0);
  const pcBadge = hasPostCall
    ? getVendorBadge("post_call", breakdown.post_call_vendors, "openai")
    : null;

  const items = [
    {
      label: "Telephony Carrier",
      icon: Phone,
      vendor: "Plivo",
      cost: breakdown.plivo_inr || 0,
      color: "border-blue-200 bg-blue-50/30 text-blue-700",
      iconColor: "text-blue-600",
    },
    {
      label: "Speech-to-Text",
      icon: Mic,
      vendor: sttBadge.label,
      tooltip: sttBadge.tooltip,
      cost: breakdown.stt_inr || 0,
      color: "border-purple-200 bg-purple-50/30 text-purple-700",
      iconColor: "text-purple-600",
    },
    {
      label: "Voice Synthesis",
      icon: Volume2,
      vendor: ttsBadge.label,
      tooltip: ttsBadge.tooltip,
      cost: breakdown.tts_inr || 0,
      color: "border-amber-200 bg-amber-50/30 text-amber-700",
      iconColor: "text-amber-600",
    },
    {
      label: "In-Call LLM",
      icon: Bot,
      vendor: llmBadge.label,
      tooltip: llmBadge.tooltip,
      cost: breakdown.llm_inr || 0,
      color: "border-emerald-200 bg-emerald-50/30 text-emerald-700",
      iconColor: "text-emerald-600",
    },
    ...(hasPostCall && pcBadge
      ? [
        {
          label: "Post-Call & QA",
          icon: Brain,
          vendor: pcBadge.label,
          tooltip: pcBadge.tooltip,
          cost: breakdown.post_call_inr || 0,
          color: "border-indigo-200 bg-indigo-50/30 text-indigo-700",
          iconColor: "text-indigo-600",
        },
      ]
      : []),
  ];

  return (
    <div
      className={`grid grid-cols-2 ${items.length === 5 ? "sm:grid-cols-5" : "sm:grid-cols-4"
        } gap-2.5`}
    >
      {items.map((it) => {
        const share = totalCost > 0 ? (it.cost / totalCost) * 100 : 0;
        const IconComponent = it.icon;
        return (
          <div
            key={it.label}
            className="p-3 bg-white rounded-xl border border-slate-200/80 shadow-xs flex flex-col justify-between"
            title={it.tooltip}
          >
            <div className="flex items-center justify-between gap-1.5 mb-1.5">
              <span className="text-[11px] font-medium text-slate-500 flex items-center gap-1.5 truncate">
                <IconComponent className={`w-3.5 h-3.5 ${it.iconColor} shrink-0`} />
                {it.label}
              </span>
              <span
                className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full border truncate max-w-[90px] ${it.color}`}
              >
                {it.vendor}
              </span>
            </div>
            <div>
              <div className="text-sm font-bold text-slate-900">{inr(it.cost)}</div>
              <div className="text-[10px] text-slate-400 mt-0.5">
                {share.toFixed(1)}% of total cost
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ModelsInventorySection({ models }: { models?: ModelsUsedInventory }) {
  if (!models) return null;
  const hasStt = models.stt && models.stt.length > 0;
  const hasTts = models.tts && models.tts.length > 0;
  const hasLlm = models.llm && models.llm.length > 0;
  const hasPostCall = models.post_call && models.post_call.length > 0;
  if (!hasStt && !hasTts && !hasLlm && !hasPostCall) return null;

  const totalCols = [hasLlm, hasPostCall, hasTts, hasStt].filter(Boolean).length;
  const gridColsClass =
    totalCols >= 4
      ? "grid-cols-1 sm:grid-cols-2 lg:grid-cols-4"
      : totalCols === 3
        ? "grid-cols-1 md:grid-cols-3"
        : totalCols === 2
          ? "grid-cols-1 sm:grid-cols-2"
          : "grid-cols-1";

  return (
    <div className="mt-4 pt-3.5 border-t border-slate-200/80">
      <div className="flex items-center justify-between mb-2.5">
        <div className="flex items-center gap-2">
          <span className="flex items-center gap-1.5 text-xs font-bold text-slate-800 tracking-tight">
            <Cpu className="w-4 h-4 text-[#fe6905]" /> Models & Engines Inventory
          </span>
          <span className="text-[10px] font-medium text-slate-500 bg-slate-100 border border-slate-200/60 px-2 py-0.5 rounded-md">
            Live Telemetry Breakdown
          </span>
        </div>
        <span className="text-[11px] text-slate-400">Audited upstream model invocations</span>
      </div>

      <div className={`grid ${gridColsClass} gap-3`}>
        {/* In-Call LLM Models */}
        {hasLlm && (
          <div className="bg-white border border-slate-200/80 rounded-xl p-3 shadow-xs">
            <div className="text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-2 flex items-center justify-between border-b border-slate-100 pb-1.5">
              <span className="flex items-center gap-1 text-emerald-800">
                <Bot className="w-3.5 h-3.5 text-emerald-600" /> In-Call LLM
              </span>
              <span className="text-[10px] font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200 px-1.5 py-0.2 rounded-full">
                {models.llm!.length} active
              </span>
            </div>
            <div className="space-y-2">
              {models.llm!.map((item, idx) => (
                <div
                  key={idx}
                  className="flex items-center justify-between bg-slate-50/70 hover:bg-slate-50 px-2.5 py-1.5 rounded-lg border border-slate-200/60 text-xs transition-colors"
                >
                  <div className="min-w-0 pr-2">
                    <div className="font-semibold text-slate-800 text-[11px] truncate flex items-center gap-1">
                      <span>{formatVendorName(item.provider)}</span>
                      <span className="font-mono text-[10px] text-slate-500 font-normal bg-white px-1.5 py-0.2 rounded border border-slate-200">
                        {item.model}
                      </span>
                    </div>
                    <div className="text-[10px] text-slate-500 mt-0.5">
                      <span className="font-medium text-slate-700">{item.calls}</span>{" "}
                      {item.calls === 1 ? "call" : "calls"} • {formatTokens(item.tokens)}
                    </div>
                  </div>
                  <div className="text-right shrink-0 font-bold text-xs text-slate-900">
                    {inr(item.cost_inr)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Post-Call & QA Models */}
        {hasPostCall && (
          <div className="bg-white border border-slate-200/80 rounded-xl p-3 shadow-xs">
            <div className="text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-2 flex items-center justify-between border-b border-slate-100 pb-1.5">
              <span className="flex items-center gap-1 text-indigo-800">
                <Brain className="w-3.5 h-3.5 text-indigo-600" /> Post-Call & QA
              </span>
              <span className="text-[10px] font-semibold text-indigo-700 bg-indigo-50 border border-indigo-200 px-1.5 py-0.2 rounded-full">
                {models.post_call!.length} active
              </span>
            </div>
            <div className="space-y-2">
              {models.post_call!.map((item, idx) => (
                <div
                  key={idx}
                  className="flex items-center justify-between bg-slate-50/70 hover:bg-slate-50 px-2.5 py-1.5 rounded-lg border border-slate-200/60 text-xs transition-colors"
                >
                  <div className="min-w-0 pr-2">
                    <div className="font-semibold text-slate-800 text-[11px] truncate flex items-center gap-1">
                      <span>{formatVendorName(item.provider)}</span>
                      <span className="font-mono text-[10px] text-slate-500 font-normal bg-white px-1.5 py-0.2 rounded border border-slate-200">
                        {item.model}
                      </span>
                    </div>
                    <div className="text-[10px] text-slate-500 mt-0.5">
                      <span className="font-medium text-slate-700">{item.calls}</span>{" "}
                      {item.calls === 1 ? "call" : "calls"} • {formatTokens(item.tokens)}
                    </div>
                  </div>
                  <div className="text-right shrink-0 font-bold text-xs text-slate-900">
                    {inr(item.cost_inr)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* TTS Voices */}
        {hasTts && (
          <div className="bg-white border border-slate-200/80 rounded-xl p-3 shadow-xs">
            <div className="text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-2 flex items-center justify-between border-b border-slate-100 pb-1.5">
              <span className="flex items-center gap-1 text-amber-800">
                <Volume2 className="w-3.5 h-3.5 text-amber-600" /> Voice Synthesis (TTS)
              </span>
              <span className="text-[10px] font-semibold text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.2 rounded-full">
                {models.tts!.length} active
              </span>
            </div>
            <div className="space-y-2">
              {models.tts!.map((item, idx) => (
                <div
                  key={idx}
                  className="flex items-center justify-between bg-slate-50/70 hover:bg-slate-50 px-2.5 py-1.5 rounded-lg border border-slate-200/60 text-xs transition-colors"
                >
                  <div className="min-w-0 pr-2">
                    <div className="font-semibold text-slate-800 text-[11px] truncate flex items-center gap-1">
                      <span>{formatVendorName(item.provider)}</span>
                      <span className="font-mono text-[10px] text-slate-500 font-normal bg-white px-1.5 py-0.2 rounded border border-slate-200">
                        {item.model}
                      </span>
                    </div>
                    <div className="text-[10px] text-slate-500 mt-0.5">
                      <span className="font-medium text-slate-700">{item.calls}</span>{" "}
                      {item.calls === 1 ? "call" : "calls"} • {formatChars(item.characters)}
                    </div>
                  </div>
                  <div className="text-right shrink-0 font-bold text-xs text-slate-900">
                    {inr(item.cost_inr)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* STT Engines */}
        {hasStt && (
          <div className="bg-white border border-slate-200/80 rounded-xl p-3 shadow-xs">
            <div className="text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-2 flex items-center justify-between border-b border-slate-100 pb-1.5">
              <span className="flex items-center gap-1 text-purple-800">
                <Mic className="w-3.5 h-3.5 text-purple-600" /> Speech-to-Text (STT)
              </span>
              <span className="text-[10px] font-semibold text-purple-700 bg-purple-50 border border-purple-200 px-1.5 py-0.2 rounded-full">
                {models.stt!.length} active
              </span>
            </div>
            <div className="space-y-2">
              {models.stt!.map((item, idx) => (
                <div
                  key={idx}
                  className="flex items-center justify-between bg-slate-50/70 hover:bg-slate-50 px-2.5 py-1.5 rounded-lg border border-slate-200/60 text-xs transition-colors"
                >
                  <div className="min-w-0 pr-2">
                    <div className="font-semibold text-slate-800 text-[11px] truncate flex items-center gap-1">
                      <span>{formatVendorName(item.provider)}</span>
                      <span className="font-mono text-[10px] text-slate-500 font-normal bg-white px-1.5 py-0.2 rounded border border-slate-200">
                        {item.model}
                      </span>
                    </div>
                    <div className="text-[10px] text-slate-500 mt-0.5">
                      <span className="font-medium text-slate-700">{item.calls}</span>{" "}
                      {item.calls === 1 ? "call" : "calls"} • {formatMinutes(item.minutes)}
                    </div>
                  </div>
                  <div className="text-right shrink-0 font-bold text-xs text-slate-900">
                    {inr(item.cost_inr)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function PlanBucketRow({ b }: { b: PlanBucket }) {
  const planInfo = PLAN_STYLES[b.plan] || {
    label: b.plan,
    badge: "bg-slate-100 text-slate-700 border-slate-200",
  };

  return (
    <div className="border border-slate-200/80 rounded-xl overflow-hidden bg-white shadow-xs">
      {/* Bucket Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 bg-slate-50/80 border-b border-slate-100">
        <div className="flex items-center gap-2.5">
          <span
            className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider border ${planInfo.badge}`}
          >
            {planInfo.label} Plan
          </span>
          <span className="text-xs text-slate-600 font-medium">
            {b.calls} calls · {b.total_minutes.toFixed(1)} mins
          </span>
        </div>

        <div className="flex items-center gap-4 text-right">
          <div>
            <div className="text-xs font-bold text-emerald-700">{inr(b.revenue_inr)}</div>
            <div className="text-[10px] text-slate-400">billed</div>
          </div>
          <div>
            <div className="text-xs font-bold text-slate-700">{inr(b.cost_inr)}</div>
            <div className="text-[10px] text-slate-400">cogs</div>
          </div>
          <div>
            <div
              className={`text-xs font-bold ${b.profit_inr >= 0 ? "text-emerald-700" : "text-rose-600"
                }`}
            >
              {inr(b.profit_inr)}
            </div>
            <div className="text-[10px] text-slate-400">profit</div>
          </div>
          <MarginPill margin={b.margin_pct} />
        </div>
      </div>

      {/* Bucket Content */}
      <div className="p-4 space-y-3.5 bg-white">
        <CostBar breakdown={b.breakdown} total={b.cost_inr} />
        <InfrastructureBento
          breakdown={b.breakdown}
          ttsProvider={b.tts_provider}
          totalCost={b.cost_inr}
        />
        <ModelsInventorySection models={b.models_used} />
      </div>
    </div>
  );
}

function CustomerRowCard({
  c,
  isOpen,
  onToggle,
}: {
  c: CustomerRow;
  isOpen: boolean;
  onToggle: () => void;
}) {
  const distinctPlans = Array.from(new Set(c.plan_breakdown?.map((b) => b.plan) || []));
  const hasMultiplePlans = distinctPlans.length > 1;

  const planInfo = PLAN_STYLES[c.plan] || {
    label: c.plan,
    badge: "bg-slate-100 text-slate-700 border-slate-200",
  };

  const initials = getInitials(c.company_name);
  const avatarClass = getAvatarColor(c.company_name);

  return (
    <div
      className={`border rounded-xl transition-all duration-200 overflow-hidden bg-white shadow-xs ${isOpen ? "border-slate-300 ring-2 ring-slate-100" : "border-slate-200/80 hover:border-slate-300"
        }`}
    >
      <button
        type="button"
        className="w-full flex items-center justify-between gap-4 px-4 py-3.5 hover:bg-slate-50/50 transition-colors text-left cursor-pointer"
        onClick={onToggle}
      >
        {/* Left Section: Company Avatar, Name, Email, Badges */}
        <div className="flex items-center gap-3.5 min-w-0 flex-1">
          <div
            className={`w-9 h-9 rounded-xl border flex items-center justify-center font-bold text-xs shrink-0 select-none ${avatarClass}`}
          >
            {initials}
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-bold text-sm text-slate-900 truncate">{c.company_name}</span>

              {hasMultiplePlans ? (
                <span className="text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider bg-purple-50 text-purple-800 border border-purple-200 flex items-center gap-1">
                  <Sparkles className="w-2.5 h-2.5 text-purple-600" />
                  Mixed Plans ({distinctPlans.length})
                </span>
              ) : (
                <span
                  className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider border ${planInfo.badge}`}
                >
                  {planInfo.label}
                </span>
              )}

              {c.status && c.status !== "active" && (
                <span className="text-[10px] font-semibold px-1.5 py-0.2 rounded bg-amber-50 text-amber-800 border border-amber-200 uppercase">
                  {c.status}
                </span>
              )}
            </div>

            <div className="flex items-center gap-3 text-xs text-slate-400 mt-0.5">
              {c.contact_email && <span className="truncate">{c.contact_email}</span>}
              {c.contact_email && <span>•</span>}
              <span className="font-medium text-slate-600 flex items-center gap-1">
                <Phone className="w-3 h-3 text-slate-400" />
                {c.calls} calls · {c.total_minutes.toFixed(1)} mins
              </span>
            </div>
          </div>
        </div>

        {/* Right Section: Financial Columns & Margin */}
        <div className="flex items-center gap-4 sm:gap-6 shrink-0">
          <div className="text-right hidden sm:block">
            <div className="text-xs font-bold text-emerald-700">{inr(c.revenue_inr)}</div>
            <div className="text-[10px] text-slate-400">billed</div>
          </div>

          <div className="text-right hidden sm:block">
            <div className="text-xs font-bold text-slate-700">{inr(c.cost_inr)}</div>
            <div className="text-[10px] text-slate-400">est. cost</div>
          </div>

          <div className="text-right">
            <div
              className={`text-xs font-black ${c.profit_inr >= 0 ? "text-emerald-700" : "text-rose-600"
                }`}
            >
              {inr(c.profit_inr)}
            </div>
            <div className="text-[10px] text-slate-400">gross profit</div>
          </div>

          <div className="w-20 text-right">
            <MarginPill margin={c.margin_pct} />
          </div>

          <div className="w-6 flex items-center justify-end text-slate-400">
            {isOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </div>
        </div>
      </button>

      {/* Expanded Unit Economics Drawer */}
      {isOpen && (
        <div className="px-5 py-4 bg-slate-50/70 border-t border-slate-100 space-y-4">
          {hasMultiplePlans ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2 p-2.5 bg-purple-50/60 border border-purple-200/80 rounded-xl text-xs text-purple-900">
                <Info className="w-4 h-4 text-purple-600 shrink-0" />
                <span>
                  <strong>Multi-Plan History:</strong> Customer transitioned between subscription
                  plans during this timeframe. Below is the audited breakdown for each plan segment:
                </span>
              </div>
              <div className="space-y-3">
                {c.plan_breakdown.map((b) => (
                  <PlanBucketRow key={`${b.plan}-${b.tts_provider}`} b={b} />
                ))}
              </div>
            </div>
          ) : (
            <>
              <CostBar breakdown={c.breakdown} total={c.cost_inr} />
              <InfrastructureBento
                breakdown={c.breakdown}
                ttsProvider={c.plan_breakdown?.[0]?.tts_provider}
                totalCost={c.cost_inr}
              />
              <ModelsInventorySection models={c.models_used} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

const PERIODS = [
  { value: "today", label: "Today" },
  { value: "week", label: "Last 7 Days" },
  { value: "month", label: "This Month" },
  { value: "all", label: "All Time" },
];

export default function ProfitabilityPage() {
  const [period, setPeriod] = useState("month");
  const [data, setData] = useState<ProfitData | null>(null);
  const [loading, setLoading] = useState(true);

  // Search & Filter State
  const [search, setSearch] = useState("");
  const [marginFilter, setMarginFilter] = useState<"all" | "healthy" | "moderate" | "loss">("all");
  const [sortField, setSortField] = useState<
    "profit_desc" | "profit_asc" | "revenue_desc" | "cost_desc" | "calls_desc" | "margin_desc"
  >("profit_desc");

  // Expanded Accordion State
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());

  // Global Settings Modal State
  const [isGlobalSettingsOpen, setIsGlobalSettingsOpen] = useState(false);
  const [globalSettingsInput, setGlobalSettingsInput] = useState("");
  const [globalSettingsLoading, setGlobalSettingsLoading] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  const overrides = useMemo(() => {
    try {
      return JSON.parse(globalSettingsInput || "{}");
    } catch {
      return {};
    }
  }, [globalSettingsInput]);

  const hasCustomOverrides = Object.keys(overrides).length > 0;

  const setOverride = (key: string, value: string) => {
    const num = parseFloat(value);
    const newOverrides = { ...overrides };
    if (isNaN(num) || value.trim() === "") {
      delete newOverrides[key];
    } else {
      newOverrides[key] = num;
    }
    setGlobalSettingsInput(JSON.stringify(newOverrides, null, 2));
  };

  const fetchData = async (p: string) => {
    setLoading(true);
    try {
      const res = await adminFetch(`/admin/profitability?period=${p}`);
      if (res.ok) {
        const json = await res.json();
        setData(json);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData(period);
  }, [period]);

  const toggleCustomer = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (!data?.customers) return;
    if (expandedIds.size === data.customers.length) {
      setExpandedIds(new Set());
    } else {
      setExpandedIds(new Set(data.customers.map((c) => c.customer_id)));
    }
  };

  // Filtered & Sorted Customers
  const filteredCustomers = useMemo(() => {
    if (!data?.customers) return [];
    let list = [...data.customers];

    // Text Search
    if (search.trim()) {
      const q = search.toLowerCase().trim();
      list = list.filter(
        (c) =>
          c.company_name.toLowerCase().includes(q) ||
          (c.contact_email && c.contact_email.toLowerCase().includes(q)) ||
          c.plan.toLowerCase().includes(q)
      );
    }

    // Margin Health Filter
    if (marginFilter === "healthy") {
      list = list.filter((c) => c.margin_pct >= 50);
    } else if (marginFilter === "moderate") {
      list = list.filter((c) => c.margin_pct >= 30 && c.margin_pct < 50);
    } else if (marginFilter === "loss") {
      list = list.filter((c) => c.margin_pct < 30 || c.profit_inr < 0);
    }

    // Sort
    list.sort((a, b) => {
      if (sortField === "profit_desc") return b.profit_inr - a.profit_inr;
      if (sortField === "profit_asc") return a.profit_inr - b.profit_inr;
      if (sortField === "revenue_desc") return b.revenue_inr - a.revenue_inr;
      if (sortField === "cost_desc") return b.cost_inr - a.cost_inr;
      if (sortField === "calls_desc") return b.calls - a.calls;
      if (sortField === "margin_desc") return b.margin_pct - a.margin_pct;
      return 0;
    });

    return list;
  }, [data, search, marginFilter, sortField]);

  const s = data?.summary;

  return (
    <div className="space-y-6 pb-12">
      {/* ── 1. PAGE HEADER & QUICK ACTIONS ── */}
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4 pb-4 border-b border-slate-200/80">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-black tracking-tight text-slate-900">
              Call Profitability & Unit Economics
            </h1>
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              Live Audited Telemetry
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            Reconciles customer billing against upstream carrier (Plivo) and AI wholesale inference
            costs.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Segmented Period Switcher */}
          <div className="flex items-center bg-slate-100 p-1 rounded-xl border border-slate-200/70 text-xs">
            {PERIODS.map((p) => (
              <button
                key={p.value}
                onClick={() => setPeriod(p.value)}
                className={`px-3 py-1.5 rounded-lg transition-all font-semibold cursor-pointer ${period === p.value
                    ? "bg-white text-slate-900 shadow-xs"
                    : "text-slate-600 hover:text-slate-900"
                  }`}
              >
                {p.label}
              </button>
            ))}
          </div>

          {/* Refresh Button */}
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchData(period)}
            className="h-9 w-9 p-0 border-slate-200 bg-white hover:bg-slate-50 text-slate-700 shadow-xs cursor-pointer rounded-xl"
            title="Refresh metrics"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin text-[#fe6905]" : ""}`} />
          </Button>

          <div className="h-5 w-px bg-slate-200 mx-1 hidden sm:block" />

          {/* Test Accounts Excluded Pill */}
          <span className="text-[10px] font-bold text-slate-500 bg-slate-100 px-2.5 py-1.5 rounded-lg uppercase tracking-wider border border-slate-200/60 whitespace-nowrap">
            Test Accounts Excluded
          </span>

          {/* Global Overrides Button */}
          <Button
            variant="outline"
            size="sm"
            onClick={async () => {
              try {
                setGlobalSettingsLoading(true);
                const res = await adminFetch("/admin/platform-settings");
                if (res.ok) {
                  const data = await res.json();
                  const currentOverrides = data.settings?.profitability_overrides || {};
                  setGlobalSettingsInput(JSON.stringify(currentOverrides, null, 2));
                  setIsGlobalSettingsOpen(true);
                }
              } catch (e) {
                console.error(e);
              } finally {
                setGlobalSettingsLoading(false);
              }
            }}
            className="h-9 text-xs border-slate-200 bg-white hover:bg-slate-50 text-slate-700 font-semibold cursor-pointer rounded-xl shadow-xs whitespace-nowrap"
          >
            <SlidersHorizontal className="w-3.5 h-3.5 mr-1.5 text-slate-500" />
            Cost Overrides
            {hasCustomOverrides && (
              <span className="ml-1.5 w-2 h-2 rounded-full bg-[#fe6905]" />
            )}
          </Button>
        </div>
      </div>

      {/* ── 2. EXECUTIVE KPI CARDS (BENTO GRID) ── */}
      {s && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3.5">
          {/* Total Calls */}
          <Card className="bg-white border border-slate-200/80 rounded-2xl p-4 shadow-xs">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                Total Calls
              </span>
              <div className="p-1.5 rounded-lg bg-indigo-50 text-indigo-600 border border-indigo-100">
                <PhoneCall className="h-3.5 w-3.5" />
              </div>
            </div>
            <div className="text-2xl font-black text-slate-900 mt-2">
              {s.total_calls.toLocaleString("en-IN")}
            </div>
            <p className="text-[11px] text-slate-400 mt-1">Processed customer runs</p>
          </Card>

          {/* Wallet Topups */}
          <Card className="bg-white border border-slate-200/80 rounded-2xl p-4 shadow-xs">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                Wallet Topups
              </span>
              <div className="p-1.5 rounded-lg bg-sky-50 text-sky-600 border border-sky-100">
                <Wallet className="h-3.5 w-3.5" />
              </div>
            </div>
            <div className="text-2xl font-black text-sky-700 mt-2">
              {inr(s.total_topups_inr)}
            </div>
            <p className="text-[11px] text-slate-400 mt-1">Prepaid wallet float</p>
          </Card>

          {/* Revenue Billed */}
          <Card className="bg-white border border-slate-200/80 rounded-2xl p-4 shadow-xs">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                Revenue Billed
              </span>
              <div className="p-1.5 rounded-lg bg-emerald-50 text-emerald-600 border border-emerald-100">
                <TrendingUp className="h-3.5 w-3.5" />
              </div>
            </div>
            <div className="text-2xl font-black text-emerald-700 mt-2">
              {inr(s.total_revenue_inr)}
            </div>
            <p className="text-[11px] text-slate-400 mt-1">Metered call minutes</p>
          </Card>

          {/* Est. AI & Telephony Cost */}
          <Card className="bg-white border border-slate-200/80 rounded-2xl p-4 shadow-xs">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                Est. AI & Carrier
              </span>
              <div className="p-1.5 rounded-lg bg-rose-50 text-rose-600 border border-rose-100">
                <TrendingDown className="h-3.5 w-3.5" />
              </div>
            </div>
            <div className="text-2xl font-black text-rose-600 mt-2">
              {inr(s.total_cost_inr)}
            </div>
            <p className="text-[11px] text-slate-400 mt-1">Upstream COGS</p>
          </Card>

          {/* Gross Profit */}
          <Card className="bg-gradient-to-br from-white to-emerald-50/40 border border-emerald-200/80 rounded-2xl p-4 shadow-xs">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-semibold text-emerald-800 uppercase tracking-wider">
                Gross Profit
              </span>
              <div className="p-1.5 rounded-lg bg-emerald-100 text-emerald-700 border border-emerald-200">
                <DollarSign className="h-3.5 w-3.5" />
              </div>
            </div>
            <div
              className={`text-2xl font-black mt-2 ${s.gross_profit_inr >= 0 ? "text-emerald-700" : "text-rose-600"
                }`}
            >
              {inr(s.gross_profit_inr)}
            </div>
            <p className="text-[11px] text-emerald-700/80 mt-1">Retained margin</p>
          </Card>

          {/* Gross Margin % */}
          <Card className="bg-gradient-to-br from-white to-blue-50/40 border border-blue-200/80 rounded-2xl p-4 shadow-xs">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-semibold text-blue-800 uppercase tracking-wider">
                Gross Margin
              </span>
              <div className="p-1.5 rounded-lg bg-blue-100 text-blue-700 border border-blue-200">
                <Percent className="h-3.5 w-3.5" />
              </div>
            </div>
            <div
              className={`text-2xl font-black mt-2 ${s.gross_margin_pct >= 50
                  ? "text-emerald-700"
                  : s.gross_margin_pct >= 30
                    ? "text-amber-700"
                    : "text-rose-600"
                }`}
            >
              {pct(s.gross_margin_pct)}
            </div>
            <div className="mt-2 w-full bg-slate-100 h-1.5 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${s.gross_margin_pct >= 50
                    ? "bg-emerald-500"
                    : s.gross_margin_pct >= 30
                      ? "bg-amber-500"
                      : "bg-rose-500"
                  }`}
                style={{ width: `${Math.max(0, Math.min(100, s.gross_margin_pct))}%` }}
              />
            </div>
          </Card>
        </div>
      )}

      {/* ── 3. CUSTOMER LIST TOOLBAR (SEARCH, HEALTH FILTER, SORT) ── */}
      <div className="space-y-3 pt-2">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-bold text-slate-900 tracking-tight">
              Customer Profitability Breakdown
            </h2>
            {data?.customers && (
              <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 border border-slate-200">
                {filteredCustomers.length} of {data.customers.length} Accounts
              </span>
            )}
          </div>

          {/* Quick Expand All Toggle */}
          {data?.customers && data.customers.length > 0 && (
            <button
              onClick={toggleAll}
              className="text-xs text-slate-500 hover:text-slate-800 font-medium flex items-center gap-1 cursor-pointer transition-colors"
            >
              {expandedIds.size === data.customers.length ? "Collapse All" : "Expand All"}
            </button>
          )}
        </div>

        {/* Filter Controls Row */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 bg-white p-3 rounded-2xl border border-slate-200/80 shadow-xs">
          {/* Search Box */}
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400 pointer-events-none" />
            <input
              type="text"
              placeholder="Search by company, email, or plan..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-9 pr-8 h-9 text-xs bg-slate-50 border border-slate-200 rounded-xl text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-[#fe6905]/20 focus:border-[#fe6905] transition-all"
            />
            {search && (
              <button
                onClick={() => setSearch("")}
                className="absolute right-2.5 top-2.5 text-slate-400 hover:text-slate-600"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {/* Margin Health Filter Pills */}
            <div className="flex items-center bg-slate-100 p-1 rounded-xl text-xs border border-slate-200/60">
              <button
                onClick={() => setMarginFilter("all")}
                className={`px-2.5 py-1 rounded-lg font-semibold transition-all cursor-pointer ${marginFilter === "all"
                    ? "bg-white text-slate-900 shadow-xs"
                    : "text-slate-600 hover:text-slate-900"
                  }`}
              >
                All
              </button>
              <button
                onClick={() => setMarginFilter("healthy")}
                className={`px-2.5 py-1 rounded-lg font-semibold transition-all cursor-pointer flex items-center gap-1 ${marginFilter === "healthy"
                    ? "bg-white text-emerald-700 shadow-xs"
                    : "text-slate-600 hover:text-slate-900"
                  }`}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                Healthy (≥50%)
              </button>
              <button
                onClick={() => setMarginFilter("moderate")}
                className={`px-2.5 py-1 rounded-lg font-semibold transition-all cursor-pointer flex items-center gap-1 ${marginFilter === "moderate"
                    ? "bg-white text-amber-700 shadow-xs"
                    : "text-slate-600 hover:text-slate-900"
                  }`}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
                Moderate (30-50%)
              </button>
              <button
                onClick={() => setMarginFilter("loss")}
                className={`px-2.5 py-1 rounded-lg font-semibold transition-all cursor-pointer flex items-center gap-1 ${marginFilter === "loss"
                    ? "bg-white text-rose-700 shadow-xs"
                    : "text-slate-600 hover:text-slate-900"
                  }`}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-rose-500" />
                Loss / At Risk (&lt;30%)
              </button>
            </div>

            {/* Sort Dropdown */}
            <div className="flex items-center gap-1.5 bg-slate-50 border border-slate-200 px-2.5 py-1.5 rounded-xl text-xs text-slate-600">
              <ArrowUpDown className="w-3.5 h-3.5 text-slate-400" />
              <select
                value={sortField}
                onChange={(e) => setSortField(e.target.value as any)}
                className="bg-transparent font-medium text-slate-700 focus:outline-none cursor-pointer"
              >
                <option value="profit_desc">Highest Profit</option>
                <option value="profit_asc">Lowest Profit</option>
                <option value="revenue_desc">Highest Revenue</option>
                <option value="cost_desc">Highest Cost</option>
                <option value="calls_desc">Most Calls</option>
                <option value="margin_desc">Highest Margin %</option>
              </select>
            </div>
          </div>
        </div>

        {/* ── 4. CUSTOMER CARDS LIST ── */}
        {loading ? (
          <div className="text-center py-16 bg-white rounded-2xl border border-slate-200/80 shadow-xs">
            <div className="w-6 h-6 border-2 border-[#fe6905] border-t-transparent rounded-full animate-spin mx-auto mb-3" />
            <div className="text-xs font-semibold text-slate-700">
              Aggregating live call telemetry & wholesale costs...
            </div>
            <div className="text-[11px] text-slate-400 mt-1">
              Parsing LLM tokens, TTS voice audio and STT minutes
            </div>
          </div>
        ) : filteredCustomers.length === 0 ? (
          <div className="text-center py-16 bg-white rounded-2xl border border-slate-200/80 shadow-xs text-xs text-slate-500 space-y-2">
            <Phone className="w-8 h-8 text-slate-300 mx-auto" />
            <div className="font-semibold text-slate-700">No customer call activity found</div>
            <p className="text-slate-400 max-w-sm mx-auto">
              {search || marginFilter !== "all"
                ? "No accounts match your search or margin health filter. Try clearing your filters."
                : "No call records exist for the selected timeframe."}
            </p>
            {(search || marginFilter !== "all") && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setSearch("");
                  setMarginFilter("all");
                }}
                className="h-8 text-xs mt-2"
              >
                Reset Filters
              </Button>
            )}
          </div>
        ) : (
          <div className="space-y-2.5">
            {/* Table Header Bar for larger screens */}
            <div className="hidden sm:flex items-center justify-between px-5 py-1 text-[10px] font-bold text-slate-400 uppercase tracking-wider">
              <div className="flex-1">Account & Plan</div>
              <div className="flex items-center gap-6 pr-6">
                <div className="w-20 text-right">Billed</div>
                <div className="w-20 text-right">Est. Cost</div>
                <div className="w-24 text-right">Gross Profit</div>
                <div className="w-20 text-right">Margin</div>
              </div>
            </div>

            {filteredCustomers.map((c) => (
              <CustomerRowCard
                key={c.customer_id}
                c={c}
                isOpen={expandedIds.has(c.customer_id)}
                onToggle={() => toggleCustomer(c.customer_id)}
              />
            ))}
          </div>
        )}
      </div>

      {/* ── 5. COST AUDIT FOOTNOTE & MODEL ASSUMPTIONS ── */}
      {data && (
        <div className="text-xs text-slate-500 border border-slate-200/80 rounded-2xl p-4 bg-white shadow-xs space-y-2">
          <div className="flex items-center gap-2 font-bold text-slate-800 text-xs">
            <Sparkles className="w-4 h-4 text-[#fe6905]" />
            Audited Upstream Unit Economics & Pricing Model
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3 pt-1 text-[11px] leading-relaxed">
            <div className="p-2.5 bg-slate-50 rounded-xl border border-slate-100">
              <span className="font-semibold text-slate-700 block mb-0.5">📞 Telephony</span>
              Plivo India: ₹0.45/min standard (₹0.30 inbound, ₹1.80 intl). Unanswered or 0s calls incur ₹0.
            </div>
            <div className="p-2.5 bg-slate-50 rounded-xl border border-slate-100">
              <span className="font-semibold text-slate-700 block mb-0.5">🎤 Speech (STT)</span>
              Deepgram Flux & Nova-3 ($0.0048/min) · Smallest AI STT (~$0.003/min) · Sarvam (₹0.49/min).
            </div>
            <div className="p-2.5 bg-slate-50 rounded-xl border border-slate-100">
              <span className="font-semibold text-slate-700 block mb-0.5">🔊 Voice (TTS)</span>
              Smallest AI Lightning v3.1 ($0.0175/1k chars) · ElevenLabs ($0.040-$0.18/1k chars) · Deepgram Aura ($0.015/1k chars).
            </div>
            <div className="p-2.5 bg-slate-50 rounded-xl border border-slate-100">
              <span className="font-semibold text-slate-700 block mb-0.5">🤖 Intelligence (LLM)</span>
              GPT-4.1-mini & GPT-4o-mini ($0.15 in / $0.60 out per 1M tok). USD/INR baseline ₹88.00.
            </div>
          </div>
          <div className="text-[11px] text-amber-700/90 pt-1 flex items-center gap-1.5 font-medium">
            <AlertTriangle className="w-3.5 h-3.5 text-amber-600 shrink-0" />
            <span>
              All costs are derived from live measured call telemetry. Reconcile monthly against
              upstream vendor invoices.
            </span>
          </div>
        </div>
      )}

      {/* ── 6. GLOBAL PROFITABILITY OVERRIDES MODAL ── */}
      {isGlobalSettingsOpen && (
        <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden animate-in fade-in zoom-in-95 duration-200 border border-slate-200">
            {/* Modal Header */}
            <div className="px-6 py-4.5 border-b border-slate-100 flex items-center justify-between bg-slate-50/50">
              <div>
                <h3 className="font-bold text-slate-900 text-sm flex items-center gap-2">
                  <SlidersHorizontal className="w-4 h-4 text-[#fe6905]" />
                  Global Cost Rate Overrides
                </h3>
                <p className="text-xs text-slate-500 mt-0.5">
                  Override baseline upstream wholesale pricing across all customer profitability audits.
                </p>
              </div>
              <button
                onClick={() => setIsGlobalSettingsOpen(false)}
                className="p-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Modal Body */}
            <div className="p-6 max-h-[65vh] overflow-y-auto space-y-5 text-xs">
              {/* Telephony & FX */}
              <div className="space-y-2.5">
                <h4 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                  <Phone className="w-3.5 h-3.5 text-blue-500" /> Telephony & Currency Exchange
                </h4>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-[10px] font-semibold text-slate-700 uppercase">
                      Carrier Cost / Min (₹)
                    </label>
                    <input
                      type="number"
                      step="0.01"
                      placeholder="Default: 0.45"
                      className="w-full h-8 px-2.5 mt-1 border border-slate-200 rounded-lg text-xs bg-white focus:outline-none focus:ring-1 focus:ring-[#fe6905] focus:border-[#fe6905]"
                      value={overrides.telephony_cost_per_min_inr ?? ""}
                      onChange={(e) => setOverride("telephony_cost_per_min_inr", e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="text-[10px] font-semibold text-slate-700 uppercase">
                      USD to INR Exchange Rate
                    </label>
                    <input
                      type="number"
                      step="0.1"
                      placeholder="Default: 88.00"
                      className="w-full h-8 px-2.5 mt-1 border border-slate-200 rounded-lg text-xs bg-white focus:outline-none focus:ring-1 focus:ring-[#fe6905] focus:border-[#fe6905]"
                      value={overrides.usd_to_inr ?? ""}
                      onChange={(e) => setOverride("usd_to_inr", e.target.value)}
                    />
                  </div>
                </div>
              </div>

              {/* STT */}
              <div className="space-y-2.5">
                <h4 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                  <Mic className="w-3.5 h-3.5 text-purple-500" /> Speech-to-Text (STT) - $/minute
                </h4>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-[10px] font-semibold text-slate-700 uppercase">
                      Deepgram STT ($/min)
                    </label>
                    <input
                      type="number"
                      step="0.0001"
                      placeholder="Default: 0.0048"
                      className="w-full h-8 px-2.5 mt-1 border border-slate-200 rounded-lg text-xs bg-white focus:outline-none focus:ring-1 focus:ring-[#fe6905] focus:border-[#fe6905]"
                      value={overrides.deepgram_stt_cost_per_min_usd ?? ""}
                      onChange={(e) => setOverride("deepgram_stt_cost_per_min_usd", e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="text-[10px] font-semibold text-slate-700 uppercase">
                      Smallest AI STT ($/min)
                    </label>
                    <input
                      type="number"
                      step="0.0001"
                      placeholder="Default: 0.0030"
                      className="w-full h-8 px-2.5 mt-1 border border-slate-200 rounded-lg text-xs bg-white focus:outline-none focus:ring-1 focus:ring-[#fe6905] focus:border-[#fe6905]"
                      value={overrides.smallest_stt_cost_per_min_usd ?? ""}
                      onChange={(e) => setOverride("smallest_stt_cost_per_min_usd", e.target.value)}
                    />
                  </div>
                </div>
              </div>

              {/* TTS */}
              <div className="space-y-2.5">
                <h4 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                  <Volume2 className="w-3.5 h-3.5 text-amber-500" /> Text-to-Speech (TTS) - $/1k chars
                </h4>
                <div className="grid grid-cols-3 gap-2.5">
                  <div>
                    <label className="text-[10px] font-semibold text-slate-700 uppercase">
                      Smallest AI
                    </label>
                    <input
                      type="number"
                      step="0.001"
                      placeholder="Default: 0.0175"
                      className="w-full h-8 px-2.5 mt-1 border border-slate-200 rounded-lg text-xs bg-white focus:outline-none focus:ring-1 focus:ring-[#fe6905] focus:border-[#fe6905]"
                      value={overrides.smallest_tts_cost_per_1k_chars_usd ?? ""}
                      onChange={(e) =>
                        setOverride("smallest_tts_cost_per_1k_chars_usd", e.target.value)
                      }
                    />
                  </div>
                  <div>
                    <label className="text-[10px] font-semibold text-slate-700 uppercase">
                      ElevenLabs
                    </label>
                    <input
                      type="number"
                      step="0.001"
                      placeholder="Default: 0.18"
                      className="w-full h-8 px-2.5 mt-1 border border-slate-200 rounded-lg text-xs bg-white focus:outline-none focus:ring-1 focus:ring-[#fe6905] focus:border-[#fe6905]"
                      value={overrides.elevenlabs_tts_cost_per_1k_chars_usd ?? ""}
                      onChange={(e) =>
                        setOverride("elevenlabs_tts_cost_per_1k_chars_usd", e.target.value)
                      }
                    />
                  </div>
                  <div>
                    <label className="text-[10px] font-semibold text-slate-700 uppercase">
                      Deepgram TTS
                    </label>
                    <input
                      type="number"
                      step="0.001"
                      placeholder="Default: 0.015"
                      className="w-full h-8 px-2.5 mt-1 border border-slate-200 rounded-lg text-xs bg-white focus:outline-none focus:ring-1 focus:ring-[#fe6905] focus:border-[#fe6905]"
                      value={overrides.deepgram_tts_cost_per_1k_chars_usd ?? ""}
                      onChange={(e) =>
                        setOverride("deepgram_tts_cost_per_1k_chars_usd", e.target.value)
                      }
                    />
                  </div>
                </div>
              </div>

              {/* LLM Inference */}
              <div className="space-y-2.5">
                <h4 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                  <Bot className="w-3.5 h-3.5 text-emerald-500" /> Conversational LLM - $/1M tokens
                </h4>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-[10px] font-semibold text-slate-700 uppercase">
                      Prompt / Input Tokens
                    </label>
                    <input
                      type="number"
                      step="0.01"
                      placeholder="Default: 0.15"
                      className="w-full h-8 px-2.5 mt-1 border border-slate-200 rounded-lg text-xs bg-white focus:outline-none focus:ring-1 focus:ring-[#fe6905] focus:border-[#fe6905]"
                      value={overrides.llm_cost_per_1m_input_usd ?? ""}
                      onChange={(e) => setOverride("llm_cost_per_1m_input_usd", e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="text-[10px] font-semibold text-slate-700 uppercase">
                      Completion / Output Tokens
                    </label>
                    <input
                      type="number"
                      step="0.01"
                      placeholder="Default: 0.60"
                      className="w-full h-8 px-2.5 mt-1 border border-slate-200 rounded-lg text-xs bg-white focus:outline-none focus:ring-1 focus:ring-[#fe6905] focus:border-[#fe6905]"
                      value={overrides.llm_cost_per_1m_output_usd ?? ""}
                      onChange={(e) => setOverride("llm_cost_per_1m_output_usd", e.target.value)}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Modal Footer */}
            <div className="px-6 py-4 bg-slate-50 border-t border-slate-100 flex items-center justify-between">
              <button
                type="button"
                onClick={() => setGlobalSettingsInput("{}")}
                className="text-xs text-rose-600 hover:text-rose-700 font-semibold cursor-pointer"
              >
                Reset to Defaults
              </button>

              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  onClick={() => setIsGlobalSettingsOpen(false)}
                  className="h-8 text-xs rounded-lg"
                >
                  Cancel
                </Button>
                <Button
                  onClick={async () => {
                    try {
                      const parsed = JSON.parse(globalSettingsInput);
                      setGlobalSettingsLoading(true);
                      const res = await adminFetch("/admin/platform-settings", {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ settings: { profitability_overrides: parsed } }),
                      });
                      setGlobalSettingsLoading(false);
                      if (res.ok) {
                        setSaveSuccess(true);
                        setTimeout(() => setSaveSuccess(false), 2000);
                        setIsGlobalSettingsOpen(false);
                        fetchData(period);
                      } else {
                        alert("Failed to save overrides.");
                      }
                    } catch (e) {
                      alert("Invalid JSON format.");
                    }
                  }}
                  disabled={globalSettingsLoading}
                  className="h-8 text-xs bg-[#fe6905] hover:bg-[#e55e04] text-white font-semibold rounded-lg shadow-xs"
                >
                  {globalSettingsLoading ? "Saving..." : "Save Overrides"}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
