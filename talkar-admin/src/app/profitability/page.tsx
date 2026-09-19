"use client";

import { adminFetch } from "@/lib/api";
import React, { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  TrendingUp,
  TrendingDown,
  DollarSign,
  Phone,
  Clock,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Settings2,
  X,
} from "lucide-react";

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
  };
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
  };
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

const inr = (v: number) =>
  `₹${v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const pct = (v: number) => `${v.toFixed(1)}%`;

const marginColor = (m: number) => {
  if (m >= 50) return "text-emerald-700";
  if (m >= 30) return "text-amber-700";
  return "text-red-700";
};

const marginBg = (m: number) => {
  if (m >= 50) return "bg-emerald-50 text-emerald-700 border border-emerald-200";
  if (m >= 30) return "bg-amber-50 text-amber-800 border border-amber-200";
  return "bg-red-50 text-red-700 border border-red-200";
};

function CostBar({ breakdown, total }: { breakdown: CustomerRow["breakdown"]; total: number }) {
  if (total === 0) return null;
  const segments = [
    { label: "Plivo", value: breakdown.plivo_inr, color: "bg-blue-500" },
    { label: "STT", value: breakdown.stt_inr, color: "bg-purple-500" },
    { label: "TTS", value: breakdown.tts_inr, color: "bg-amber-500" },
    { label: "LLM", value: breakdown.llm_inr, color: "bg-emerald-500" },
  ];
  return (
    <div className="flex gap-2 items-center">
      <div className="flex h-2 flex-1 rounded-full overflow-hidden gap-0.5 bg-slate-100">
        {segments.map((s) => (
          <div
            key={s.label}
            className={`${s.color} transition-all`}
            style={{ width: `${(s.value / total) * 100}%` }}
            title={`${s.label}: ${inr(s.value)}`}
          />
        ))}
      </div>
      <div className="flex gap-2 text-[10px] text-slate-500">
        {segments.map((s) => (
          <span key={s.label} className="flex items-center gap-1">
            <span className={`inline-block w-1.5 h-1.5 rounded-full ${s.color}`} />
            {s.label}
          </span>
        ))}
      </div>

    </div>
  );
}

function PlanBucketRow({ b }: { b: PlanBucket }) {
  const sttLabel = b.tts_provider.includes("smallest") ? "🎤 STT (Smallest AI)" : "🎤 STT (Deepgram)";
  const ttsLabel = b.tts_provider.includes("smallest") ? "🔊 TTS (Smallest AI)" : b.tts_provider.includes("elevenlabs") ? "🔊 TTS (ElevenLabs)" : "🔊 TTS (Deepgram)";
  const planColors: Record<string, string> = {
    starter: "bg-slate-100 text-slate-700 border-slate-200",
    growth: "bg-emerald-50 text-emerald-800 border-emerald-200",
    pro: "bg-amber-50 text-amber-800 border-amber-200",
    elite: "bg-purple-50 text-purple-800 border-purple-200",
  };
  return (
    <div className="border border-slate-200 rounded-lg overflow-hidden bg-white text-xs">
      <div className="flex items-center gap-4 px-4 py-2.5 bg-slate-50/60 border-b border-slate-100">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider border ${planColors[b.plan] ?? "bg-slate-100 text-slate-700"}`}>
              {b.plan}
            </span>
            <span className="text-xs text-slate-500">{b.calls} calls · {b.total_minutes.toFixed(1)} min</span>
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-xs font-semibold text-emerald-700">{inr(b.revenue_inr)}</div>
          <div className="text-[10px] text-slate-400">billed</div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-xs font-semibold text-red-600">{inr(b.cost_inr)}</div>
          <div className="text-[10px] text-slate-400">cost</div>
        </div>
        <div className="text-right shrink-0">
          <div className={`text-xs font-bold ${b.profit_inr >= 0 ? "text-emerald-700" : "text-red-600"}`}>{inr(b.profit_inr)}</div>
          <div className="text-[10px] text-slate-400">profit</div>
        </div>
        <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${marginBg(b.margin_pct)}`}>{pct(b.margin_pct)}</span>
      </div>
      <div className="px-4 py-3 bg-white">
        <CostBar breakdown={b.breakdown} total={b.cost_inr} />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-2.5">
          {[
            { label: "📞 Plivo", value: b.breakdown.plivo_inr },
            { label: sttLabel, value: b.breakdown.stt_inr },
            { label: ttsLabel, value: b.breakdown.tts_inr },
            { label: "🤖 LLM", value: b.breakdown.llm_inr },
          ].map((item) => (
            <div key={item.label} className="bg-slate-50/70 border border-slate-200 rounded-md p-2 text-center">
              <div className="text-[10px] text-slate-500">{item.label}</div>
              <div className="font-semibold text-xs text-slate-900 mt-0.5">{inr(item.value)}</div>
            </div>
          ))}
        </div>
      </div>

    </div>
  );
}

function CustomerRowCard({ c }: { c: CustomerRow }) {
  const [open, setOpen] = useState(false);
  const isGrowth = c.plan === "growth";
  const isPro = c.plan === "pro" || c.plan === "elite";
  const planColors: Record<string, string> = {
    starter: "bg-slate-100 text-slate-700 border-slate-200",
    growth: "bg-emerald-50 text-emerald-800 border-emerald-200",
    pro: "bg-amber-50 text-amber-800 border-amber-200",
    elite: "bg-purple-50 text-purple-800 border-purple-200",
  };
  const hasMultiplePlans = c.plan_breakdown && c.plan_breakdown.length > 1;
  return (
    <div className="border border-slate-200/80 rounded-xl overflow-hidden bg-white shadow-none">
      <button
        className="w-full flex items-center gap-4 px-4 py-3 hover:bg-slate-50/60 transition-colors text-left cursor-pointer"
        onClick={() => setOpen((v) => !v)}
      >
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-xs text-slate-900 truncate flex items-center gap-2">
            {c.company_name}
            {hasMultiplePlans ? (
              <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full uppercase tracking-wider bg-blue-50 text-blue-800 border border-blue-200">
                mixed plans
              </span>
            ) : (
              <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full uppercase tracking-wider border ${planColors[c.plan] ?? "bg-slate-100 text-slate-700"}`}>
                {c.plan}
              </span>
            )}
          </div>
          {c.contact_email && (
            <div className="text-[11px] text-slate-500 truncate mt-0.5">{c.contact_email}</div>
          )}
          <div className="text-[11px] text-slate-400 mt-0.5">
            {c.calls} calls · {c.total_minutes.toFixed(1)} min
            {c.status && c.status !== "active" && (
              <span className="ml-2 px-1.5 py-0.2 rounded text-[10px] font-medium bg-amber-50 text-amber-800 border border-amber-200">
                {c.status}
              </span>
            )}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-xs font-semibold text-emerald-700">{inr(c.revenue_inr)}</div>
          <div className="text-[10px] text-slate-400">billed</div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-xs font-semibold text-red-600">{inr(c.cost_inr)}</div>
          <div className="text-[10px] text-slate-400">cost</div>
        </div>
        <div className="text-right shrink-0">
          <div className={`text-xs font-bold ${c.profit_inr >= 0 ? "text-emerald-700" : "text-red-600"}`}>
            {inr(c.profit_inr)}
          </div>
          <div className="text-[10px] text-slate-400">profit</div>
        </div>
        <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${marginBg(c.margin_pct)}`}>
          {pct(c.margin_pct)}
        </span>
        {open ? <ChevronUp className="h-4 w-4 text-slate-400" /> : <ChevronDown className="h-4 w-4 text-slate-400" />}
      </button>

      {open && (
        <div className="px-4 py-3.5 bg-slate-50/70 border-t border-slate-100 space-y-3">
          {hasMultiplePlans ? (
            <>
              <p className="text-xs text-slate-600 font-medium">Customer used multiple subscription plans during this period:</p>
              <div className="space-y-2">
                {c.plan_breakdown.map((b) => (
                  <PlanBucketRow key={`${b.plan}-${b.tts_provider}`} b={b} />
                ))}
              </div>
            </>
          ) : (
            <>
              <CostBar breakdown={c.breakdown} total={c.cost_inr} />
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                {(c.plan_breakdown?.[0] ? [
                  { label: "📞 Plivo (telephony)", value: c.breakdown.plivo_inr },
                  { label: c.plan_breakdown[0].tts_provider.includes("smallest") ? "🎤 STT (Smallest AI)" : "🎤 STT (Deepgram)", value: c.breakdown.stt_inr },
                  { label: c.plan_breakdown[0].tts_provider.includes("smallest") ? "🔊 TTS (Smallest AI)" : isGrowth || isPro ? "🔊 TTS (ElevenLabs)" : "🔊 TTS (Deepgram)", value: c.breakdown.tts_inr },
                  { label: "🤖 LLM (OpenAI)", value: c.breakdown.llm_inr },
                ] : [
                  { label: "📞 Plivo (telephony)", value: c.breakdown.plivo_inr },
                  { label: "🎤 STT", value: c.breakdown.stt_inr },
                  { label: "🔊 TTS", value: c.breakdown.tts_inr },
                  { label: "🤖 LLM (OpenAI)", value: c.breakdown.llm_inr },
                ]).map((item) => (
                  <div key={item.label} className="bg-white border border-slate-200 rounded-lg p-2.5 text-center">
                    <div className="text-[10px] text-slate-500">{item.label}</div>
                    <div className="font-semibold text-xs text-slate-900 mt-1">{inr(item.value)}</div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}

    </div>
  );
}

const PERIODS = [
  { value: "today", label: "Today" },
  { value: "week", label: "Last 7 days" },
  { value: "month", label: "This month" },
  { value: "all", label: "All time" },
];

export default function ProfitabilityPage() {
  const [period, setPeriod] = useState("month");
  const [data, setData] = useState<ProfitData | null>(null);
  const [loading, setLoading] = useState(true);
  const [isGlobalSettingsOpen, setIsGlobalSettingsOpen] = useState(false);
  const [globalSettingsInput, setGlobalSettingsInput] = useState("");
  const [globalSettingsLoading, setGlobalSettingsLoading] = useState(false);

  const fetchData = async (p: string) => {
    setLoading(true);
    try {
      const res = await adminFetch(`/admin/profitability?period=${p}`);
      if (res.ok) setData(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData(period);
  }, [period]);

  const s = data?.summary;

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-2 border-b border-slate-200/70">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 flex items-center gap-2.5">
            Profitability Dashboard
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Revenue vs. estimated AI & telephony infrastructure costs based on live call usage.
          </p>
        </div>

        <div className="flex items-center gap-2">
          {/* Segmented Period Switcher */}
          <div className="flex items-center bg-slate-100 p-0.5 rounded-lg text-xs border border-slate-200/60">
            {PERIODS.map((p) => (
              <button
                key={p.value}
                onClick={() => setPeriod(p.value)}
                className={`px-3 py-1 rounded-md transition-all font-medium cursor-pointer ${
                  period === p.value
                    ? "bg-white text-slate-900 shadow-sm font-semibold"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>

          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchData(period)}
            className="h-8 w-8 p-0 border-slate-200 bg-white hover:bg-slate-50 text-slate-700 shadow-none cursor-pointer"
            title="Refresh metrics"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          </Button>
          
          <div className="h-4 w-px bg-slate-200 mx-1"></div>
          
          <span className="text-[10px] font-semibold text-slate-400 bg-slate-100 px-2 py-1 rounded-md uppercase tracking-wide whitespace-nowrap">Test Accounts Excluded</span>
          <Button 
            variant="outline" 
            size="sm" 
            onClick={async () => {
              try {
                setGlobalSettingsLoading(true);
                const res = await adminFetch("/admin/profitability/global-settings");
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
            className="h-8 text-xs border-slate-200 bg-white hover:bg-slate-50 text-slate-700 font-medium cursor-pointer whitespace-nowrap"
          >
            <Settings2 className="w-3.5 h-3.5 mr-1.5" /> Global Overrides
          </Button>
        </div>
      </div>

      {/* Summary Cards */}
      {s && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3.5">
          <Card className="bg-white border border-slate-200/80 rounded-xl p-4 shadow-none">
            <div className="text-[11px] text-slate-500 font-medium">Total Calls</div>
            <div className="text-xl font-bold text-slate-900 mt-1 flex items-center gap-1.5">
              <Phone className="h-4 w-4 text-slate-400" />
              {s.total_calls}
            </div>
          </Card>

          <Card className="bg-white border border-slate-200/80 rounded-xl p-4 shadow-none">
            <div className="text-[11px] text-slate-500 font-medium">Wallet Topups</div>
            <div className="text-xl font-bold text-blue-700 mt-1">{inr(s.total_topups_inr)}</div>
          </Card>

          <Card className="bg-white border border-slate-200/80 rounded-xl p-4 shadow-none">
            <div className="text-[11px] text-emerald-700 font-medium flex items-center gap-1">
              <TrendingUp className="h-3.5 w-3.5" /> Revenue Billed
            </div>
            <div className="text-xl font-bold text-emerald-700 mt-1">{inr(s.total_revenue_inr)}</div>
          </Card>

          <Card className="bg-white border border-slate-200/80 rounded-xl p-4 shadow-none">
            <div className="text-[11px] text-red-600 font-medium flex items-center gap-1">
              <TrendingDown className="h-3.5 w-3.5" /> Est. AI Cost
            </div>
            <div className="text-xl font-bold text-red-600 mt-1">{inr(s.total_cost_inr)}</div>
          </Card>

          <Card className="bg-emerald-50/50 border border-emerald-200/80 rounded-xl p-4 shadow-none">
            <div className="text-[11px] text-emerald-800 font-medium flex items-center gap-1">
              <DollarSign className="h-3.5 w-3.5" /> Gross Profit
            </div>
            <div className={`text-xl font-bold mt-1 ${s.gross_profit_inr >= 0 ? "text-emerald-700" : "text-red-600"}`}>
              {inr(s.gross_profit_inr)}
            </div>
          </Card>

          <Card className="bg-blue-50/50 border border-blue-200/80 rounded-xl p-4 shadow-none">
            <div className="text-[11px] text-blue-800 font-medium">Gross Margin</div>
            <div className={`text-xl font-bold mt-1 ${marginColor(s.gross_margin_pct)}`}>
              {pct(s.gross_margin_pct)}
            </div>
          </Card>
        </div>
      )}

      {/* Per-Customer Breakdown */}
      <div>
        <h3 className="text-sm font-semibold text-slate-900 mb-3">Customer Profitability Breakdown</h3>
        {loading ? (
          <div className="text-center py-12 text-slate-400 text-xs">
            <div className="w-5 h-5 border-2 border-[#fe6905] border-t-transparent rounded-full animate-spin mx-auto mb-2" />
            Loading profitability breakdown...
          </div>
        ) : !data || !data.customers || data.customers.length === 0 ? (
          <div className="text-center py-12 text-slate-400 border border-slate-200 rounded-xl bg-white text-xs">
            No call activity recorded for this period.
          </div>
        ) : (
          <div className="space-y-2">
            <div className="hidden sm:flex items-center gap-4 px-4 py-1 text-xs text-slate-400 font-semibold uppercase tracking-wider text-[10px]">
              <div className="flex-1">Customer</div>
              <div className="w-24 text-right">Revenue</div>
              <div className="w-24 text-right">Est. Cost</div>
              <div className="w-24 text-right">Profit</div>
              <div className="w-16 text-right">Margin</div>
              <div className="w-4" />
            </div>
            {data.customers.map((c) => (
              <CustomerRowCard key={c.customer_id} c={c} />
            ))}
          </div>
        )}
      </div>

      {/* Assumptions footnote */}
      {data && (
        <div className="text-[11px] text-slate-500 border border-slate-200 rounded-xl p-3.5 bg-slate-50/60 space-y-1 leading-relaxed">
          <div><strong className="text-slate-700">Cost model assumptions:</strong> Plivo ₹0.60/min · Deepgram STT $0.0048/min · Deepgram TTS $0.015/1k chars · ElevenLabs TTS $0.18/1k chars · GPT-4o-mini $0.15/$0.60 per 1M tokens · USD/INR ₹95.7 · TTS speaking ratio 47% · 900 chars/min of AI speech.</div>
          <div><strong className="text-slate-700">Growth plan:</strong> Smallest AI STT ~$0.003/min · Smallest AI TTS $0.0175/1k chars (Lightning v3.1).</div>
          <div className="text-amber-700 font-medium">⚠️ Estimates based on aggregate call duration and provider pricing. Verify against actual upstream API invoices monthly.</div>
        </div>
      )}

      {/* Global Settings Modal */}
      {isGlobalSettingsOpen && (
        <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-md overflow-hidden animate-in fade-in zoom-in-95 duration-200">
            <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
              <div>
                <h3 className="font-semibold text-slate-900">Global Profitability Settings</h3>
                <p className="text-xs text-slate-500 mt-0.5">Override base costs platform-wide</p>
              </div>
              <button onClick={() => setIsGlobalSettingsOpen(false)} className="text-slate-400 hover:text-slate-600">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-5 space-y-4">
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-slate-700">JSON Overrides</label>
                <textarea
                  className="w-full h-32 border border-slate-200 rounded-md p-2 text-xs font-mono text-slate-700"
                  value={globalSettingsInput}
                  onChange={(e) => setGlobalSettingsInput(e.target.value)}
                  placeholder={'{\n  "telephony_cost_per_min_inr": 0\n}'}
                />
              </div>
            </div>
            <div className="px-5 py-4 bg-slate-50 border-t border-slate-100 flex justify-end gap-2">
              <Button variant="outline" onClick={() => setIsGlobalSettingsOpen(false)} className="h-8 text-xs">
                Cancel
              </Button>
              <Button 
                onClick={async () => {
                  try {
                    const parsed = JSON.parse(globalSettingsInput);
                    setGlobalSettingsLoading(true);
                    const res = await adminFetch('/admin/platform-settings', {
                      method: "PUT",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ settings: { profitability_overrides: parsed } })
                    });
                    setGlobalSettingsLoading(false);
                    if (res.ok) {
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
                className="h-8 text-xs bg-slate-900 text-white"
              >
                {globalSettingsLoading ? "Saving..." : "Save Settings"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
