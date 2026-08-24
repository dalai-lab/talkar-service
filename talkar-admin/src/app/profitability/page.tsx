"use client";
import { adminFetch } from "@/lib/api";
import React, { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import {
  TrendingUp,
  TrendingDown,
  DollarSign,
  Phone,
  Clock,
  ChevronDown,
  ChevronUp,
  RefreshCw,
} from "lucide-react";

// ── types ──────────────────────────────────────────────────────────────────────
interface CustomerRow {
  customer_id: number;
  company_name: string;
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

// ── helpers ────────────────────────────────────────────────────────────────────
const inr = (v: number) =>
  `₹${v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const pct = (v: number) => `${v.toFixed(1)}%`;

const marginColor = (m: number) => {
  if (m >= 50) return "text-green-600";
  if (m >= 30) return "text-yellow-600";
  return "text-red-600";
};

const marginBg = (m: number) => {
  if (m >= 50) return "bg-green-100 text-green-800";
  if (m >= 30) return "bg-yellow-100 text-yellow-800";
  return "bg-red-100 text-red-800";
};

// ── CostBar ────────────────────────────────────────────────────────────────────
function CostBar({ breakdown, total }: { breakdown: CustomerRow["breakdown"]; total: number }) {
  if (total === 0) return null;
  const segments = [
    { label: "Plivo", value: breakdown.plivo_inr, color: "bg-blue-400" },
    { label: "STT", value: breakdown.stt_inr, color: "bg-purple-400" },
    { label: "TTS", value: breakdown.tts_inr, color: "bg-orange-400" },
    { label: "LLM", value: breakdown.llm_inr, color: "bg-emerald-400" },
  ];
  return (
    <div className="flex gap-1 items-center">
      <div className="flex h-2 flex-1 rounded overflow-hidden gap-0.5">
        {segments.map((s) => (
          <div
            key={s.label}
            className={`${s.color} transition-all`}
            style={{ width: `${(s.value / total) * 100}%` }}
            title={`${s.label}: ${inr(s.value)}`}
          />
        ))}
      </div>
      <div className="flex gap-2 text-[10px] text-muted-foreground">
        {segments.map((s) => (
          <span key={s.label} className="flex items-center gap-0.5">
            <span className={`inline-block w-2 h-2 rounded-sm ${s.color}`} />
            {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── CustomerRow ────────────────────────────────────────────────────────────────
function CustomerRowCard({ c }: { c: CustomerRow }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center gap-4 px-4 py-3 bg-white hover:bg-slate-50 transition-colors text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm truncate">{c.company_name}</div>
          <div className="text-xs text-muted-foreground mt-0.5">
            {c.calls} calls · {c.total_minutes.toFixed(1)} min
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-sm font-semibold text-green-700">{inr(c.revenue_inr)}</div>
          <div className="text-xs text-muted-foreground">billed</div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-sm font-semibold text-red-600">{inr(c.cost_inr)}</div>
          <div className="text-xs text-muted-foreground">cost</div>
        </div>
        <div className="text-right shrink-0">
          <div className={`text-sm font-bold ${c.profit_inr >= 0 ? "text-green-700" : "text-red-600"}`}>
            {inr(c.profit_inr)}
          </div>
          <div className="text-xs text-muted-foreground">profit</div>
        </div>
        <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${marginBg(c.margin_pct)}`}>
          {pct(c.margin_pct)}
        </span>
        {open ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
      </button>

      {open && (
        <div className="px-4 py-3 bg-slate-50 border-t space-y-3">
          <CostBar breakdown={c.breakdown} total={c.cost_inr} />
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { label: "📞 Plivo (telephony)", value: c.breakdown.plivo_inr },
              { label: "🎤 STT (Deepgram)", value: c.breakdown.stt_inr },
              { label: "🔊 TTS (voice)", value: c.breakdown.tts_inr },
              { label: "🤖 LLM (OpenAI)", value: c.breakdown.llm_inr },
            ].map((item) => (
              <div key={item.label} className="bg-white border rounded p-2 text-center">
                <div className="text-xs text-muted-foreground">{item.label}</div>
                <div className="font-semibold text-sm mt-1">{inr(item.value)}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────────
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
      {/* Header */}
      <div className="flex justify-between items-start flex-wrap gap-4">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Profitability Dashboard</h2>
          <p className="text-muted-foreground mt-1">
            Revenue vs. estimated AI + telephony costs · Estimated values based on real usage data
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border overflow-hidden">
            {PERIODS.map((p) => (
              <button
                key={p.value}
                onClick={() => setPeriod(p.value)}
                className={`px-3 py-1.5 text-sm transition-colors ${
                  period === p.value
                    ? "bg-slate-900 text-white"
                    : "bg-white text-slate-700 hover:bg-slate-50"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <button
            onClick={() => fetchData(period)}
            className="p-2 border rounded-lg hover:bg-slate-50 transition-colors"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {/* Summary Cards */}
      {s && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
          <Card className="border-slate-200">
            <CardHeader className="pb-1 pt-3 px-4">
              <CardTitle className="text-xs text-muted-foreground font-normal">Total Calls</CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              <div className="text-2xl font-bold flex items-center gap-1">
                <Phone className="h-4 w-4 text-slate-400" />
                {s.total_calls}
              </div>
            </CardContent>
          </Card>

          <Card className="border-slate-200">
            <CardHeader className="pb-1 pt-3 px-4">
              <CardTitle className="text-xs text-muted-foreground font-normal">Wallet Topups</CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              <div className="text-2xl font-bold text-blue-700">{inr(s.total_topups_inr)}</div>
            </CardContent>
          </Card>

          <Card className="border-green-100">
            <CardHeader className="pb-1 pt-3 px-4">
              <CardTitle className="text-xs text-muted-foreground font-normal flex items-center gap-1">
                <TrendingUp className="h-3 w-3 text-green-600" /> Revenue Billed
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              <div className="text-2xl font-bold text-green-700">{inr(s.total_revenue_inr)}</div>
            </CardContent>
          </Card>

          <Card className="border-red-100">
            <CardHeader className="pb-1 pt-3 px-4">
              <CardTitle className="text-xs text-muted-foreground font-normal flex items-center gap-1">
                <TrendingDown className="h-3 w-3 text-red-500" /> Est. AI Cost
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              <div className="text-2xl font-bold text-red-600">{inr(s.total_cost_inr)}</div>
            </CardContent>
          </Card>

          <Card className="border-emerald-200 bg-emerald-50">
            <CardHeader className="pb-1 pt-3 px-4">
              <CardTitle className="text-xs text-muted-foreground font-normal flex items-center gap-1">
                <DollarSign className="h-3 w-3 text-emerald-600" /> Gross Profit
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              <div className={`text-2xl font-bold ${s.gross_profit_inr >= 0 ? "text-emerald-700" : "text-red-600"}`}>
                {inr(s.gross_profit_inr)}
              </div>
            </CardContent>
          </Card>

          <Card className="border-blue-200 bg-blue-50">
            <CardHeader className="pb-1 pt-3 px-4">
              <CardTitle className="text-xs text-muted-foreground font-normal">Gross Margin</CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              <div className={`text-2xl font-bold ${marginColor(s.gross_margin_pct)}`}>
                {pct(s.gross_margin_pct)}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Per-Customer Breakdown */}
      <div>
        <h3 className="text-lg font-semibold mb-3">Per-Customer Breakdown</h3>
        {loading ? (
          <div className="text-center py-12 text-muted-foreground">Loading...</div>
        ) : !data || data.customers.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground border rounded-lg bg-slate-50">
            No call data for this period.
          </div>
        ) : (
          <div className="space-y-2">
            {/* Header */}
            <div className="hidden sm:flex items-center gap-4 px-4 py-1 text-xs text-muted-foreground font-medium">
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
        <div className="text-xs text-muted-foreground border rounded p-3 bg-slate-50">
          <strong>Cost model assumptions:</strong> Plivo ₹0.60/min · Deepgram STT $0.0048/min ·
          Deepgram TTS $0.015/1k chars · ElevenLabs $0.18/1k chars · GPT-4o-mini $0.15/$0.60 per 1M tokens ·
          USD/INR ₹95.7 · TTS speaking ratio 47% · 900 chars/min of AI speech.
          <br />
          <span className="text-amber-600">⚠ These are estimates. Actual provider bills may differ slightly.</span>
        </div>
      )}
    </div>
  );
}
