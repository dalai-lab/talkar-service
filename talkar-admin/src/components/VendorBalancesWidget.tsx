"use client";

import React, { useEffect, useState } from "react";
import { adminFetch } from "@/lib/api";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Phone,
  Mic,
  Volume2,
  Zap,
  RefreshCw,
  ExternalLink,
  ShieldAlert,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  HelpCircle,
} from "lucide-react";

interface VendorData {
  vendor: string;
  display_name: string;
  balance: number | null;
  units: string;
  quota_limit?: number | null;
  status: "healthy" | "warning" | "critical" | "payg_active" | "not_configured" | "error";
  auto_recharge?: boolean;
  overages_enabled?: boolean;
  next_reset_at?: string | null;
  error_message?: string | null;
  console_url: string;
  warning_threshold: number;
  critical_threshold: number;
  checked_at?: string;
}

interface VendorBalancesResponse {
  updated_at: string;
  overall_healthy: boolean;
  vendors: Record<string, VendorData>;
}

export function VendorBalancesWidget() {
  const [data, setData] = useState<VendorBalancesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchBalances = async () => {
    try {
      const res = await adminFetch("/admin/vendor-balances");
      if (res.ok) {
        const json = await res.json();
        setData(json);
        setError(null);
      } else {
        setError("Unable to load vendor balances");
      }
    } catch (e: any) {
      setError(e.message || "Failed to fetch balances");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchBalances();
  }, []);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const res = await adminFetch("/admin/vendor-balances/refresh", { method: "POST" });
      if (res.ok) {
        const json = await res.json();
        setData(json);
        setError(null);
      }
    } catch (e) {
      console.error("Sync failed", e);
    } finally {
      setSyncing(false);
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "healthy":
        return (
          <Badge className="bg-emerald-50 text-emerald-700 border-emerald-200 font-medium flex items-center gap-1">
            <CheckCircle2 className="w-3 h-3 text-emerald-600" /> Healthy
          </Badge>
        );
      case "payg_active":
        return (
          <Badge className="bg-blue-50 text-blue-700 border-blue-200 font-medium flex items-center gap-1">
            <CheckCircle2 className="w-3 h-3 text-blue-600" /> Pay-As-You-Go
          </Badge>
        );
      case "warning":
        return (
          <Badge className="bg-amber-50 text-amber-700 border-amber-200 font-medium flex items-center gap-1 animate-pulse">
            <AlertTriangle className="w-3 h-3 text-amber-600" /> Low Balance
          </Badge>
        );
      case "critical":
        return (
          <Badge className="bg-red-50 text-red-700 border-red-200 font-bold flex items-center gap-1 animate-pulse">
            <XCircle className="w-3 h-3 text-red-600" /> Critical / Empty
          </Badge>
        );
      case "not_configured":
        return (
          <Badge className="bg-slate-100 text-slate-500 border-slate-200 font-medium flex items-center gap-1">
            <HelpCircle className="w-3 h-3" /> Not Configured
          </Badge>
        );
      default:
        return (
          <Badge className="bg-slate-100 text-slate-600 border-slate-200 font-medium flex items-center gap-1">
            <AlertTriangle className="w-3 h-3 text-slate-400" /> Check Needed
          </Badge>
        );
    }
  };

  const vendors = data?.vendors || {};
  const plivo = vendors["plivo"];
  const deepgram = vendors["deepgram"];
  const elevenlabs = vendors["elevenlabs"];
  const smallest = vendors["smallest"];

  const formatRelativeTime = (isoString?: string) => {
    if (!isoString) return "Never";
    try {
      const diffSec = Math.floor((new Date().getTime() - new Date(isoString).getTime()) / 1000);
      if (diffSec < 60) return "Just now";
      if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
      return `${Math.floor(diffSec / 3600)}h ago`;
    } catch {
      return "Recently";
    }
  };

  return (
    <div className="space-y-4">
      {/* Header Bar */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 bg-white p-4 rounded-xl border border-slate-200/80 shadow-none">
        <div>
          <div className="flex items-center gap-2">
            <ShieldAlert className="h-4 w-4 text-[#fe6905]" />
            <h2 className="text-sm font-semibold text-slate-900">
              Upstream Infrastructure & Vendor Balances
            </h2>
            {data && (
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 font-medium">
                Last checked {formatRelativeTime(data.updated_at)}
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-1">
            Live credit pools for telecom and voice AI. Priority alerts automatically dispatch to{" "}
            <span className="font-mono text-slate-700 font-medium">it@4thorbit.com</span>.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={handleSync}
            disabled={syncing || loading}
            className="h-8 text-xs border-slate-200 hover:bg-slate-50 font-medium flex items-center gap-1.5"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${syncing ? "animate-spin text-[#fe6905]" : "text-slate-500"}`} />
            {syncing ? "Checking Live..." : "Sync Now"}
          </Button>
        </div>
      </div>

      {error && (
        <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-600 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Bento Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* 1. Plivo */}
        <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none flex flex-col justify-between">
          <CardHeader className="flex flex-row items-center justify-between pb-2 p-4">
            <div className="flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-blue-50 text-blue-600 border border-blue-100">
                <Phone className="h-4 w-4" />
              </div>
              <div>
                <CardTitle className="text-xs font-semibold text-slate-900">Plivo Telephony</CardTitle>
                <p className="text-[10px] text-slate-400">Carrier Trunking</p>
              </div>
            </div>
            {getStatusBadge(plivo?.status || "not_configured")}
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-1 space-y-3">
            <div>
              <div className="text-2xl font-bold font-mono text-slate-900 tracking-tight">
                {plivo?.balance !== null && plivo?.balance !== undefined
                  ? `${plivo.units === "INR" ? "₹" : "$"}${plivo.balance.toFixed(2)}`
                  : plivo?.status === "not_configured"
                  ? "Not Configured"
                  : "—"}
              </div>
              <div className="flex items-center justify-between text-[11px] text-slate-500 mt-1">
                <span>Auto-Recharge:</span>
                <span className={plivo?.auto_recharge ? "text-emerald-600 font-semibold" : "text-slate-500"}>
                  {plivo?.auto_recharge ? "Enabled (Backup Card)" : plivo?.status === "not_configured" ? "Needs Auth ID" : "Disabled"}
                </span>
              </div>
              <div className="text-[10px] text-slate-400 mt-0.5">
                Warning threshold: ${plivo?.warning_threshold?.toFixed(2) || "20.00"}
              </div>
              {plivo?.error_message && plivo?.status !== "healthy" && (
                <div className="text-[10px] text-slate-500 bg-slate-50 p-1.5 rounded border border-slate-100 mt-1.5 truncate" title={plivo.error_message}>
                  {plivo.error_message}
                </div>
              )}
            </div>

            <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
              <a
                href={plivo?.console_url || "https://console.plivo.com/billing/"}
                target="_blank"
                rel="noreferrer"
                className="text-[11px] text-[#fe6905] hover:underline font-semibold flex items-center gap-1"
              >
                Top Up Console <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          </CardContent>
        </Card>

        {/* 2. Deepgram */}
        <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none flex flex-col justify-between">
          <CardHeader className="flex flex-row items-center justify-between pb-2 p-4">
            <div className="flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-purple-50 text-purple-600 border border-purple-100">
                <Mic className="h-4 w-4" />
              </div>
              <div>
                <CardTitle className="text-xs font-semibold text-slate-900">Deepgram STT</CardTitle>
                <p className="text-[10px] text-slate-400">Live Transcription</p>
              </div>
            </div>
            {getStatusBadge(deepgram?.status || "not_configured")}
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-1 space-y-3">
            <div>
              <div className="text-2xl font-bold font-mono text-slate-900 tracking-tight">
                {deepgram?.status === "payg_active"
                  ? "Active (PAYG)"
                  : deepgram?.balance !== null && deepgram?.balance !== undefined
                  ? `$${deepgram.balance.toFixed(2)}`
                  : deepgram?.status === "not_configured"
                  ? "Not Configured"
                  : "—"}
              </div>
              <div className="text-[11px] text-slate-500 mt-1">
                {deepgram?.status === "payg_active" ? (
                  <span className="text-blue-600 font-semibold">Live STT Active • Card Billed</span>
                ) : (
                  <span>Prepaid Balance: ${deepgram?.balance?.toFixed(2) || "0.00"}</span>
                )}
              </div>
              <div className="text-[10px] text-slate-400 mt-0.5">
                Warning threshold: ${deepgram?.warning_threshold?.toFixed(2) || "20.00"}
              </div>
              {deepgram?.error_message && deepgram?.status !== "healthy" && (
                <div className="text-[10px] text-slate-500 bg-slate-50 p-1.5 rounded border border-slate-100 mt-1.5 truncate" title={deepgram.error_message}>
                  {deepgram.error_message}
                </div>
              )}
            </div>

            <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
              <a
                href={deepgram?.console_url || "https://console.deepgram.com/"}
                target="_blank"
                rel="noreferrer"
                className="text-[11px] text-[#fe6905] hover:underline font-semibold flex items-center gap-1"
              >
                Deepgram Console <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          </CardContent>
        </Card>

        {/* 3. ElevenLabs */}
        <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none flex flex-col justify-between">
          <CardHeader className="flex flex-row items-center justify-between pb-2 p-4">
            <div className="flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-orange-50 text-orange-600 border border-orange-100">
                <Volume2 className="h-4 w-4" />
              </div>
              <div>
                <CardTitle className="text-xs font-semibold text-slate-900">ElevenLabs TTS</CardTitle>
                <p className="text-[10px] text-slate-400">Premium Voice Synthesis</p>
              </div>
            </div>
            {getStatusBadge(elevenlabs?.status || "not_configured")}
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-1 space-y-3">
            <div>
              <div className="text-2xl font-bold font-mono text-slate-900 tracking-tight">
                {elevenlabs?.balance !== null && elevenlabs?.balance !== undefined
                  ? `${elevenlabs.balance.toLocaleString()} chars`
                  : elevenlabs?.status === "not_configured"
                  ? "Not Configured"
                  : "—"}
              </div>

              {elevenlabs?.quota_limit && (
                <div className="space-y-1 mt-2">
                  <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${
                        (elevenlabs.balance || 0) < 10000
                          ? "bg-red-500"
                          : (elevenlabs.balance || 0) < 50000
                          ? "bg-amber-500"
                          : "bg-emerald-500"
                      }`}
                      style={{
                        width: `${Math.min(
                          100,
                          Math.max(0, ((elevenlabs.balance || 0) / elevenlabs.quota_limit) * 100)
                        )}%`,
                      }}
                    />
                  </div>
                  <div className="flex justify-between text-[10px] text-slate-400">
                    <span>
                      {Math.round(((elevenlabs.balance || 0) / elevenlabs.quota_limit) * 100)}% left
                    </span>
                    <span>Limit: {elevenlabs.quota_limit.toLocaleString()}</span>
                  </div>
                </div>
              )}

              <div className="flex items-center justify-between text-[11px] text-slate-500 mt-2">
                <span>Overages:</span>
                <span className={elevenlabs?.overages_enabled ? "text-emerald-600 font-semibold" : "text-slate-500"}>
                  {elevenlabs?.overages_enabled ? "Enabled" : "Capped"}
                </span>
              </div>
            </div>

            <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
              <a
                href={elevenlabs?.console_url || "https://elevenlabs.io/app/subscription"}
                target="_blank"
                rel="noreferrer"
                className="text-[11px] text-[#fe6905] hover:underline font-semibold flex items-center gap-1"
              >
                ElevenLabs Portal <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          </CardContent>
        </Card>

        {/* 4. Smallest AI */}
        <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none flex flex-col justify-between">
          <CardHeader className="flex flex-row items-center justify-between pb-2 p-4">
            <div className="flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-amber-50 text-amber-600 border border-amber-100">
                <Zap className="h-4 w-4" />
              </div>
              <div>
                <CardTitle className="text-xs font-semibold text-slate-900">Smallest AI</CardTitle>
                <p className="text-[10px] text-slate-400">Waves & Lightning TTS</p>
              </div>
            </div>
            {getStatusBadge(smallest?.status || "not_configured")}
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-1 space-y-3">
            <div>
              <div className="text-2xl font-bold font-mono text-slate-900 tracking-tight">
                {smallest?.balance !== null && smallest?.balance !== undefined
                  ? `${Number(smallest.balance).toLocaleString(undefined, { maximumFractionDigits: 1 })} credits`
                  : smallest?.status === "payg_active"
                  ? "Active Subscription"
                  : smallest?.status === "not_configured"
                  ? "Not Configured"
                  : "—"}
              </div>

              {smallest?.quota_limit && (
                <div className="space-y-1 mt-2">
                  <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${
                        (smallest.balance || 0) < 50
                          ? "bg-red-500"
                          : (smallest.balance || 0) < 500
                          ? "bg-amber-500"
                          : "bg-emerald-500"
                      }`}
                      style={{
                        width: `${Math.min(
                          100,
                          Math.max(0, ((smallest.balance || 0) / smallest.quota_limit) * 100)
                        )}%`,
                      }}
                    />
                  </div>
                  <div className="flex justify-between text-[10px] text-slate-400">
                    <span>
                      {Math.round(((smallest.balance || 0) / smallest.quota_limit) * 100)}% left
                    </span>
                    <span>Pool: {Number(smallest.quota_limit).toLocaleString()}</span>
                  </div>
                </div>
              )}

              <div className="text-[11px] text-slate-500 mt-1">
                Ultralow-latency voice credit pool
              </div>
              <div className="text-[10px] text-slate-400 mt-0.5">
                Warning threshold: {smallest?.warning_threshold || 500} credits
              </div>
              {smallest?.error_message && smallest?.status !== "healthy" && (
                <div className="text-[10px] text-slate-500 bg-slate-50 p-1.5 rounded border border-slate-100 mt-1.5 truncate" title={smallest.error_message}>
                  {smallest.error_message}
                </div>
              )}
            </div>

            <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
              <a
                href={smallest?.console_url || "https://smallest.ai/dashboard"}
                target="_blank"
                rel="noreferrer"
                className="text-[11px] text-[#fe6905] hover:underline font-semibold flex items-center gap-1"
              >
                Smallest Dashboard <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
