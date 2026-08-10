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
        setAlerts(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Platform Wallet Overview</h2>
          <p className="text-muted-foreground mt-2">
            Monitor total pre-paid funds floating in the Talkar platform.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Platform Float</CardTitle>
            <Wallet className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              ₹{(totalBalance / 100).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
            </div>
            <p className="text-xs text-muted-foreground">Across all customer wallets</p>
          </CardContent>
        </Card>
      </div>

      <h3 className="text-xl font-bold mt-8 flex items-center gap-2">
        <AlertTriangle className="h-5 w-5 text-amber-500" />
        Low Balance Alerts ({"< ₹100"})
      </h3>
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Customer ID</TableHead>
                <TableHead>Current Balance</TableHead>
                <TableHead>Auto-Recharge</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={3} className="text-center py-8">Loading alerts...</TableCell>
                </TableRow>
              ) : alerts.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={3} className="text-center py-8 text-muted-foreground">No low balance alerts.</TableCell>
                </TableRow>
              ) : (
                alerts.map((w) => (
                  <TableRow key={w.id}>
                    <TableCell className="font-mono font-medium">#{w.customer_id}</TableCell>
                    <TableCell className="text-red-600 font-bold">
                      ₹{(w.balance_paise / 100).toFixed(2)}
                    </TableCell>
                    <TableCell>
                      {w.auto_recharge_enabled ? (
                        <Badge variant="outline" className="text-green-600 border-green-600">Enabled</Badge>
                      ) : (
                        <Badge variant="secondary">Disabled</Badge>
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
  );
}
