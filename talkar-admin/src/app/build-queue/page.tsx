"use client";
import { adminFetch } from "@/lib/api";

import React, { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { 
  Dialog, 
  DialogContent, 
  DialogHeader, 
  DialogTitle, 
  DialogFooter,
  DialogTrigger 
} from "@/components/ui/dialog";

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

export default function BuildQueuePage() {
  const [customers, setCustomers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedCustomer, setSelectedCustomer] = useState<any | null>(null);
  const [isBriefOpen, setIsBriefOpen] = useState(false);
  const [showRawJsonQueue, setShowRawJsonQueue] = useState(false);

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

  const handleMarkReady = async (customerId: number) => {
    try {
      const res = await adminFetch(`/admin/build-queue/${customerId}/ready`, {
        method: "PATCH"
      });
      if (res.ok) {
        fetchQueue();
      }
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Agent Build Queue</h2>
          <p className="text-muted-foreground mt-2">
            Customers whose payments have cleared and are waiting for manual agent setup.
          </p>
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Customer</TableHead>
                <TableHead>Use Case</TableHead>
                <TableHead>Language</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8">Loading queue...</TableCell>
                </TableRow>
              ) : customers.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">No customers in build queue.</TableCell>
                </TableRow>
              ) : (
                customers.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="font-medium">
                      {c.company_name}
                      <br />
                      <span className="text-xs text-muted-foreground">{c.contact_name}</span>
                    </TableCell>
                    <TableCell className="max-w-xs truncate">
                      {c.onboarding_form?.useCaseType || "N/A"}
                    </TableCell>
                    <TableCell>
                      {c.onboarding_form?.languages || "N/A"}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="bg-blue-100 text-blue-800 hover:bg-blue-100">
                        Agent Building
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right space-x-2">
                      <Button 
                        variant="secondary" 
                        size="sm"
                        onClick={() => { setSelectedCustomer(c); setIsBriefOpen(true); }}
                      >
                        View Brief
                      </Button>
                      <Button variant="outline" size="sm" onClick={async () => {
                        try {
                          const res = await adminFetch(`/admin/build-queue/${c.id}/assign`, { method: "PATCH" });
                          if (res.ok) {
                            const data = await res.json();
                            if (data.access_token) {
                              // Open Dograh impersonation route in a new tab
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
                      }}>Impersonate & Build</Button>
                      <Button size="sm" onClick={() => handleMarkReady(c.id)}>Mark as Ready</Button>
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
        <DialogContent className="max-w-3xl w-full max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Agent Setup Brief & Specifications</DialogTitle>
          </DialogHeader>
          {selectedCustomer && (
            <div className="space-y-6 py-2 text-sm">
              {/* Contact Info */}
              <div className="border rounded-md p-4 space-y-3 bg-muted/20">
                <h4 className="font-semibold text-base border-b pb-2">Contact Information</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <Label className="text-xs text-muted-foreground">Primary Contact</Label>
                    <p className="font-medium">{selectedCustomer.contact_name || selectedCustomer.onboarding_form?.pocName || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Designation / Role</Label>
                    <p>{selectedCustomer.onboarding_form?.pocDesignation || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Email Address</Label>
                    <p>{selectedCustomer.contact_email || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Phone Number</Label>
                    <p>{selectedCustomer.contact_phone || selectedCustomer.onboarding_form?.pocPhone || "N/A"}</p>
                  </div>
                </div>
              </div>

              {/* Business Profile */}
              <div className="border rounded-md p-4 space-y-3 bg-muted/20">
                <h4 className="font-semibold text-base border-b pb-2">Business & Company Profile</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <Label className="text-xs text-muted-foreground">Company Name</Label>
                    <p className="font-medium">{selectedCustomer.company_name || selectedCustomer.onboarding_form?.businessName || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Industry</Label>
                    <p>{selectedCustomer.industry || selectedCustomer.onboarding_form?.industry || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">GST Number</Label>
                    <p className="font-mono">{selectedCustomer.onboarding_form?.gstNumber || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Company Size</Label>
                    <p>{selectedCustomer.onboarding_form?.companySize || "N/A"}</p>
                  </div>
                  <div className="md:col-span-2">
                    <Label className="text-xs text-muted-foreground">Website</Label>
                    {selectedCustomer.onboarding_form?.websiteUrl ? (
                      <p>
                        <a 
                          href={selectedCustomer.onboarding_form.websiteUrl.startsWith("http") ? selectedCustomer.onboarding_form.websiteUrl : `https://${selectedCustomer.onboarding_form.websiteUrl}`} 
                          target="_blank" 
                          rel="noreferrer"
                          className="text-blue-600 hover:underline"
                        >
                          {selectedCustomer.onboarding_form.websiteUrl}
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
                    <p className="font-medium capitalize">{selectedCustomer.onboarding_form?.useCaseType || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Expected Call Volume</Label>
                    <p>{selectedCustomer.onboarding_form?.callVolume || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Target Languages</Label>
                    <p>{selectedCustomer.onboarding_form?.languages || "N/A"}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-muted-foreground">Integrations</Label>
                    <p>{selectedCustomer.onboarding_form?.integrations || "N/A"}</p>
                  </div>
                  <div className="md:col-span-2">
                    <Label className="text-xs text-muted-foreground">Use Case Description & Prompt Details</Label>
                    <p className="mt-1 whitespace-pre-wrap bg-background p-3 rounded-md border text-xs">
                      {selectedCustomer.onboarding_form?.useCaseDescription || "No detailed description provided."}
                    </p>
                  </div>
                  {selectedCustomer.onboarding_form?.needsApiIntegration && (
                    <div className="md:col-span-2 bg-blue-50/80 border border-blue-200 p-3 rounded-md">
                      <Label className="text-xs font-semibold text-blue-900 block mb-1">Custom API Integration Required</Label>
                      <p className="text-blue-800 text-xs">{selectedCustomer.onboarding_form.apiIntegrationDetails || "Details pending."}</p>
                    </div>
                  )}
                </div>
              </div>

              {/* Submitted Verification Documents */}
              {(selectedCustomer.onboarding_form?.gstCertificateUrl || selectedCustomer.onboarding_form?.businessRegistrationUrl) && (
                <div className="border rounded-md p-4 space-y-3 bg-muted/20">
                  <h4 className="font-semibold text-base border-b pb-2">Submitted Verification Documents</h4>
                  <div className="flex flex-wrap gap-4">
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
                <div className="pt-2">
                  <Button 
                    variant="ghost" 
                    size="sm" 
                    onClick={() => setShowRawJsonQueue(!showRawJsonQueue)}
                    className="text-xs text-muted-foreground"
                  >
                    {showRawJsonQueue ? "Hide Raw Brief Data (JSON)" : "Show Raw Brief Data (JSON)"}
                  </Button>
                  {showRawJsonQueue && (
                    <pre className="mt-2 bg-zinc-950 text-zinc-100 p-3 rounded-md text-xs font-mono overflow-auto max-h-60">
                      {JSON.stringify(selectedCustomer.onboarding_form, null, 2)}
                    </pre>
                  )}
                </div>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsBriefOpen(false)}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
