"use client";
import { adminFetch } from "@/lib/api";

import React, { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { 
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter
} from "@/components/ui/dialog";


export default function ApplicationsPage() {
  const [apps, setApps] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedApp, setSelectedApp] = useState<any | null>(null);
  const [isApproveOpen, setIsApproveOpen] = useState(false);
  const [isRejectOpen, setIsRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");

  useEffect(() => {
    fetchApplications();
  }, []);

  const fetchApplications = async () => {
    try {
      const res = await adminFetch(`/admin/applications`);
      if (res.ok) {
        const data = await res.json();
        setApps(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleApprove = async (plan: string) => {
    if (!selectedApp) return;
    try {
      const res = await adminFetch(`/admin/applications/${selectedApp.id}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan })
      });
      if (res.ok) {
        setIsApproveOpen(false);
        fetchApplications();
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleReject = async () => {
    if (!selectedApp) return;
    try {
      const res = await adminFetch(`/admin/applications/${selectedApp.id}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: rejectReason })
      });
      if (res.ok) {
        setIsRejectOpen(false);
        setRejectReason("");
        fetchApplications();
      }
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Applications Queue</h2>
          <p className="text-muted-foreground mt-2">
            Review and approve new Talkar customer applications.
          </p>
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Company</TableHead>
                <TableHead>Contact</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8">Loading applications...</TableCell>
                </TableRow>
              ) : apps.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">No applications in queue.</TableCell>
                </TableRow>
              ) : (
                apps.map((app) => (
                  <TableRow key={app.id}>
                    <TableCell>{new Date(app.created_at).toLocaleDateString()}</TableCell>
                    <TableCell className="font-medium">{app.company_name}</TableCell>
                    <TableCell>
                      {app.contact_name}
                      <br />
                      <span className="text-xs text-muted-foreground">{app.contact_email}</span>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="bg-amber-100 text-amber-800 hover:bg-amber-100">
                        Under Review
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right space-x-2">
                      <Button 
                        variant="outline" 
                        size="sm"
                        onClick={async () => {
                          await adminFetch(`/admin/applications/${app.id}/request-info`, {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ message: "Please provide more details." })
                          });
                          alert("Information requested!");
                        }}
                      >
                        Request Info
                      </Button>
                      <Button 
                        variant="outline" 
                        size="sm"
                        onClick={() => { setSelectedApp(app); setIsRejectOpen(true); }}
                      >
                        Reject
                      </Button>
                      <Button 
                        size="sm"
                        onClick={() => { setSelectedApp(app); setIsApproveOpen(true); }}
                      >
                        Review & Approve
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={isApproveOpen} onOpenChange={setIsApproveOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Approve Application</DialogTitle>
          </DialogHeader>
          <div className="py-4 space-y-4">
            <p className="text-sm text-muted-foreground">
              Approving <strong>{selectedApp?.company_name}</strong> will generate a setup fee Razorpay link and notify the customer. Select their plan:
            </p>
            <div className="grid grid-cols-2 gap-4">
              <Button variant="outline" className="h-24 flex flex-col items-center justify-center space-y-2" onClick={() => handleApprove("starter")}>
                <span className="font-bold">Starter Plan</span>
                <span className="text-xs text-muted-foreground">₹10,000 Setup</span>
              </Button>
              <Button variant="outline" className="h-24 flex flex-col items-center justify-center space-y-2 border-blue-500 bg-blue-50" onClick={() => handleApprove("pro")}>
                <span className="font-bold text-blue-700">Pro Plan</span>
                <span className="text-xs text-blue-600/70">₹25,000 Setup</span>
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={isRejectOpen} onOpenChange={setIsRejectOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject Application</DialogTitle>
          </DialogHeader>
          <div className="py-4 space-y-4">
            <p className="text-sm text-muted-foreground">
              Please provide a reason for rejecting <strong>{selectedApp?.company_name}</strong>. This will be shown to the customer.
            </p>
            <textarea 
              className="w-full min-h-[100px] p-3 border rounded-md" 
              placeholder="e.g. Incomplete GST documents..."
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsRejectOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={handleReject} disabled={!rejectReason.trim()}>Confirm Rejection</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
