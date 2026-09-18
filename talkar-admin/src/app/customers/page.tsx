"use client";

import { adminFetch } from "@/lib/api";
import React, { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useRouter } from "next/navigation";
import { Users, Search, ChevronRight } from "lucide-react";

export default function CustomersPage() {
  const [customers, setCustomers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const router = useRouter();

  useEffect(() => {
    fetchCustomers();
  }, []);

  const fetchCustomers = async () => {
    try {
      const res = await adminFetch(`/admin/customers`);
      if (res.ok) {
        const data = await res.json();
        setCustomers(data);
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
            Customer Directory
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            View, inspect, and configure accounts for all Talkar customers.
          </p>
        </div>
      </div>

      {/* Search Filter */}
      <div className="relative max-w-sm">
        <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400 pointer-events-none" />
        <Input 
          placeholder="Search by company or email..." 
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-9 h-9 text-xs bg-white border-slate-200 text-slate-800 placeholder:text-slate-400"
        />
      </div>

      <Card className="bg-white border border-slate-200/80 rounded-xl overflow-hidden shadow-none">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-50/70 border-b border-slate-200/80 hover:bg-slate-50/70">
                <TableHead className="text-slate-600 font-semibold text-xs py-3">ID</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Company</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Contact</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Status</TableHead>
                <TableHead className="text-right text-slate-600 font-semibold text-xs py-3 pr-4">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-12 text-slate-400 text-xs">
                    <div className="w-5 h-5 border-2 border-[#fe6905] border-t-transparent rounded-full animate-spin mx-auto mb-2" />
                    Loading customers...
                  </TableCell>
                </TableRow>
              ) : customers.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-12 text-slate-400 text-xs">
                    No customers registered yet.
                  </TableCell>
                </TableRow>
              ) : (
                customers.filter(c => 
                  c.company_name?.toLowerCase().includes(search.toLowerCase()) || 
                  c.contact_email?.toLowerCase().includes(search.toLowerCase())
                ).map((c) => (
                  <TableRow key={c.id} className="border-b border-slate-100 hover:bg-slate-50/50 transition-colors">
                    <TableCell className="font-mono text-xs text-slate-400">#{c.id}</TableCell>
                    <TableCell className="text-xs font-semibold text-slate-900">{c.company_name}</TableCell>
                    <TableCell className="text-xs">
                      <div className="text-slate-800 font-medium">{c.contact_name}</div>
                      <span className="text-[11px] text-slate-500">{c.contact_email}</span>
                    </TableCell>
                    <TableCell className="text-xs">
                      <Badge className={c.status === 'active' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-slate-100 text-slate-700 border-slate-200'}>
                        {c.status.replace("_", " ")}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right pr-4">
                      <Button 
                        variant="outline" 
                        size="sm" 
                        onClick={() => router.push(`/customers/${c.id}`)}
                        className="h-7 px-2.5 text-xs font-medium border-slate-200 bg-white hover:bg-slate-50 text-slate-700 shadow-none cursor-pointer"
                      >
                        Manage <ChevronRight className="w-3.5 h-3.5 ml-1 text-slate-400" />
                      </Button>
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
