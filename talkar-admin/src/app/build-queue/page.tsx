"use client";

import { adminFetch } from "@/lib/api";
import React, { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { 
  Dialog, 
  DialogContent, 
  DialogHeader, 
  DialogTitle, 
  DialogFooter,
  DialogTrigger 
} from "@/components/ui/dialog";
import { Building, Eye, UserCheck, CheckCircle2, FileText, Download, AlertCircle } from "lucide-react";

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
        <DialogTrigger render={<Button variant="link" className="p-0 h-auto text-xs text-indigo-600 font-medium hover:underline flex items-center gap-1.5" />}>
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
      <a href={dataUrl} download={`${title.replace(/\s+/g, '_').toLowerCase()}.${getExtension()}`} className="text-xs text-slate-500 hover:text-slate-900 inline-flex items-center gap-1 font-medium">
        <Download className="w-3 h-3" /> Download
      </a>
    </div>
  );
};

export default function BuildQueuePage() {
  const [customers, setCustomers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedCustomer, setSelectedCustomer] = useState<any | null>(null);
  const [isBriefOpen, setIsBriefOpen] = useState(false);
  const [showRawJsonQueue, setShowRawJsonQueue] = useState(false);
  
  const [isReadyModalOpen, setIsReadyModalOpen] = useState(false);
  const [readyCustomer, setReadyCustomer] = useState<any | null>(null);
  const [customMessage, setCustomMessage] = useState("");
  const [useCustomDetails, setUseCustomDetails] = useState(false);
  const [customAgentName, setCustomAgentName] = useState("");
  const [customPhoneNumber, setCustomPhoneNumber] = useState("");
  const [submittingReady, setSubmittingReady] = useState(false);

  useEffect(() => {
    fetchQueue();
  }, []);

  const fetchQueue = async () => {
    try {
      const res = await adminFetch(`/admin/build-queue`);
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

  const openReadyModal = (customer: any) => {
    setReadyCustomer(customer);
    setCustomMessage("");
    setUseCustomDetails(false);
    setCustomAgentName(customer.company_name || "");
    const defaultPhones = customer.phone_numbers && customer.phone_numbers.length > 0 
      ? customer.phone_numbers.join(", ") 
      : "";
    setCustomPhoneNumber(defaultPhones);
    setIsReadyModalOpen(true);
  };

  const submitMarkReady = async () => {
    if (!readyCustomer) return;
    setSubmittingReady(true);
    try {
      const payload: {
        custom_message?: string | null;
        override_agent_name?: string | null;
        override_phone_number?: string | null;
      } = {
        custom_message: customMessage.trim() || null
      };

      if (useCustomDetails) {
        if (customAgentName.trim()) {
          payload.override_agent_name = customAgentName.trim();
        }
        if (customPhoneNumber.trim()) {
          payload.override_phone_number = customPhoneNumber.trim();
        }
      }

      const res = await adminFetch(`/admin/build-queue/${readyCustomer.id}/ready`, {
        method: "PATCH",
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        setIsReadyModalOpen(false);
        fetchQueue();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`Failed to mark ready: ${err.detail || res.statusText}`);
      }
    } catch (e) {
      console.error(e);
      alert("Network error occurred. Please try again.");
    } finally {
      setSubmittingReady(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex justify-between items-center pb-2 border-b border-slate-200/70">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 flex items-center gap-2.5">
            <div className="p-2 rounded-lg bg-indigo-50 border border-indigo-100 text-indigo-600">
              <Building className="w-5 h-5" />
            </div>
            Agent Build Queue
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Customers whose deposits have cleared and are waiting for AI agent configuration & telephony setup.
          </p>
        </div>
      </div>

      <Card className="bg-white border border-slate-200/80 rounded-xl overflow-hidden shadow-none">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-50/70 border-b border-slate-200/80 hover:bg-slate-50/70">
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Customer</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Use Case</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Language</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Status</TableHead>
                <TableHead className="text-right text-slate-600 font-semibold text-xs py-3 pr-4">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-12 text-slate-400 text-xs">
                    <div className="w-5 h-5 border-2 border-indigo-600 border-t-transparent rounded-full animate-spin mx-auto mb-2" />
                    Loading build queue...
                  </TableCell>
                </TableRow>
              ) : customers.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-12 text-slate-400 text-xs">
                    No customers currently in build queue.
                  </TableCell>
                </TableRow>
              ) : (
                customers.map((c) => (
                  <TableRow key={c.id} className="border-b border-slate-100 hover:bg-slate-50/50 transition-colors">
                    <TableCell className="text-xs">
                      <div className="font-semibold text-slate-900">{c.company_name}</div>
                      <span className="text-[11px] text-slate-500">{c.contact_name}</span>
                    </TableCell>
                    <TableCell className="text-xs max-w-xs truncate text-slate-700">
                      {c.onboarding_form?.useCaseType || "N/A"}
                    </TableCell>
                    <TableCell className="text-xs text-slate-700">
                      {c.onboarding_form?.languages || "N/A"}
                    </TableCell>
                    <TableCell className="text-xs whitespace-nowrap">
                      <Badge className="bg-blue-50 text-blue-700 border-blue-200 font-medium">
                        Agent Building
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right space-x-1.5 whitespace-nowrap pr-4">
                      <Button 
                        variant="outline" 
                        size="sm"
                        className="h-7 px-2.5 text-xs font-medium border-slate-200 bg-white hover:bg-slate-50 text-slate-700 shadow-none cursor-pointer"
                        onClick={() => { setSelectedCustomer(c); setIsBriefOpen(true); }}
                      >
                        View Brief
                      </Button>
                      <Button 
                        variant="outline" 
                        size="sm"
                        className="h-7 px-2.5 text-xs font-medium border-slate-200 bg-white hover:bg-slate-50 text-slate-700 shadow-none cursor-pointer"
                        onClick={async () => {
                          try {
                            const res = await adminFetch(`/admin/build-queue/${c.id}/assign`, { method: "PATCH" });
                            if (res.ok) {
                              const data = await res.json();
                              if (data.access_token) {
                                const dograhUrl = process.env.NEXT_PUBLIC_DOGRAH_URL || "https://talkar.in";
                                window.open(`${dograhUrl}/auth/impersonate?token=${data.access_token}&refresh_token=${data.refresh_token || ''}`, "_blank");
                              } else {
                                alert("Assigned, but no magic link could be generated.");
                              }
                              fetchQueue();
                            } else {
                              const error = await res.json();
                              alert(`Failed to assign: ${error.detail || res.statusText}`);
                            }
                          } catch (e) {
                            alert("Network error. Could not assign.");
                          }
                        }}
                      >
                        Impersonate & Build
                      </Button>
                      <Button 
                        size="sm"
                        className="h-7 px-2.5 text-xs font-medium bg-indigo-600 hover:bg-indigo-700 text-white shadow-none cursor-pointer"
                        onClick={() => openReadyModal(c)}
                      >
                        Mark as Ready
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* View Full Agent Brief & Onboarding Details Modal */}
      <Dialog open={isBriefOpen} onOpenChange={setIsBriefOpen}>
        <DialogContent className="max-w-3xl w-full max-h-[85vh] overflow-y-auto bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold border-b pb-3">Agent Setup Brief & Specifications</DialogTitle>
          </DialogHeader>
          {selectedCustomer && (
            <div className="space-y-4 py-2 text-xs">
              {/* Contact Info */}
              <div className="border border-slate-200 rounded-lg p-4 space-y-3 bg-slate-50/50">
                <h4 className="font-semibold text-sm text-slate-900 border-b border-slate-200 pb-2">Contact Information</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Primary Contact</Label>
                    <p className="font-semibold text-slate-900">{selectedCustomer.contact_name || selectedCustomer.onboarding_form?.pocName || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Designation / Role</Label>
                    <p className="text-slate-800">{selectedCustomer.onboarding_form?.pocDesignation || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Email Address</Label>
                    <p className="text-slate-800 font-mono text-[11px]">{selectedCustomer.contact_email || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Phone Number</Label>
                    <p className="text-slate-800 font-mono text-[11px]">{selectedCustomer.contact_phone || selectedCustomer.onboarding_form?.pocPhone || "N/A"}</p>
                  </div>
                </div>
              </div>

              {/* Business Profile */}
              <div className="border border-slate-200 rounded-lg p-4 space-y-3 bg-slate-50/50">
                <h4 className="font-semibold text-sm text-slate-900 border-b border-slate-200 pb-2">Business & Company Profile</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Company Name</Label>
                    <p className="font-semibold text-slate-900">{selectedCustomer.company_name || selectedCustomer.onboarding_form?.businessName || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Industry</Label>
                    <p className="text-slate-800">{selectedCustomer.industry || selectedCustomer.onboarding_form?.industry || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">GST Number</Label>
                    <p className="font-mono text-slate-800">{selectedCustomer.onboarding_form?.gstNumber || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Company Size</Label>
                    <p className="text-slate-800">{selectedCustomer.onboarding_form?.companySize || "N/A"}</p>
                  </div>
                  <div className="md:col-span-2">
                    <Label className="text-[11px] text-slate-500 font-medium">Website</Label>
                    {selectedCustomer.onboarding_form?.websiteUrl ? (
                      <p>
                        <a 
                          href={selectedCustomer.onboarding_form.websiteUrl.startsWith("http") ? selectedCustomer.onboarding_form.websiteUrl : `https://${selectedCustomer.onboarding_form.websiteUrl}`} 
                          target="_blank" 
                          rel="noreferrer"
                          className="text-indigo-600 hover:underline"
                        >
                          {selectedCustomer.onboarding_form.websiteUrl}
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
                    <p className="font-semibold capitalize text-slate-800">{selectedCustomer.onboarding_form?.useCaseType || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Expected Call Volume</Label>
                    <p className="text-slate-800">{selectedCustomer.onboarding_form?.callVolume || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Target Languages</Label>
                    <p className="text-slate-800">{selectedCustomer.onboarding_form?.languages || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-500 font-medium">Integrations</Label>
                    <p className="text-slate-800">{selectedCustomer.onboarding_form?.integrations || "N/A"}</p>
                  </div>
                  <div className="md:col-span-2">
                    <Label className="text-[11px] text-slate-500 font-medium">Use Case Description & Prompt Details</Label>
                    <p className="mt-1 whitespace-pre-wrap bg-white p-3 rounded-lg border border-slate-200 text-slate-800 leading-relaxed text-xs">
                      {selectedCustomer.onboarding_form?.useCaseDescription || "No detailed description provided."}
                    </p>
                  </div>
                  {selectedCustomer.onboarding_form?.needsApiIntegration && (
                    <div className="md:col-span-2 bg-blue-50/70 border border-blue-200 p-3 rounded-lg">
                      <Label className="text-[11px] font-semibold text-blue-900 block mb-1">Custom API Integration Required</Label>
                      <p className="text-blue-800 text-xs">{selectedCustomer.onboarding_form.apiIntegrationDetails || "Details pending."}</p>
                    </div>
                  )}
                </div>
              </div>

              {/* Submitted Verification Documents */}
              {(selectedCustomer.onboarding_form?.gstCertificateUrl || selectedCustomer.onboarding_form?.businessRegistrationUrl) && (
                <div className="border border-slate-200 rounded-lg p-4 space-y-3 bg-slate-50/50">
                  <h4 className="font-semibold text-sm text-slate-900 border-b border-slate-200 pb-2">Submitted Verification Documents</h4>
                  <div className="flex flex-wrap gap-3">
                    {selectedCustomer.onboarding_form?.gstCertificateUrl && (
                      <DocumentViewer title="GST Certificate" dataUrl={selectedCustomer.onboarding_form.gstCertificateUrl} />
                    )}
                    {selectedCustomer.onboarding_form?.businessRegistrationUrl && (
                      <DocumentViewer title="Business Registration" dataUrl={selectedCustomer.onboarding_form.businessRegistrationUrl} />
                    )}
                  </div>
                </div>
              )}

              {/* Raw JSON submission */}
              {selectedCustomer.onboarding_form && (
                <div className="pt-1">
                  <Button 
                    variant="ghost" 
                    size="sm" 
                    onClick={() => setShowRawJsonQueue(!showRawJsonQueue)}
                    className="text-xs text-slate-500 hover:text-slate-800 h-7"
                  >
                    {showRawJsonQueue ? "Hide Raw Brief Data (JSON)" : "Show Raw Brief Data (JSON)"}
                  </Button>
                  {showRawJsonQueue && (
                    <pre className="mt-2 bg-slate-950 text-slate-100 p-3 rounded-lg text-xs font-mono overflow-auto max-h-60 border border-slate-800">
                      {JSON.stringify(selectedCustomer.onboarding_form, null, 2)}
                    </pre>
                  )}
                </div>
              )}
            </div>
          )}
          <DialogFooter className="border-t pt-3">
            <Button variant="outline" size="sm" onClick={() => setIsBriefOpen(false)} className="text-xs border-slate-200">Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Mark Ready Modal */}
      <Dialog open={isReadyModalOpen} onOpenChange={setIsReadyModalOpen}>
        <DialogContent className="max-w-lg bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold border-b pb-3">Mark Agent Ready & Notify Customer</DialogTitle>
          </DialogHeader>
          {readyCustomer && (
            <div className="space-y-4 py-2 text-xs">
              <p className="text-slate-600">
                This will send an email notification to <span className="font-semibold text-slate-900">{readyCustomer.contact_email}</span> letting them know their AI agent is built and ready for calls.
              </p>

              {/* Notification Details Preview */}
              <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3.5 space-y-2.5">
                <div className="flex items-center justify-between font-semibold text-slate-500 uppercase tracking-wider text-[10px]">
                  <span>Notification Details Preview</span>
                  {useCustomDetails ? (
                    <Badge className="bg-amber-50 text-amber-800 border-amber-200 text-[10px] font-medium">
                      Custom Override
                    </Badge>
                  ) : (
                    <Badge className="bg-emerald-50 text-emerald-700 border-emerald-200 text-[10px] font-medium">
                      Auto-detected Default
                    </Badge>
                  )}
                </div>

                <div className="grid grid-cols-2 gap-3 pt-1">
                  <div>
                    <span className="text-[11px] text-slate-400 block mb-0.5 font-medium">Agent Name</span>
                    <span className="font-semibold text-slate-900 text-xs break-words">
                      {useCustomDetails && customAgentName.trim()
                        ? customAgentName.trim()
                        : (readyCustomer.company_name || "N/A")}
                    </span>
                  </div>
                  <div>
                    <span className="text-[11px] text-slate-400 block mb-0.5 font-medium">Assigned Phone Number</span>
                    <span className="font-mono text-xs font-semibold text-slate-900 break-words block">
                      {useCustomDetails && customPhoneNumber.trim()
                        ? customPhoneNumber.trim()
                        : (readyCustomer.phone_numbers && readyCustomer.phone_numbers.length > 0 
                            ? readyCustomer.phone_numbers.join(", ") 
                            : <span className="text-amber-600 font-sans font-normal text-xs">No number assigned yet</span>)}
                    </span>
                  </div>
                </div>
              </div>

              {/* Checkbox to toggle custom override */}
              <div className="flex items-center gap-2 pt-1">
                <input
                  type="checkbox"
                  id="override-details-checkbox"
                  checked={useCustomDetails}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    setUseCustomDetails(checked);
                    if (checked) {
                      if (!customAgentName) setCustomAgentName(readyCustomer.company_name || "");
                      if (!customPhoneNumber) {
                        setCustomPhoneNumber(readyCustomer.phone_numbers?.join(", ") || "");
                      }
                    }
                  }}
                  className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500 cursor-pointer"
                />
                <Label htmlFor="override-details-checkbox" className="text-xs font-medium text-slate-700 cursor-pointer select-none">
                  Customize agent name & phone number
                </Label>
              </div>

              {/* Conditional Inputs */}
              {useCustomDetails && (
                <div className="space-y-3 p-3.5 rounded-lg border border-indigo-100 bg-indigo-50/40">
                  <div className="space-y-1">
                    <Label className="text-[11px] font-medium text-slate-700">Custom Agent Name</Label>
                    <Input 
                      placeholder="e.g. Sales Assistant"
                      value={customAgentName}
                      onChange={(e) => setCustomAgentName(e.target.value)}
                      className="bg-white border-slate-200 h-8 text-xs"
                    />
                    <p className="text-[10px] text-slate-400">Default: {readyCustomer.company_name}</p>
                  </div>
                  <div className="space-y-1">
                    <Label className="text-[11px] font-medium text-slate-700">Custom Phone Number</Label>
                    <Input 
                      placeholder="e.g. +91 80 1234 5678"
                      value={customPhoneNumber}
                      onChange={(e) => setCustomPhoneNumber(e.target.value)}
                      className="bg-white border-slate-200 h-8 text-xs"
                    />
                    <p className="text-[10px] text-slate-400">
                      Default: {readyCustomer.phone_numbers?.length ? readyCustomer.phone_numbers.join(", ") : "None assigned"}
                    </p>
                  </div>
                </div>
              )}

              {/* Custom Admin Note */}
              <div className="space-y-1.5">
                <Label className="text-slate-700 font-medium text-xs">Custom Admin Note (Optional)</Label>
                <Textarea 
                  placeholder="e.g. We tweaked your prompt and tested order lookups, it is performing smoothly!"
                  value={customMessage}
                  onChange={(e) => setCustomMessage(e.target.value)}
                  rows={3}
                  className="text-xs bg-white border-slate-200 focus:ring-indigo-500"
                />
                <p className="text-[10px] text-slate-400">
                  Included in the notification email under &quot;Admin Note&quot;.
                </p>
              </div>
            </div>
          )}
          <DialogFooter className="border-t pt-3">
            <Button variant="outline" size="sm" onClick={() => setIsReadyModalOpen(false)} disabled={submittingReady} className="text-xs border-slate-200">
              Cancel
            </Button>
            <Button size="sm" onClick={submitMarkReady} disabled={submittingReady} className="text-xs bg-indigo-600 hover:bg-indigo-700 text-white">
              {submittingReady ? "Sending Email..." : "Confirm & Send Email"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
