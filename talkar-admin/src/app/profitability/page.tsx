"use client";
import { adminFetch } from "@/lib/api";

import React, { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { TrendingUp, TrendingDown, DollarSign } from "lucide-react";


export default function ProfitabilityPage() {
  const [metrics, setMetrics] = useState({ revenue: 0, cost: 0, margin: 0 });

  useEffect(() => {
    fetchMetrics();
  }, []);

  const fetchMetrics = async () => {
    try {
      const res = await adminFetch(`/admin/profitability`);
      if (res.ok) {
        const data = await res.json();
        setMetrics(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Profitability Dashboard</h2>
          <p className="text-muted-foreground mt-2">
            Overview of platform revenue, Twilio/Plivo costs, and gross margins.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Card className="border-green-200">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Estimated Revenue (Mtd)</CardTitle>
            <TrendingUp className="h-4 w-4 text-green-600" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-green-700">₹{metrics.revenue.toFixed(2)}</div>
            <p className="text-xs text-muted-foreground mt-1">From integration fees & per-minute billing</p>
          </CardContent>
        </Card>
        
        <Card className="border-red-200">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Platform Costs (Mtd)</CardTitle>
            <TrendingDown className="h-4 w-4 text-red-600" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-red-700">₹{metrics.cost.toFixed(2)}</div>
            <p className="text-xs text-muted-foreground mt-1">Twilio/Plivo/Dograh costs</p>
          </CardContent>
        </Card>

        <Card className="border-blue-200 bg-blue-50">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Gross Margin</CardTitle>
            <DollarSign className="h-4 w-4 text-blue-600" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-blue-700">{metrics.margin}%</div>
            <p className="text-xs text-muted-foreground mt-1">Overall platform profitability</p>
          </CardContent>
        </Card>
      </div>
      
      <div className="mt-8 p-8 border rounded-lg bg-slate-50 text-center text-muted-foreground text-sm">
        <p>Detailed per-customer breakdown will be available once cron jobs in Phase 8 begin populating real call data and reconciling Twilio API costs.</p>
      </div>
    </div>
  );
}
