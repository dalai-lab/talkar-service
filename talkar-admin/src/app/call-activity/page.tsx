"use client";

import { adminFetch } from "@/lib/api";
import React, { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { PhoneCall, Activity, Radio } from "lucide-react";

export default function CallActivityPage() {
  const [activeCalls, setActiveCalls] = useState(0);

  useEffect(() => {
    const fetchActiveCalls = async () => {
      try {
        const res = await adminFetch(`/admin/calls/active`);
        if (res.ok) {
          const data = await res.json();
          setActiveCalls(data.active_calls || 0);
        }
      } catch (e) {
        console.error(e);
      }
    };
    
    fetchActiveCalls();
    const interval = setInterval(fetchActiveCalls, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex justify-between items-center pb-2 border-b border-slate-200/70">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 flex items-center gap-2.5">
            <div className="p-2 rounded-lg bg-indigo-50 border border-indigo-100 text-indigo-600">
              <Activity className="w-5 h-5" />
            </div>
            Live Call Activity
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Real-time telephony stream telemetry across all Talkar agent deployments.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
          <CardHeader className="flex flex-row items-center justify-between pb-2 p-5">
            <CardTitle className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Live Active Calls</CardTitle>
            <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-red-50 border border-red-200 text-red-700 text-[10px] font-semibold">
              <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
              LIVE
            </div>
          </CardHeader>
          <CardContent className="px-5 pb-5">
            <div className="text-4xl font-bold text-slate-900">{activeCalls}</div>
            <p className="text-xs text-slate-400 mt-1">Currently bridging across Dograh instances</p>
          </CardContent>
        </Card>
      </div>

      <div className="pt-2">
        <h3 className="text-sm font-semibold text-slate-900 mb-3">Recent Telephony Logs</h3>
        <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
          <CardContent className="p-12 text-center text-slate-400">
            <Activity className="h-8 w-8 mx-auto mb-3 opacity-30 text-slate-500" />
            <p className="text-xs font-medium text-slate-600">Historical telephony CDR logs are streamed in real time.</p>
            <p className="text-[11px] text-slate-400 mt-1">Live metrics poll every 5 seconds from the cluster gateway.</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
