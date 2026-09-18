"use client";

import { adminFetch } from "@/lib/api";
import React, { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
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
import { ListTodo, CheckCircle2, XCircle, HelpCircle, Eye, FileText, Download } from "lucide-react";

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
    <div className="flex items-center gap-2.5 bg-slate-50 p-2.5 px-3 rounded-lg border border-slate-200 text-xs">
      <Dialog>
        <DialogTrigger render={<Button variant="link" className="p-0 h-auto text-xs text-[#fe6905] font-medium hover:underline flex items-center gap-1.5" />}>
          <FileText className="w-3.5 h-3.5" /> {title} (View)
        </DialogTrigger>
        <DialogContent className="max-w-4xl w-full h-[80vh] flex flex-col bg-white border-slate-200">
          <DialogHeader>
            <DialogTitle className="text-sm font-semibold">{title}</DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-auto bg-slate-50 rounded-lg border border-slate-200 flex items-center justify-center p-4">
            {isPdf ? (
              <iframe src={dataUrl} className="w-full h-full border-0 rounded-lg bg-white" title={title} />
            ) : isImage ? (
              <img src={dataUrl} alt={title} className="max-w-full max-h-full object-contain rounded-lg shadow-sm" />
            ) : (
              <p className="text-slate-400 text-xs">Preview not available for this file type.</p>
            )}
          </div>
        </DialogContent>
      </Dialog>
      <span className="text-slate-300">•</span>
      <a 
        href={dataUrl} 
        download={`${title.replace(/\s+/g, '_').toLowerCase()}.${getExtension()}`} 
        className="text-xs text-slate-500 hover:text-slate-900 inline-flex items-center gap-1 font-medium"
      >
        <Download className="w-3 h-3" /> Download
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
      {/* Page Header */}
      <div className="flex justify-between items-center pb-2 border-b border-slate-200/70">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 flex items-center gap-2.5">
            Applications Queue
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Review, qualify, and approve incoming Talkar customer applications.
          </p>
        </div>
      </div>

      <Card className="bg-white border border-slate-200/80 rounded-xl overflow-hidden shadow-none">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-50/70 border-b border-slate-200/80 hover:bg-slate-50/70">
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Date</TableHead>
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
                    Loading applications...
                  </TableCell>
                </TableRow>
              ) : apps.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-12 text-slate-400 text-xs">
                    No applications currently waiting in queue.
                  </TableCell>
                </TableRow>
              ) : (
                apps.map((app) => (
                  <TableRow key={app.id} className="border-b border-slate-100 hover:bg-slate-50/50 transition-colors">
                    <TableCell className="text-xs text-slate-500 whitespace-nowrap">
                      {new Date(app.created_at).toLocaleDateString()}
                    </TableCell>
                    <TableCell className="text-xs">
                      <div className="font-semibold text-slate-900">{app.company_name}</div>
                      {app.onboarding_form?.needsApiIntegration && (
                        <div className="mt-1">
                          <span className="inline-flex items-center text-[10px] font-semibold px-2 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-200">
                            Custom API Req
                          </span>
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="text-xs">
                      <div className="text-slate-800 font-medium">{app.contact_name}</div>
                      <span className="text-[11px] text-slate-500">{app.contact_email}</span>
                    </TableCell>
                    <TableCell className="text-xs whitespace-nowrap">
                      {app.status === "pending_approval" ? (
                        <Badge className="bg-purple-50 text-purple-700 border-purple-200 font-medium">
                          New Agent Brief
                        </Badge>
                      ) : app.status === "info_requested" ? (
                        <Badge className="bg-sky-50 text-sky-700 border-sky-200 font-medium">
                          Info Requested
                        </Badge>
                      ) : (
                        <Badge className="bg-amber-50 text-amber-800 border-amber-200 font-medium">
                          Under Review
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right space-x-1.5 whitespace-nowrap pr-4">
                      <Button 
                        variant="outline" 
                        size="sm"
                        className="h-7 px-2.5 text-xs font-medium border-slate-200 bg-white hover:bg-slate-50 text-slate-700 shadow-none cursor-pointer"
                        onClick={() => { setSelectedApp(app); setIsDetailOpen(true); }}
                      >
                        View Details
                      </Button>
                      <Button 
                        variant="outline" 
                        size="sm"
                        className="h-7 px-2.5 text-xs font-medium border-slate-200 bg-white hover:bg-slate-50 text-slate-700 shadow-none cursor-pointer"
                        onClick={() => { setSelectedApp(app); setIsRequestInfoOpen(true); }}
                      >
                        Request Info
                      </Button>
                      <Button 
                        variant="outline" 
                        size="sm"
                        className="h-7 px-2.5 text-xs font-medium border-slate-200 bg-white hover:bg-red-50 text-red-600 hover:text-red-700 shadow-none cursor-pointer"
                        onClick={() => { setSelectedApp(app); setIsRejectOpen(true); }}
                      >
                        Reject
                      </Button>
                      <Button 
                        size="sm"
                        className="h-7 px-2.5 text-xs font-medium bg-[#fe6905] hover:bg-[#e55e04] text-white shadow-none cursor-pointer"
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

      {/* APPROVE DIALOG */}
      <Dialog open={isApproveOpen} onOpenChange={setIsApproveOpen}>
        <DialogContent className="bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold border-b pb-3">Approve Application</DialogTitle>
          </DialogHeader>
          <div className="py-3 space-y-4 text-xs">
            <p className="text-slate-600 leading-relaxed">
              {selectedApp?.onboarding_form?.needsApiIntegration 
                ? `Approving ${selectedApp?.company_name} will generate a custom integration fee Razorpay payment link and notify the customer.`
                : `Approving ${selectedApp?.company_name} will mark their agent as ready for building. No integration fee is required by default.`}
            </p>
            
            {selectedApp?.onboarding_form?.needsApiIntegration && (
              <div className="bg-blue-50/70 border border-blue-200 p-3 rounded-lg text-xs">
                <span className="font-semibold text-blue-900 block mb-1">Customer's Integration Request:</span>
                <span className="text-blue-800">{selectedApp.onboarding_form.apiIntegrationDetails}</span>
              </div>
            )}
            <div className="space-y-1.5">
              <label className="text-slate-700 font-medium">Custom Integration Fee (₹)</label>
              <Input 
                type="number" 
                value={integrationFee} 
                onChange={(e: any) => setIntegrationFee(e.target.value)} 
                placeholder="0 for no fee"
                className="h-9 text-xs bg-white border-slate-200"
              />
              <p className="text-[11px] text-slate-400">₹0 = agent build is always free. Customer selects their own tier after wallet deposit.</p>
            </div>
            <div className="space-y-1.5">
              <label className="text-slate-700 font-medium">Approved Tier</label>
              <Select value={approvedTier} onValueChange={(v) => v && setApprovedTier(v)}>
                <SelectTrigger className="h-9 text-xs bg-white border-slate-200"><SelectValue placeholder="Select tier" /></SelectTrigger>
                <SelectContent className="bg-white border-slate-200">
                  <SelectItem value="starter">Starter</SelectItem>
                  <SelectItem value="pro">Pro</SelectItem>
                  <SelectItem value="elite">Elite</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {parseInt(integrationFee) > 0 && (
              <div className="space-y-1.5">
                <label className="text-slate-700 font-medium">Integration Description</label>
                <textarea 
                  className="w-full min-h-[80px] p-2.5 border border-slate-200 rounded-lg text-xs bg-white focus:outline-none focus:border-[#fe6905]" 
                  value={integrationDescription} 
                  onChange={(e: any) => setIntegrationDescription(e.target.value)} 
                  placeholder="E.g., HubSpot CRM webhook integration + Custom reporting pipeline..."
                />
              </div>
            )}
          </div>
          <DialogFooter className="border-t pt-3">
            <Button variant="outline" size="sm" onClick={() => setIsApproveOpen(false)} className="text-xs border-slate-200">Cancel</Button>
            <Button size="sm" onClick={handleApprove} className="text-xs bg-[#fe6905] hover:bg-[#e55e04] text-white">Confirm Approval</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* REJECT DIALOG */}
      <Dialog open={isRejectOpen} onOpenChange={setIsRejectOpen}>
        <DialogContent className="bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold border-b pb-3 text-red-600">Reject Application</DialogTitle>
          </DialogHeader>
          <div className="py-3 space-y-3 text-xs">
            <p className="text-slate-600">
              Please provide a reason for rejecting <strong className="text-slate-900">{selectedApp?.company_name}</strong>. This will be shown to the customer.
            </p>
            <textarea 
              className="w-full min-h-[100px] p-3 border border-slate-200 rounded-lg text-xs bg-white focus:outline-none focus:border-red-500" 
              placeholder="e.g. Incomplete GST documents..."
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
            />
          </div>
          <DialogFooter className="border-t pt-3">
            <Button variant="outline" size="sm" onClick={() => setIsRejectOpen(false)} className="text-xs border-slate-200">Cancel</Button>
            <Button size="sm" variant="destructive" onClick={handleReject} disabled={!rejectReason.trim()} className="text-xs">Confirm Rejection</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* REQUEST INFO DIALOG */}
      <Dialog open={isRequestInfoOpen} onOpenChange={setIsRequestInfoOpen}>
        <DialogContent className="bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold border-b pb-3">Request Additional Information</DialogTitle>
          </DialogHeader>
          <div className="py-3 space-y-3 text-xs">
            <p className="text-slate-600">
              What additional information do you need from <strong className="text-slate-900">{selectedApp?.company_name}</strong>?
            </p>
            <textarea 
              className="w-full min-h-[100px] p-3 border border-slate-200 rounded-lg text-xs bg-white focus:outline-none focus:border-[#fe6905]" 
              placeholder="e.g. Please clarify your use case for outbound calls..."
              value={requestInfoMessage}
              onChange={(e) => setRequestInfoMessage(e.target.value)}
            />
          </div>
          <DialogFooter className="border-t pt-3">
            <Button variant="outline" size="sm" onClick={() => setIsRequestInfoOpen(false)} className="text-xs border-slate-200">Cancel</Button>
            <Button 
              size="sm"
              className="text-xs bg-[#fe6905] hover:bg-[#e55e04] text-white"
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

      {/* VIEW FULL SUBMISSION DETAILS MODAL */}
      <Dialog open={isDetailOpen} onOpenChange={setIsDetailOpen}>
        <DialogContent className="max-w-3xl w-full max-h-[85vh] overflow-y-auto bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold border-b pb-3">Application Submission Details</DialogTitle>
          </DialogHeader>
          {selectedApp && (
            <div className="space-y-4 py-2 text-xs">
              {/* Contact Info */}
              <div className="border border-slate-200 rounded-lg p-4 space-y-3 bg-slate-50/50">
                <h4 className="font-semibold text-sm text-slate-900 border-b border-slate-200 pb-2">Contact Information</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Primary Contact</Label>
                    <p className="font-semibold text-slate-900">{selectedApp.contact_name || selectedApp.onboarding_form?.pocName || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Designation / Role</Label>
                    <p className="text-slate-800">{selectedApp.onboarding_form?.pocDesignation || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Email Address</Label>
                    <p className="text-slate-800 font-mono text-[11px]">{selectedApp.contact_email || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Phone Number</Label>
                    <p className="text-slate-800 font-mono text-[11px]">{selectedApp.contact_phone || selectedApp.onboarding_form?.pocPhone || "N/A"}</p>
                  </div>
                </div>
              </div>

              {/* Business Profile */}
              <div className="border border-slate-200 rounded-lg p-4 space-y-3 bg-slate-50/50">
                <h4 className="font-semibold text-sm text-slate-900 border-b border-slate-200 pb-2">Business & Company Profile</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Company Name</Label>
                    <p className="font-semibold text-slate-900">{selectedApp.company_name || selectedApp.onboarding_form?.businessName || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Industry</Label>
                    <p className="text-slate-800">{selectedApp.industry || selectedApp.onboarding_form?.industry || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">GST Number</Label>
                    <p className="font-mono text-slate-800">{selectedApp.onboarding_form?.gstNumber || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Company Size</Label>
                    <p className="text-slate-800">{selectedApp.onboarding_form?.companySize || "N/A"}</p>
                  </div>
                  <div className="md:col-span-2">
                    <Label className="text-[11px] text-slate-500 font-medium">Website</Label>
                    {selectedApp.onboarding_form?.websiteUrl ? (
                      <p>
                        <a 
                          href={selectedApp.onboarding_form.websiteUrl.startsWith("http") ? selectedApp.onboarding_form.websiteUrl : `https://${selectedApp.onboarding_form.websiteUrl}`} 
                          target="_blank" 
                          rel="noreferrer"
                          className="text-[#fe6905] hover:underline"
                        >
                          {selectedApp.onboarding_form.websiteUrl}
                        </a>
                      </p>
                    ) : (
                      <p className="text-slate-400">N/A</p>
                    )}
                  </div>
                </div>
              </div>

              {/* Voice Agent & Use Case */}
              <div className="border border-slate-200 rounded-lg p-4 space-y-3 bg-slate-50/50">
                <h4 className="font-semibold text-sm text-slate-900 border-b border-slate-200 pb-2">Agent & Use Case Requirements</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Call Direction / Type</Label>
                    <p className="font-semibold capitalize text-slate-800">{selectedApp.onboarding_form?.useCaseType || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Expected Call Volume</Label>
                    <p className="text-slate-800">{selectedApp.onboarding_form?.callVolume || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Target Languages</Label>
                    <p className="text-slate-800">{selectedApp.onboarding_form?.languages || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Integrations</Label>
                    <p className="text-slate-800">{selectedApp.onboarding_form?.integrations || "N/A"}</p>
                  </div>
                  <div className="md:col-span-2">
                    <Label className="text-[11px] text-slate-500 font-medium">Use Case Description & Prompt Details</Label>
                    <p className="mt-1 whitespace-pre-wrap bg-white p-3 rounded-lg border border-slate-200 text-slate-800 leading-relaxed text-xs">
                      {selectedApp.onboarding_form?.useCaseDescription || "No detailed description provided."}
                    </p>
                  </div>
                  {selectedApp.onboarding_form?.needsApiIntegration && (
                    <div className="md:col-span-2 bg-blue-50/70 border border-blue-200 p-3 rounded-lg">
                      <Label className="text-[11px] font-semibold text-blue-900 block mb-1">Custom API Integration Required</Label>
                      <p className="text-blue-800 text-xs">{selectedApp.onboarding_form.apiIntegrationDetails || "Details pending."}</p>
                    </div>
                  )}
                </div>
              </div>

              {/* Submitted Verification Documents */}
              {(selectedApp.onboarding_form?.gstCertificateUrl || selectedApp.onboarding_form?.businessRegistrationUrl) && (
                <div className="border border-slate-200 rounded-lg p-4 space-y-3 bg-slate-50/50">
                  <h4 className="font-semibold text-sm text-slate-900 border-b border-slate-200 pb-2">Submitted Verification Documents</h4>
                  <div className="flex flex-wrap gap-3">
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
                <div className="pt-1">
                  <Button 
                    variant="ghost" 
                    size="sm" 
                    onClick={() => setShowRawJsonApp(!showRawJsonApp)}
                    className="text-xs text-slate-500 hover:text-slate-800 h-7"
                  >
                    {showRawJsonApp ? "Hide Raw Submission Data (JSON)" : "Show Raw Submission Data (JSON)"}
                  </Button>
                  {showRawJsonApp && (
                    <pre className="mt-2 bg-slate-950 text-slate-100 p-3 rounded-lg text-xs font-mono overflow-auto max-h-60 border border-slate-800">
                      {JSON.stringify(selectedApp.onboarding_form, null, 2)}
                    </pre>
                  )}
                </div>
              )}
            </div>
          )}
          <DialogFooter className="border-t pt-3">
            <Button variant="outline" size="sm" onClick={() => setIsDetailOpen(false)} className="text-xs border-slate-200">Close</Button>
            <Button size="sm" onClick={() => { setIsDetailOpen(false); setIsApproveOpen(true); }} className="text-xs bg-[#fe6905] hover:bg-[#e55e04] text-white">Review & Approve</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
