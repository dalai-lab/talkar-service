"use client";
import { adminFetch } from "@/lib/api";

import React, { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
  const [isRequestInfoOpen, setIsRequestInfoOpen] = useState(false);
  const [requestInfoMessage, setRequestInfoMessage] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [integrationFee, setIntegrationFee] = useState("0");
  const [integrationDescription, setIntegrationDescription] = useState("");

  useEffect(() => {
    fetchApplications();
  }, []);

  const fetchApplications = async () => {
    try {
      const res = await adminFetch(`/admin/applications`);
      if (res.ok) {
        const data = await res.json();
        setApps(Array.isArray(data) ? data : (data.detail ? [] : Object.values(data).flat() || []));
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleApprove = async () => {
    if (!selectedApp) return;
    try {
      const feePaise = parseInt(integrationFee) * 100;
      const res = await adminFetch(`/admin/applications/${selectedApp.id}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ 
          integration_fee_paise: feePaise || 0,
          integration_description: feePaise > 0 ? integrationDescription : ""
        })
      });
      if (res.ok) {
        setIsApproveOpen(false);
        setIntegrationFee("0");
        setIntegrationDescription("");
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
                        onClick={() => { setSelectedApp(app); setIsRequestInfoOpen(true); }}
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
              Approving <strong>{selectedApp?.company_name}</strong> will generate a setup fee Razorpay link and notify the customer.
            </p>
            <div className="space-y-2">
              <label className="text-sm font-medium">Custom Integration Fee (₹)</label>
              <Input 
                type="number" 
                value={integrationFee} 
                onChange={(e: any) => setIntegrationFee(e.target.value)} 
                placeholder="0 for no fee"
              />
              <p className="text-xs text-muted-foreground">₹0 = agent build is always free. Customer selects their own tier after wallet deposit.</p>
            </div>
            {parseInt(integrationFee) > 0 && (
              <div className="space-y-2">
                <label className="text-sm font-medium">Integration Description</label>
                <textarea 
                  className="w-full min-h-[80px] p-2 border rounded-md text-sm" 
                  value={integrationDescription} 
                  onChange={(e: any) => setIntegrationDescription(e.target.value)} 
                  placeholder="E.g., HubSpot CRM webhook integration + Custom reporting pipeline..."
                />
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsApproveOpen(false)}>Cancel</Button>
            <Button onClick={handleApprove}>Confirm Approval</Button>
          </DialogFooter>
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
      <Dialog open={isRequestInfoOpen} onOpenChange={setIsRequestInfoOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Request Information</DialogTitle>
          </DialogHeader>
          <div className="py-4 space-y-4">
            <p className="text-sm text-muted-foreground">
              What additional information do you need from <strong>{selectedApp?.company_name}</strong>?
            </p>
            <textarea 
              className="w-full min-h-[100px] p-3 border rounded-md" 
              placeholder="e.g. Please clarify your use case for outbound calls..."
              value={requestInfoMessage}
              onChange={(e) => setRequestInfoMessage(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsRequestInfoOpen(false)}>Cancel</Button>
            <Button 
              onClick={async () => {
                if (!selectedApp || !requestInfoMessage.trim()) return;
                try {
                  const res = await adminFetch(`/admin/applications/${selectedApp.id}/request-info`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ message: requestInfoMessage })
                  });
                  if (res.ok) {
                    setIsRequestInfoOpen(false);
                    setRequestInfoMessage("");
                    alert("Information requested!");
                  }
                } catch (e) {
                  console.error(e);
                }
              }} 
              disabled={!requestInfoMessage.trim()}
            >
              Send Request
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
