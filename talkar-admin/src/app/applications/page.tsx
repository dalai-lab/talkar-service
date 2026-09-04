"use client";
import { adminFetch } from "@/lib/api";

import React, { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { 
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogTrigger
} from "@/components/ui/dialog";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";

const DocumentViewer = ({ title, dataUrl }: { title: string, dataUrl: string }) => {
  if (!dataUrl) return null;
  const isPdf = dataUrl.startsWith("data:application/pdf");
  const isImage = dataUrl.startsWith("data:image/");
  
  const getExtension = () => {
    if (isPdf) return "pdf";
    if (isImage) {
      const mime = dataUrl.split(";")[0].split(":")[1];
      return mime.split("/")[1] || "png";
    }
    return "bin";
  };

  return (
    <div className="flex items-center gap-3 bg-muted/40 p-2 px-3 rounded-md border text-sm">
      <Dialog>
        <DialogTrigger render={<Button variant="link" className="p-0 h-auto text-sm text-blue-600 font-medium" />}>
          📄 {title} (View)
        </DialogTrigger>
        <DialogContent className="max-w-4xl w-full h-[80vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-auto bg-zinc-100/50 dark:bg-zinc-900/50 rounded-md border flex items-center justify-center p-4">
            {isPdf ? (
              <iframe src={dataUrl} className="w-full h-full border-0 rounded-md bg-white" title={title} />
            ) : isImage ? (
              <img src={dataUrl} alt={title} className="max-w-full max-h-full object-contain rounded-md shadow-sm" />
            ) : (
              <p className="text-muted-foreground text-sm">Preview not available for this file type.</p>
            )}
          </div>
        </DialogContent>
      </Dialog>
      <span className="text-muted-foreground text-xs">•</span>
      <a href={dataUrl} download={`${title.replace(/\s+/g, '_').toLowerCase()}.${getExtension()}`} className="text-xs text-zinc-500 hover:text-zinc-800 underline underline-offset-2">
        Download
      </a>
    </div>
  );
};

export default function ApplicationsPage() {
  const [apps, setApps] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedApp, setSelectedApp] = useState<any | null>(null);
  const [isDetailOpen, setIsDetailOpen] = useState(false);
  const [showRawJsonApp, setShowRawJsonApp] = useState(false);
  const [isApproveOpen, setIsApproveOpen] = useState(false);
  const [isRejectOpen, setIsRejectOpen] = useState(false);
  const [isRequestInfoOpen, setIsRequestInfoOpen] = useState(false);
  const [requestInfoMessage, setRequestInfoMessage] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [integrationFee, setIntegrationFee] = useState("0");
  const [integrationDescription, setIntegrationDescription] = useState("");
  const [approvedTier, setApprovedTier] = useState("starter");

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
          integration_description: feePaise > 0 ? integrationDescription : "",
          approved_tier: approvedTier
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
                    <TableCell className="font-medium">
                      {app.company_name}
                      {app.onboarding_form?.needsApiIntegration && (
                        <div className="mt-1"><Badge variant="outline" className="bg-blue-50 text-blue-700 border-blue-200">Custom API Req</Badge></div>
                      )}
                    </TableCell>
                    <TableCell>
                      {app.contact_name}
                      <br />
                      <span className="text-xs text-muted-foreground">{app.contact_email}</span>
                    </TableCell>
                    <TableCell>
                      {app.status === "pending_approval" ? (
                        <Badge variant="secondary" className="bg-purple-100 text-purple-800 hover:bg-purple-100">
                          New Agent Brief
                        </Badge>
                      ) : (
                        <Badge variant="secondary" className="bg-amber-100 text-amber-800 hover:bg-amber-100">
                          Under Review
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right space-x-2">
                      <Button 
                        variant="secondary" 
                        size="sm"
                        onClick={() => { setSelectedApp(app); setIsDetailOpen(true); }}
                      >
                        View Details
                      </Button>
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
              {selectedApp?.onboarding_form?.needsApiIntegration 
                ? `Approving ${selectedApp?.company_name} will generate a custom integration fee Razorpay link and notify the customer.`
                : `Approving ${selectedApp?.company_name} will mark their agent as ready for building. No integration fee is required by default.`}
            </p>
            
            {selectedApp?.onboarding_form?.needsApiIntegration && (
              <div className="bg-blue-50/50 border border-blue-100 p-3 rounded-md text-sm mb-4">
                <span className="font-semibold text-blue-900 block mb-1">Customer's Integration Request:</span>
                <span className="text-blue-800">{selectedApp.onboarding_form.apiIntegrationDetails}</span>
              </div>
            )}
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
            <div className="space-y-2">
              <label className="text-sm font-medium">Approved Tier</label>
              <Select value={approvedTier} onValueChange={(v) => v && setApprovedTier(v)}>
                <SelectTrigger><SelectValue placeholder="Select tier" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="starter">Starter</SelectItem>
                  <SelectItem value="pro">Pro</SelectItem>
                  <SelectItem value="elite">Elite</SelectItem>
                </SelectContent>
              </Select>
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

      {/* View Full Application Submission Modal */}
      <Dialog open={isDetailOpen} onOpenChange={setIsDetailOpen}>
        <DialogContent className="max-w-3xl w-full max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Application Submission Details</DialogTitle>
          </DialogHeader>
          {selectedApp && (
            <div className="space-y-6 py-2 text-sm">
              {/* Contact Info */}
              <div className="border rounded-md p-4 space-y-3 bg-muted/20">
                <h4 className="font-semibold text-base border-b pb-2">Contact Information</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <Label className="text-xs text-muted-foreground">Primary Contact</Label>
                    <p className="font-medium">{selectedApp.contact_name || selectedApp.onboarding_form?.pocName || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Designation / Role</Label>
                    <p>{selectedApp.onboarding_form?.pocDesignation || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Email Address</Label>
                    <p>{selectedApp.contact_email || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Phone Number</Label>
                    <p>{selectedApp.contact_phone || selectedApp.onboarding_form?.pocPhone || "N/A"}</p>
                  </div>
                </div>
              </div>

              {/* Business Profile */}
              <div className="border rounded-md p-4 space-y-3 bg-muted/20">
                <h4 className="font-semibold text-base border-b pb-2">Business & Company Profile</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <Label className="text-xs text-muted-foreground">Company Name</Label>
                    <p className="font-medium">{selectedApp.company_name || selectedApp.onboarding_form?.businessName || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Industry</Label>
                    <p>{selectedApp.industry || selectedApp.onboarding_form?.industry || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">GST Number</Label>
                    <p className="font-mono">{selectedApp.onboarding_form?.gstNumber || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Company Size</Label>
                    <p>{selectedApp.onboarding_form?.companySize || "N/A"}</p>
                  </div>
                  <div className="md:col-span-2">
                    <Label className="text-xs text-muted-foreground">Website</Label>
                    {selectedApp.onboarding_form?.websiteUrl ? (
                      <p>
                        <a 
                          href={selectedApp.onboarding_form.websiteUrl.startsWith("http") ? selectedApp.onboarding_form.websiteUrl : `https://${selectedApp.onboarding_form.websiteUrl}`} 
                          target="_blank" 
                          rel="noreferrer"
                          className="text-blue-600 hover:underline"
                        >
                          {selectedApp.onboarding_form.websiteUrl}
                        </a>
                      </p>
                    ) : (
                      <p className="text-muted-foreground">N/A</p>
                    )}
                  </div>
                </div>
              </div>

              {/* Voice Agent & Use Case */}
              <div className="border rounded-md p-4 space-y-3 bg-muted/20">
                <h4 className="font-semibold text-base border-b pb-2">Agent & Use Case Requirements</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <Label className="text-xs text-muted-foreground">Call Direction / Type</Label>
                    <p className="font-medium capitalize">{selectedApp.onboarding_form?.useCaseType || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Expected Call Volume</Label>
                    <p>{selectedApp.onboarding_form?.callVolume || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Target Languages</Label>
                    <p>{selectedApp.onboarding_form?.languages || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Integrations</Label>
                    <p>{selectedApp.onboarding_form?.integrations || "N/A"}</p>
                  </div>
                  <div className="md:col-span-2">
                    <Label className="text-xs text-muted-foreground">Use Case Description & Prompt Details</Label>
                    <p className="mt-1 whitespace-pre-wrap bg-background p-3 rounded-md border text-xs">
                      {selectedApp.onboarding_form?.useCaseDescription || "No detailed description provided."}
                    </p>
                  </div>
                  {selectedApp.onboarding_form?.needsApiIntegration && (
                    <div className="md:col-span-2 bg-blue-50/80 border border-blue-200 p-3 rounded-md">
                      <Label className="text-xs font-semibold text-blue-900 block mb-1">Custom API Integration Required</Label>
                      <p className="text-blue-800 text-xs">{selectedApp.onboarding_form.apiIntegrationDetails || "Details pending."}</p>
                    </div>
                  )}
                </div>
              </div>

              {/* Submitted Verification Documents */}
              {(selectedApp.onboarding_form?.gstCertificateUrl || selectedApp.onboarding_form?.businessRegistrationUrl) && (
                <div className="border rounded-md p-4 space-y-3 bg-muted/20">
                  <h4 className="font-semibold text-base border-b pb-2">Submitted Verification Documents</h4>
                  <div className="flex flex-wrap gap-4">
                    {selectedApp.onboarding_form?.gstCertificateUrl && (
                      <DocumentViewer title="GST Certificate" dataUrl={selectedApp.onboarding_form.gstCertificateUrl} />
                    )}
                    {selectedApp.onboarding_form?.businessRegistrationUrl && (
                      <DocumentViewer title="Business Registration" dataUrl={selectedApp.onboarding_form.businessRegistrationUrl} />
                    )}
                  </div>
                </div>
              )}

              {/* Raw JSON submission */}
              {selectedApp.onboarding_form && (
                <div className="pt-2">
                  <Button 
                    variant="ghost" 
                    size="sm" 
                    onClick={() => setShowRawJsonApp(!showRawJsonApp)}
                    className="text-xs text-muted-foreground"
                  >
                    {showRawJsonApp ? "Hide Raw Submission Data (JSON)" : "Show Raw Submission Data (JSON)"}
                  </Button>
                  {showRawJsonApp && (
                    <pre className="mt-2 bg-zinc-950 text-zinc-100 p-3 rounded-md text-xs font-mono overflow-auto max-h-60">
                      {JSON.stringify(selectedApp.onboarding_form, null, 2)}
                    </pre>
                  )}
                </div>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsDetailOpen(false)}>Close</Button>
            <Button onClick={() => { setIsDetailOpen(false); setIsApproveOpen(true); }}>Review & Approve</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
