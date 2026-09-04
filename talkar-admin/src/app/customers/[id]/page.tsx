"use client";
import { adminFetch } from "@/lib/api";

import React, { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription, DialogTrigger } from "@/components/ui/dialog";

const DocumentViewer = ({ title, dataUrl }: { title: string, dataUrl: string }) => {
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
    <div className="flex items-center gap-4">
      <Dialog>
        <DialogTrigger render={<Button variant="link" className="p-0 h-auto text-sm text-blue-600" />}>
          📄 {title} (View)
        </DialogTrigger>
        <DialogContent className="max-w-4xl w-full h-[80vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-auto bg-zinc-100/50 rounded-md border flex items-center justify-center p-4">
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
      <a href={dataUrl} download={`${title.replace(/\s+/g, '_').toLowerCase()}.${getExtension()}`} className="text-xs text-zinc-500 hover:text-zinc-800 underline underline-offset-2">
        Download
      </a>
    </div>
  );
};
export default function CustomerDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;
  
  const [customer, setCustomer] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  // Credit modal
  const [isCreditOpen, setIsCreditOpen] = useState(false);
  const [creditAmount, setCreditAmount] = useState("");
  const [creditDesc, setCreditDesc] = useState("");

  // Deduct modal
  const [isDeductOpen, setIsDeductOpen] = useState(false);
  const [deductAmount, setDeductAmount] = useState("");
  const [deductReason, setDeductReason] = useState("");

  // Plan upgrade modal
  const [isPlanOpen, setIsPlanOpen] = useState(false);
  const [newPlan, setNewPlan] = useState("");
  const [planLoading, setPlanLoading] = useState(false);

  const [phoneNumbers, setPhoneNumbers] = useState<any[]>([]);
  const [agents, setAgents] = useState<any[]>([]);
  const [showRawJson, setShowRawJson] = useState(false);
  const [phoneNumberInput, setPhoneNumberInput] = useState("");
  const [plivoIdInput, setPlivoIdInput] = useState("");
  const [isAssigningPhone, setIsAssigningPhone] = useState(false);

  useEffect(() => {
    fetchCustomer();
  }, [id]);

  const fetchCustomer = async () => {
    try {
      const [res, phoneRes, agentsRes] = await Promise.all([
        adminFetch(`/admin/customers/${id}`),
        adminFetch(`/admin/customers/${id}/phone-numbers`),
        adminFetch(`/admin/customers/${id}/agents`)
      ]);
      if (res.ok) {
        const data = await res.json();
        setCustomer(data);
        setNewPlan(data.onboarding_form?.approved_tier || "");
      }
      if (phoneRes.ok) {
        const pData = await phoneRes.json();
        setPhoneNumbers(pData);
      }
      if (agentsRes.ok) {
        const aData = await agentsRes.json();
        setAgents(aData);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleGrantCredit = async () => {
    try {
      const amountPaise = parseInt(creditAmount) * 100;
      const res = await adminFetch(`/admin/customers/${id}/credit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount_paise: amountPaise, description: creditDesc })
      });
      if (res.ok) {
        setIsCreditOpen(false);
        setCreditAmount("");
        setCreditDesc("");
        alert("Credit granted successfully!");
        fetchCustomer();
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleDeduct = async () => {
    try {
      const amountPaise = parseInt(deductAmount) * 100;
      const res = await adminFetch(`/admin/customers/${id}/deduct`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount_paise: amountPaise, reason: deductReason })
      });
      if (res.ok) {
        setIsDeductOpen(false);
        setDeductAmount("");
        setDeductReason("");
        alert("Deduction successful!");
        fetchCustomer();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`Failed to deduct: ${err.detail || "Unknown error"}`);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleUpdateRate = async (agentId: number, rateStr: string) => {
    try {
      const ratePaise = rateStr ? parseInt(rateStr) * 100 : null;
      const res = await adminFetch(`/admin/customers/${id}/agents/${agentId}/rate`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ per_minute_rate_paise: ratePaise })
      });
      if (res.ok) {
        alert("Rate updated successfully!");
        fetchCustomer();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`Failed to update rate: ${err.detail || "Unknown error"}`);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleUpgradePlan = async () => {
    if (!newPlan) return;
    setPlanLoading(true);
    try {
      const res = await adminFetch(`/admin/customers/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier: newPlan })
      });
      if (res.ok) {
        setIsPlanOpen(false);
        alert(`Tier updated to "${newPlan}" and Dograh config has been re-provisioned!`);
        fetchCustomer();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`Failed to update plan: ${err.detail || "Unknown error"}`);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setPlanLoading(false);
    }
  };

  const handleSuspend = async () => {
    if (!confirm("Are you sure you want to suspend this account?")) return;
    try {
      const res = await adminFetch(`/admin/customers/${id}/suspend`, { method: "POST" });
      if (res.ok) fetchCustomer();
    } catch (e) {
      console.error(e);
    }
  };

  const handleDenyUpgrade = async () => {
    if (!confirm("Are you sure you want to deny this upgrade request?")) return;
    try {
      const res = await adminFetch(`/admin/customers/${id}/deny-tier-upgrade`, { method: "POST" });
      if (res.ok) fetchCustomer();
    } catch (e) {
      console.error(e);
    }
  };

  const handleAssignPhone = async () => {
    if (!phoneNumberInput) return;
    setIsAssigningPhone(true);
    try {
      const res = await adminFetch(`/admin/customers/${id}/assign-phone-number`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ number: phoneNumberInput, plivo_number_id: plivoIdInput })
      });
      if (res.ok) {
        setPhoneNumberInput("");
        setPlivoIdInput("");
        fetchCustomer();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`Failed to assign: ${err.detail || "Unknown error"}`);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsAssigningPhone(false);
    }
  };

  const handleRetryProvisioning = async () => {
    try {
      const res = await adminFetch(`/admin/customers/${id}/provision/retry`, { method: "POST" });
      if (res.ok) {
        alert("Provisioning queued for retry!");
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`Provisioning failed: ${err.detail || "Unknown error"}`);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const [isImpersonating, setIsImpersonating] = useState(false);
  const handleImpersonate = async () => {
    setIsImpersonating(true);
    try {
      const res = await adminFetch(`/admin/customers/${id}/impersonate`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        const token = data.access_token;
        const refreshToken = data.refresh_token;
        // The main site lives at talkar.in. When impersonating locally, we might need to point to localhost or talkar.in.
        // The auth route is /auth/impersonate
        const TALKAR_UI_URL = process.env.NEXT_PUBLIC_TALKAR_URL || "https://talkar.in";
        let url = `${TALKAR_UI_URL}/auth/impersonate?token=${token}`;
        if (refreshToken) {
          url += `&refresh_token=${refreshToken}`;
        }
        window.open(url, '_blank');
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`Impersonation failed: ${err.detail || "Unknown error"}`);
      }
    } catch (e) {
      console.error(e);
      alert("Failed to connect to admin API");
    } finally {
      setIsImpersonating(false);
    }
  };

  if (loading) return <div>Loading...</div>;
  if (!customer) return <div>Customer not found.</div>;

  const currentPlan = customer.onboarding_form?.approved_tier || "None";

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <Button variant="link" className="p-0 h-auto mb-2" onClick={() => router.push('/customers')}>
            ← Back to Directory
          </Button>
          <h2 className="text-3xl font-bold tracking-tight">{customer.company_name}</h2>
          <div className="flex gap-2 mt-2">
            <Badge variant="outline">ID: {customer.id}</Badge>
            <Badge>{customer.status}</Badge>
          </div>
        </div>
        <div className="space-x-2">
          <Button variant="default" onClick={handleImpersonate} disabled={isImpersonating}>
            {isImpersonating ? "..." : "Impersonate User"}
          </Button>
          <Button variant="outline" onClick={() => setIsCreditOpen(true)}>Grant Manual Credit</Button>
          <Button variant="outline" onClick={() => setIsDeductOpen(true)}>Deduct Balance</Button>
          <Button variant="outline" onClick={() => setIsPlanOpen(true)}>Change Tier</Button>
          <Button variant="outline" onClick={handleRetryProvisioning}>Retry Provisioning</Button>
          <Button variant="destructive" onClick={handleSuspend}>Suspend Account</Button>
        </div>
      </div>

      {customer.onboarding_form?.tier_upgrade_requested && (
        <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-4">
          <p className="font-medium">⚠️ Tier Upgrade Request Pending</p>
          <p className="text-sm text-muted-foreground mt-1">
            Customer requested upgrade to <strong>{customer.onboarding_form.tier_upgrade_requested}</strong>.
          </p>
          <div className="mt-3 flex gap-2">
            <Button 
              onClick={() => {
                setNewPlan(customer.onboarding_form.tier_upgrade_requested);
                setIsPlanOpen(true);
              }}
            >
              Approve Upgrade
            </Button>
            <Button variant="outline" onClick={handleDenyUpgrade}>
              Deny Request
            </Button>
          </div>
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Contact Information */}
        <Card>
          <CardHeader><CardTitle className="text-base font-semibold">Contact Information</CardTitle></CardHeader>
          <CardContent className="space-y-4 text-sm">
            <div>
              <Label className="text-muted-foreground text-xs">Primary Contact Name</Label>
              <p className="font-medium">{customer.contact_name || customer.onboarding_form?.pocName || "N/A"}</p>
            </div>
            {customer.onboarding_form?.pocDesignation && (
              <div>
                <Label className="text-muted-foreground text-xs">Designation / Role</Label>
                <p>{customer.onboarding_form.pocDesignation}</p>
              </div>
            )}
            <div>
              <Label className="text-muted-foreground text-xs">Email Address</Label>
              <p>{customer.contact_email || "N/A"}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">Phone Number</Label>
              <p>{customer.contact_phone || customer.onboarding_form?.pocPhone || "N/A"}</p>
            </div>
          </CardContent>
        </Card>

        {/* Business & Company Profile */}
        <Card>
          <CardHeader><CardTitle className="text-base font-semibold">Business & Company Profile</CardTitle></CardHeader>
          <CardContent className="space-y-4 text-sm">
            <div>
              <Label className="text-muted-foreground text-xs">Company / Legal Name</Label>
              <p className="font-medium">{customer.company_name || customer.onboarding_form?.businessName || "N/A"}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">Industry</Label>
              <p>{customer.industry || customer.onboarding_form?.industry || "N/A"}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">GST Number</Label>
              <p className="font-mono">{customer.onboarding_form?.gstNumber || "N/A"}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">Company Size</Label>
              <p>{customer.onboarding_form?.companySize || "N/A"}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">Website</Label>
              {customer.onboarding_form?.websiteUrl ? (
                <p>
                  <a 
                    href={customer.onboarding_form.websiteUrl.startsWith("http") ? customer.onboarding_form.websiteUrl : `https://${customer.onboarding_form.websiteUrl}`}
                    target="_blank" 
                    rel="noreferrer" 
                    className="text-blue-600 hover:underline"
                  >
                    {customer.onboarding_form.websiteUrl}
                  </a>
                </p>
              ) : (
                <p className="text-muted-foreground">N/A</p>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Agent & Use Case Requirements */}
        <Card className="md:col-span-2">
          <CardHeader><CardTitle className="text-base font-semibold">Agent & Use Case Requirements</CardTitle></CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
            <div>
              <Label className="text-muted-foreground text-xs">Call Type / Direction</Label>
              <p className="font-medium capitalize">{customer.onboarding_form?.useCaseType || "N/A"}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">Expected Monthly Call Volume</Label>
              <p>{customer.onboarding_form?.callVolume || "N/A"}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">Target Languages</Label>
              <p>{customer.onboarding_form?.languages || "N/A"}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">CRM & Software Integrations</Label>
              <p>{customer.onboarding_form?.integrations || "N/A"}</p>
            </div>
            <div className="md:col-span-2">
              <Label className="text-muted-foreground text-xs">Use Case Description & Prompt Specifications</Label>
              <p className="mt-1 whitespace-pre-wrap bg-zinc-50 dark:bg-zinc-900 p-3 rounded-md border text-zinc-800 dark:text-zinc-200">
                {customer.onboarding_form?.useCaseDescription || "No detailed description provided."}
              </p>
            </div>
            {customer.onboarding_form?.needsApiIntegration && (
              <div className="md:col-span-2 bg-blue-50/60 dark:bg-blue-950/30 border border-blue-200 rounded-md p-3">
                <Label className="text-blue-900 dark:text-blue-300 font-semibold text-xs block mb-1">Custom API Integration Requested</Label>
                <p className="text-blue-800 dark:text-blue-200">{customer.onboarding_form.apiIntegrationDetails || "Requested, details pending."}</p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Documents & System Info */}
        <Card className="md:col-span-2">
          <CardHeader><CardTitle className="text-base font-semibold">Verification Documents & System Identifiers</CardTitle></CardHeader>
          <CardContent className="space-y-4 text-sm">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <Label className="text-muted-foreground text-xs">Dograh Org ID</Label>
                <p className="font-mono">{customer.dograh_org_id || "Unprovisioned"}</p>
              </div>
              <div>
                <Label className="text-muted-foreground text-xs">Master Billing Org ID</Label>
                <p className="font-mono">{customer.billing_org_id ? `#${customer.billing_org_id}` : "Self (Master)"}</p>
              </div>
              <div>
                <Label className="text-muted-foreground text-xs">Current Tier</Label>
                <div className="mt-1"><Badge>{currentPlan}</Badge></div>
              </div>
            </div>

            {(customer.onboarding_form?.gstCertificateUrl || customer.onboarding_form?.businessRegistrationUrl) ? (
              <div className="pt-3 border-t">
                <Label className="text-muted-foreground text-xs mb-2 block font-semibold">Submitted Verification Documents</Label>
                <div className="flex flex-wrap gap-4">
                  {customer.onboarding_form?.gstCertificateUrl && (
                    <DocumentViewer title="GST Certificate" dataUrl={customer.onboarding_form.gstCertificateUrl} />
                  )}
                  {customer.onboarding_form?.businessRegistrationUrl && (
                    <DocumentViewer title="Business Registration" dataUrl={customer.onboarding_form.businessRegistrationUrl} />
                  )}
                </div>
              </div>
            ) : (
              <div className="pt-3 border-t text-muted-foreground text-xs">
                No verification documents uploaded.
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Raw Form Submission Data Viewer */}
      {customer.onboarding_form && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between py-3">
            <CardTitle className="text-sm font-semibold">Full Form Submission Data (JSON)</CardTitle>
            <Button 
              variant="ghost" 
              size="sm" 
              onClick={() => setShowRawJson(!showRawJson)}
              className="text-xs"
            >
              {showRawJson ? "Hide Raw Data" : "Show Raw Data"}
            </Button>
          </CardHeader>
          {showRawJson && (
            <CardContent className="pt-0">
              <pre className="bg-zinc-950 text-zinc-100 p-4 rounded-md text-xs font-mono overflow-auto max-h-96">
                {JSON.stringify(customer.onboarding_form, null, 2)}
              </pre>
            </CardContent>
          )}
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle>Agents & Billing Rates</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          {agents.length === 0 ? (
            <p className="text-sm text-muted-foreground">No agents found.</p>
          ) : (
            <div className="space-y-2">
              {agents.map((ag) => (
                <div key={ag.id} className="flex flex-col md:flex-row justify-between items-start md:items-center bg-background border p-4 rounded-md gap-4">
                  <div>
                    <p className="font-medium text-base">{ag.name}</p>
                    <div className="flex gap-2 mt-1">
                      <Badge variant="outline" className="text-xs">ID: {ag.id}</Badge>
                      <Badge variant="outline" className="text-xs">Org: {ag.dograh_org_id || 'N/A'}</Badge>
                      <Badge variant="secondary" className="text-xs">{ag.status}</Badge>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 bg-muted/30 p-2 rounded-md">
                    <Label className="text-xs font-semibold whitespace-nowrap">Per-Minute Rate (paise)</Label>
                    <Input 
                      type="number" 
                      placeholder={currentPlan === 'starter' ? '2500' : currentPlan === 'pro' ? '1800' : '1200'}
                      defaultValue={ag.per_minute_rate_paise ?? ""}
                      className="w-28 text-right font-mono"
                      onBlur={(e) => handleUpdateRate(ag.id, e.target.value)}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Phone Numbers</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="bg-muted/30 p-4 rounded-md space-y-4">
            <h4 className="font-medium text-sm">Assign New Number</h4>
            <div className="flex gap-4 items-end">
              <div className="space-y-2 flex-1">
                <Label>Phone Number (E.164)</Label>
                <Input placeholder="+919876543210" value={phoneNumberInput} onChange={e => setPhoneNumberInput(e.target.value)} />
              </div>
              <div className="space-y-2 flex-1">
                <Label>Plivo / Twilio ID (optional)</Label>
                <Input placeholder="e.g. 1234567890" value={plivoIdInput} onChange={e => setPlivoIdInput(e.target.value)} />
              </div>
              <Button onClick={handleAssignPhone} disabled={isAssigningPhone || !phoneNumberInput}>
                {isAssigningPhone ? "Assigning..." : "Assign Number"}
              </Button>
            </div>
          </div>
          
          <div className="pt-4 border-t">
            <h4 className="font-medium text-sm mb-2">Assigned Numbers</h4>
            {phoneNumbers.length === 0 ? (
              <p className="text-sm text-muted-foreground">No phone numbers assigned.</p>
            ) : (
              <div className="space-y-2">
                {phoneNumbers.map((pn) => (
                  <div key={pn.id} className="flex justify-between items-center bg-background border p-3 rounded-md">
                    <div>
                      <p className="font-mono">{pn.number}</p>
                      {pn.plivo_number_id && <p className="text-xs text-muted-foreground">Provider ID: {pn.plivo_number_id}</p>}
                    </div>
                    <Badge>{pn.status}</Badge>
                  </div>
                ))}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Grant Credit Modal */}
      <Dialog open={isCreditOpen} onOpenChange={setIsCreditOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Grant Manual Credit</DialogTitle>
          </DialogHeader>
          <div className="py-4 space-y-4">
            <div className="space-y-2">
              <Label>Amount (INR)</Label>
              <Input type="number" placeholder="e.g. 5000" value={creditAmount} onChange={e => setCreditAmount(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label>Description / Reason</Label>
              <Input placeholder="e.g. Apology for downtime" value={creditDesc} onChange={e => setCreditDesc(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsCreditOpen(false)}>Cancel</Button>
            <Button onClick={handleGrantCredit} disabled={!creditAmount || !creditDesc}>Grant Credit</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Deduct Balance Modal */}
      <Dialog open={isDeductOpen} onOpenChange={setIsDeductOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Deduct Balance</DialogTitle>
          </DialogHeader>
          <div className="py-4 space-y-4">
            <div className="space-y-2">
              <Label>Amount (INR)</Label>
              <Input type="number" placeholder="e.g. 5000" value={deductAmount} onChange={e => setDeductAmount(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label>Reason</Label>
              <Input placeholder="e.g. Phone number fee" value={deductReason} onChange={e => setDeductReason(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsDeductOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={handleDeduct} disabled={!deductAmount || !deductReason}>Deduct</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Plan Upgrade Modal */}
      <Dialog open={isPlanOpen} onOpenChange={setIsPlanOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Change Tier</DialogTitle>
            <DialogDescription>
              Changing the tier will immediately update the customer&apos;s Dograh config (LLM model, concurrent call limits, TTS provider) and subscription pricing.
            </DialogDescription>
          </DialogHeader>
          <div className="py-4 space-y-4">
            <div className="space-y-2">
              <Label>Current Tier</Label>
              <Badge className="block w-fit">{currentPlan}</Badge>
            </div>
            <div className="space-y-2">
              <Label>New Tier</Label>
              <Select value={newPlan} onValueChange={(v) => v && setNewPlan(v)}>
                <SelectTrigger><SelectValue placeholder="Select tier" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="starter">Starter — ₹6/min · 2 concurrent · Deepgram TTS</SelectItem>
                  <SelectItem value="growth">Growth — ₹6/min · 2 concurrent · Smallest AI TTS (Indian voices)</SelectItem>
                  <SelectItem value="pro">Pro — ₹4/min · 10 concurrent · ElevenLabs TTS</SelectItem>
                  <SelectItem value="elite">Elite — ₹12/min · 50 concurrent · ElevenLabs TTS</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <p className="text-xs text-muted-foreground">
              ⚠️ This will re-run provisioning and update their Dograh workspace immediately.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsPlanOpen(false)}>Cancel</Button>
            <Button onClick={handleUpgradePlan} disabled={!newPlan || planLoading}>
              {planLoading ? "Updating..." : "Confirm Tier Change"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
