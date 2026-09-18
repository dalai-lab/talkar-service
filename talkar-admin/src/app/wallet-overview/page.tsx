"use client";

import { adminFetch } from "@/lib/api";
import React, { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Wallet, AlertTriangle } from "lucide-react";

export default function WalletOverviewPage() {
  const [totalBalance, setTotalBalance] = useState<number>(0);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchWalletData();
  }, []);

  const fetchWalletData = async () => {
    try {
      const [overviewRes, alertsRes] = await Promise.all([
        adminFetch(`/admin/wallet/overview`),
        adminFetch(`/admin/wallet/alerts`)
      ]);
      
      if (overviewRes.ok) {
        const data = await overviewRes.json();
        setTotalBalance(data.total_platform_balance_paise || 0);
      }
      
      if (alertsRes.ok) {
        const data = await alertsRes.json();
        const combinedAlerts = Array.isArray(data) ? data : [...(data.zero_balance || []), ...(data.low_balance || [])];
        setAlerts(combinedAlerts);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex justify-between items-center pb-2 border-b border-slate-200/70">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 flex items-center gap-2.5">
            Platform Wallet Overview
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Monitor total pre-paid float and low-balance warnings across all customer wallets.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
          <CardHeader className="flex flex-row items-center justify-between pb-2 p-5">
            <CardTitle className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Total Platform Float</CardTitle>
            <div className="p-2 rounded-lg bg-emerald-50 text-emerald-600 border border-emerald-100">
              <Wallet className="h-4 w-4" />
            </div>
          </CardHeader>
          <CardContent className="px-5 pb-5">
            <div className="text-3xl font-bold text-slate-900">
              ₹{(totalBalance / 100).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
            </div>
            <p className="text-xs text-slate-400 mt-1">Pre-paid balance across all active accounts</p>
          </CardContent>
        </Card>
      </div>

      <div className="pt-2">
        <h3 className="text-sm font-semibold text-slate-900 mb-3 flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-amber-500" />
          Low Balance Warnings ({"< ₹100"})
        </h3>

        <Card className="bg-white border border-slate-200/80 rounded-xl overflow-hidden shadow-none">
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow className="bg-slate-50/70 border-b border-slate-200/80 hover:bg-slate-50/70">
                  <TableHead className="text-slate-600 font-semibold text-xs py-3">Customer ID</TableHead>
                  <TableHead className="text-slate-600 font-semibold text-xs py-3">Current Balance</TableHead>
                  <TableHead className="text-slate-600 font-semibold text-xs py-3">Auto-Recharge</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <TableRow>
                    <TableCell colSpan={3} className="text-center py-12 text-slate-400 text-xs">
                      <div className="w-5 h-5 border-2 border-[#fe6905] border-t-transparent rounded-full animate-spin mx-auto mb-2" />
                      Loading wallet alerts...
                    </TableCell>
                  </TableRow>
                ) : alerts.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={3} className="text-center py-12 text-slate-400 text-xs">
                      No low balance warnings. All active customer accounts are sufficiently funded.
                    </TableCell>
                  </TableRow>
                ) : (
                  alerts.map((w) => (
                    <TableRow key={w.id} className="border-b border-slate-100 hover:bg-slate-50/50 transition-colors">
                      <TableCell className="font-mono text-xs font-semibold text-slate-900">
                        {w.company_name || w.contact_email || `#${w.customer_id}`}
                      </TableCell>
                      <TableCell className="text-xs text-red-600 font-bold font-mono">
                        ₹{(w.balance_paise / 100).toFixed(2)}
                      </TableCell>
                      <TableCell className="text-xs">
                        {w.auto_recharge_enabled ? (
                          <Badge className="bg-emerald-50 text-emerald-700 border-emerald-200 font-medium">Enabled</Badge>
                        ) : (
                          <Badge className="bg-slate-100 text-slate-600 border-slate-200 font-medium">Disabled</Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
