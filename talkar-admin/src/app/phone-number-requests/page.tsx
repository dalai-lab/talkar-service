"use client";
import { adminFetch } from "@/lib/api";

import React, { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";

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
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Phone Number Requests</h2>
          <p className="text-muted-foreground mt-2">
            Manage customer requests for additional phone numbers.
          </p>
        </div>
      </div>

      <div className="flex space-x-2">
        <Button variant={filter === 'pending' ? 'default' : 'outline'} onClick={() => setFilter('pending')}>Pending</Button>
        <Button variant={filter === 'approved' ? 'default' : 'outline'} onClick={() => setFilter('approved')}>Approved</Button>
        <Button variant={filter === 'denied' ? 'default' : 'outline'} onClick={() => setFilter('denied')}>Denied</Button>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Customer ID</TableHead>
                <TableHead>Quantity</TableHead>
                <TableHead>Region</TableHead>
                <TableHead>Use Case</TableHead>
                <TableHead>Requested At</TableHead>
                {filter === 'pending' && <TableHead className="text-right">Actions</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-8">Loading requests...</TableCell>
                </TableRow>
              ) : filteredRequests.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-8 text-muted-foreground">No {filter} requests found.</TableCell>
                </TableRow>
              ) : (
                filteredRequests.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium">#{r.customer_id}</TableCell>
                    <TableCell>{r.quantity}</TableCell>
                    <TableCell>{r.region}</TableCell>
                    <TableCell className="max-w-xs truncate">{r.use_case}</TableCell>
                    <TableCell>{new Date(r.requested_at).toLocaleDateString()}</TableCell>
                    {filter === 'pending' && (
                      <TableCell className="text-right space-x-2">
                        <Button size="sm" onClick={() => { setSelectedReq(r); setIsApproveOpen(true); }}>Approve</Button>
                        <Button variant="outline" size="sm" onClick={() => { setSelectedReq(r); setIsDenyOpen(true); }}>Deny</Button>
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
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Approve Request</DialogTitle>
          </DialogHeader>
          <div className="py-4 space-y-4">
            <p className="text-sm text-muted-foreground">
              Customer requested <strong>{selectedReq?.quantity}</strong> numbers in <strong>{selectedReq?.region}</strong>.
            </p>
            <div className="space-y-2">
              <Label>Assigned Numbers (comma-separated)</Label>
              <Input 
                placeholder="+919876543210, +919876543211" 
                value={approveNumbers} 
                onChange={e => setApproveNumbers(e.target.value)} 
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsApproveOpen(false)}>Cancel</Button>
            <Button onClick={handleApprove} disabled={!approveNumbers}>Confirm Approval</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Deny Modal */}
      <Dialog open={isDenyOpen} onOpenChange={setIsDenyOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Deny Request</DialogTitle>
          </DialogHeader>
          <div className="py-4 space-y-4">
            <div className="space-y-2">
              <Label>Reason / Note to Customer</Label>
              <textarea 
                className="w-full min-h-[80px] p-2 border rounded-md text-sm"
                placeholder="E.g. Cannot fulfill this region right now..."
                value={denyReason}
                onChange={e => setDenyReason(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsDenyOpen(false)}>Cancel</Button>
            <Button onClick={handleDeny} disabled={!denyReason}>Confirm Deny</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
