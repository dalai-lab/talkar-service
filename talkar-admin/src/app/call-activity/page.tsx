"use client";
import { adminFetch } from "@/lib/api";

import React, { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { PhoneCall, Activity } from "lucide-react";


export default function CallActivityPage() {
  const [activeCalls, setActiveCalls] = useState(0);

  useEffect(() => {
    // Poll active calls every 5 seconds
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
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Live Call Activity</h2>
          <p className="text-muted-foreground mt-2">
            Real-time telephony metrics across all Talkar AI agents.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Card className="border-blue-200 shadow-sm relative overflow-hidden">
          <div className="absolute top-0 right-0 p-4 animate-pulse">
            <div className="w-3 h-3 bg-red-500 rounded-full"></div>
          </div>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Live Active Calls</CardTitle>
            <PhoneCall className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-4xl font-bold text-blue-700">{activeCalls}</div>
            <p className="text-xs text-muted-foreground mt-1">Currently bridging across Dograh instances</p>
          </CardContent>
        </Card>
      </div>

      <div className="mt-8">
        <h3 className="text-xl font-bold mb-4">Recent Call Logs</h3>
        <Card>
          <CardContent className="p-8 text-center text-muted-foreground">
            <Activity className="h-8 w-8 mx-auto mb-4 opacity-20" />
            <p>Detailed historical call logs will be synced here in Phase 8 via cron jobs.</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
