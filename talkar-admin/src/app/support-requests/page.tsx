"use client";
import { adminFetch } from "@/lib/api";

import React, { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";

export default function SupportRequestsPage() {
  const [requests, setRequests] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedReq, setSelectedReq] = useState<any | null>(null);
  const [isUpdateOpen, setIsUpdateOpen] = useState(false);
  const [status, setStatus] = useState("");
  const [adminNote, setAdminNote] = useState("");

  useEffect(() => {
    fetchRequests();
  }, []);

  const fetchRequests = async () => {
    try {
      const res = await adminFetch(`/admin/support-requests`);
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

  const handleUpdate = async () => {
    if (!selectedReq) return;
    try {
      const res = await adminFetch(`/admin/support-requests/${selectedReq.id}/resolve`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, admin_note: adminNote })
      });
      if (res.ok) {
        setIsUpdateOpen(false);
        fetchRequests();
      }
    } catch (e) {
      console.error(e);
    }
  };

  const openUpdate = (req: any) => {
    setSelectedReq(req);
    setStatus(req.status);
    setAdminNote(req.admin_note || "");
    setIsUpdateOpen(true);
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Support Requests</h2>
          <p className="text-muted-foreground mt-2">Manage customer support tickets.</p>
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Customer</TableHead>
                <TableHead>Subject</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8">Loading requests...</TableCell>
                </TableRow>
              ) : requests.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">No support requests.</TableCell>
                </TableRow>
              ) : (
                requests.map((req) => (
                  <TableRow key={req.id}>
                    <TableCell>{new Date(req.created_at).toLocaleDateString()}</TableCell>
                    <TableCell className="font-medium">
                      {req.customer.company_name}
                      <br />
                      <span className="text-xs text-muted-foreground">{req.customer.contact_email}</span>
                    </TableCell>
                    <TableCell>
                      <p className="font-semibold">{req.subject}</p>
                      <p className="text-xs text-muted-foreground">Type: {req.type}</p>
                    </TableCell>
                    <TableCell>
                      <Badge variant={req.status === 'resolved' ? 'default' : req.status === 'open' ? 'destructive' : 'secondary'}>
                        {req.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" onClick={() => openUpdate(req)}>Review</Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={isUpdateOpen} onOpenChange={setIsUpdateOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>Update Request: {selectedReq?.subject}</DialogTitle>
          </DialogHeader>
          <div className="py-4 space-y-4">
            <div className="bg-muted p-3 rounded-md text-sm">
              <p className="font-semibold">Description:</p>
              <p className="whitespace-pre-wrap">{selectedReq?.description}</p>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Status</label>
              <select 
                className="w-full p-2 border rounded-md" 
                value={status} 
                onChange={e => setStatus(e.target.value)}
              >
                <option value="open">Open</option>
                <option value="in_progress">In Progress</option>
                <option value="resolved">Resolved</option>
                <option value="closed">Closed</option>
              </select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Admin Note (visible to customer)</label>
              <textarea 
                className="w-full p-2 border rounded-md min-h-[100px]" 
                value={adminNote} 
                onChange={e => setAdminNote(e.target.value)}
                placeholder="We have added your new agent..."
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsUpdateOpen(false)}>Cancel</Button>
            <Button onClick={handleUpdate}>Save Changes</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
