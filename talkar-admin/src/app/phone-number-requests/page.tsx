"use client";

import { adminFetch } from "@/lib/api";
import React, { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Phone, CheckCircle2, XCircle } from "lucide-react";

export default function PhoneNumberRequestsPage() {
  const [requests, setRequests] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("pending");

  // Approve Modal State
  const [isApproveOpen, setIsApproveOpen] = useState(false);
  const [selectedReq, setSelectedReq] = useState<any>(null);
  const [approveNumbers, setApproveNumbers] = useState("");

  // Deny Modal State
  const [isDenyOpen, setIsDenyOpen] = useState(false);
  const [denyReason, setDenyReason] = useState("");

  useEffect(() => {
    fetchRequests();
  }, []);

  const fetchRequests = async () => {
    try {
      const res = await adminFetch(`/admin/phone-number-requests`);
      if (res.ok) {
        const data = await res.json();
        setRequests(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleApprove = async () => {
    if (!selectedReq || !approveNumbers) return;
    try {
      const numbersArray = approveNumbers.split(",").map(n => n.trim()).filter(n => n);
      const res = await adminFetch(`/admin/phone-number-requests/${selectedReq.id}/approve`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ numbers: numbersArray })
      });
      if (res.ok) {
        setIsApproveOpen(false);
        setApproveNumbers("");
        fetchRequests();
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleDeny = async () => {
    if (!selectedReq || !denyReason) return;
    try {
      const res = await adminFetch(`/admin/phone-number-requests/${selectedReq.id}/deny`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ admin_note: denyReason })
      });
      if (res.ok) {
        setIsDenyOpen(false);
        setDenyReason("");
        fetchRequests();
      }
    } catch (e) {
      console.error(e);
    }
  };

  const filteredRequests = requests.filter(r => r.status === filter);

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex justify-between items-center pb-2 border-b border-slate-200/70">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 flex items-center gap-2.5">
            <div className="p-2 rounded-lg bg-indigo-50 border border-indigo-100 text-indigo-600">
              <Phone className="w-5 h-5" />
            </div>
            Phone Number Requests
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Manage customer requests for additional phone numbers and telephony channels.
          </p>
        </div>
      </div>

      {/* Segmented Filter Control */}
      <div className="flex items-center bg-slate-100 p-0.5 rounded-lg text-xs border border-slate-200/60 w-fit">
        <button
          onClick={() => setFilter('pending')}
          className={`px-3 py-1 rounded-md transition-all font-medium cursor-pointer ${
            filter === 'pending'
              ? 'bg-white text-slate-900 shadow-sm font-semibold'
              : 'text-slate-600 hover:text-slate-900'
          }`}
        >
          Pending
        </button>
        <button
          onClick={() => setFilter('approved')}
          className={`px-3 py-1 rounded-md transition-all font-medium cursor-pointer ${
            filter === 'approved'
              ? 'bg-white text-emerald-700 shadow-sm font-semibold'
              : 'text-slate-600 hover:text-slate-900'
          }`}
        >
          Approved
        </button>
        <button
          onClick={() => setFilter('denied')}
          className={`px-3 py-1 rounded-md transition-all font-medium cursor-pointer ${
            filter === 'denied'
              ? 'bg-white text-red-700 shadow-sm font-semibold'
              : 'text-slate-600 hover:text-slate-900'
          }`}
        >
          Denied
        </button>
      </div>

      <Card className="bg-white border border-slate-200/80 rounded-xl overflow-hidden shadow-none">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-50/70 border-b border-slate-200/80 hover:bg-slate-50/70">
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Customer ID</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Quantity</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Region</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Use Case</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Requested At</TableHead>
                {filter === 'pending' && <TableHead className="text-right text-slate-600 font-semibold text-xs py-3 pr-4">Actions</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-12 text-slate-400 text-xs">
                    <div className="w-5 h-5 border-2 border-indigo-600 border-t-transparent rounded-full animate-spin mx-auto mb-2" />
                    Loading requests...
                  </TableCell>
                </TableRow>
              ) : filteredRequests.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-12 text-slate-400 text-xs">
                    No {filter} phone number requests found.
                  </TableCell>
                </TableRow>
              ) : (
                filteredRequests.map((r) => (
                  <TableRow key={r.id} className="border-b border-slate-100 hover:bg-slate-50/50 transition-colors">
                    <TableCell className="font-mono text-xs text-slate-800 font-medium">#{r.customer_id}</TableCell>
                    <TableCell className="text-xs font-semibold text-slate-900">{r.quantity}</TableCell>
                    <TableCell className="text-xs text-slate-700">{r.region}</TableCell>
                    <TableCell className="text-xs max-w-xs truncate text-slate-600">{r.use_case}</TableCell>
                    <TableCell className="text-xs text-slate-500 whitespace-nowrap">{new Date(r.requested_at).toLocaleDateString()}</TableCell>
                    {filter === 'pending' && (
                      <TableCell className="text-right space-x-1.5 whitespace-nowrap pr-4">
                        <Button 
                          size="sm" 
                          onClick={() => { setSelectedReq(r); setIsApproveOpen(true); }}
                          className="h-7 px-2.5 text-xs font-medium bg-emerald-600 hover:bg-emerald-700 text-white shadow-none cursor-pointer"
                        >
                          Approve
                        </Button>
                        <Button 
                          variant="outline" 
                          size="sm" 
                          onClick={() => { setSelectedReq(r); setIsDenyOpen(true); }}
                          className="h-7 px-2.5 text-xs font-medium border-slate-200 bg-white hover:bg-red-50 text-red-600 shadow-none cursor-pointer"
                        >
                          Deny
                        </Button>
                      </TableCell>
                    )}
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Approve Modal */}
      <Dialog open={isApproveOpen} onOpenChange={setIsApproveOpen}>
        <DialogContent className="bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold border-b pb-3">Approve Phone Number Request</DialogTitle>
          </DialogHeader>
          <div className="py-3 space-y-3 text-xs">
            <p className="text-slate-600">
              Customer requested <strong>{selectedReq?.quantity}</strong> numbers in <strong>{selectedReq?.region}</strong>.
            </p>
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Assigned Numbers (comma-separated, E.164 format)</Label>
              <Input 
                placeholder="+919876543210, +919876543211" 
                value={approveNumbers} 
                onChange={e => setApproveNumbers(e.target.value)} 
                className="h-9 text-xs bg-white border-slate-200"
              />
            </div>
          </div>
          <DialogFooter className="border-t pt-3">
            <Button variant="outline" size="sm" onClick={() => setIsApproveOpen(false)} className="text-xs border-slate-200">Cancel</Button>
            <Button size="sm" onClick={handleApprove} disabled={!approveNumbers} className="text-xs bg-emerald-600 hover:bg-emerald-700 text-white">Confirm Approval</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Deny Modal */}
      <Dialog open={isDenyOpen} onOpenChange={setIsDenyOpen}>
        <DialogContent className="bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold border-b pb-3 text-red-600">Deny Request</DialogTitle>
          </DialogHeader>
          <div className="py-3 space-y-3 text-xs">
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Reason / Note to Customer</Label>
              <textarea 
                className="w-full min-h-[90px] p-2.5 border border-slate-200 rounded-lg text-xs bg-white focus:outline-none focus:border-red-500"
                placeholder="E.g. Inventory unavailable for this specific regional prefix..."
                value={denyReason}
                onChange={e => setDenyReason(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter className="border-t pt-3">
            <Button variant="outline" size="sm" onClick={() => setIsDenyOpen(false)} className="text-xs border-slate-200">Cancel</Button>
            <Button size="sm" variant="destructive" onClick={handleDeny} disabled={!denyReason} className="text-xs">Confirm Deny</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
