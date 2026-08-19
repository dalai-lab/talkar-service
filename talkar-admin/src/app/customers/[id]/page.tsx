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
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";


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
        <Card>
          <CardHeader><CardTitle>Contact Information</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div>
              <Label className="text-muted-foreground text-xs">Primary Contact</Label>
              <p className="font-medium">{customer.contact_name}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">Email Address</Label>
              <p>{customer.contact_email}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">Phone Number</Label>
              <p>{customer.contact_phone || customer.onboarding_form?.pocPhone || "N/A"}</p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Application Data</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div>
              <Label className="text-muted-foreground text-xs">Industry</Label>
              <p>{customer.industry || customer.onboarding_form?.industry || "N/A"}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">Use Case</Label>
              <p>{customer.onboarding_form?.useCaseType || "N/A"}</p>
              <p className="text-sm text-muted-foreground">{customer.onboarding_form?.useCaseDescription || ""}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">Dograh Org ID</Label>
              <p>{customer.dograh_org_id || "Unprovisioned"}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">Current Tier</Label>
              <div className="flex gap-2 items-center mt-1">
                <Badge>{currentPlan}</Badge>
              </div>
            </div>
            {(customer.onboarding_form?.gstCertificateUrl || customer.onboarding_form?.businessRegistrationUrl) && (
              <div className="pt-2 border-t mt-4">
                <Label className="text-muted-foreground text-xs mb-2 block">Submitted Documents</Label>
                <div className="flex flex-col gap-2">
                  {customer.onboarding_form?.gstCertificateUrl && (
                    <a href={customer.onboarding_form.gstCertificateUrl} target="_blank" rel="noreferrer" className="text-sm text-blue-600 hover:underline flex items-center gap-1">
                      📄 GST Certificate
                    </a>
                  )}
                  {customer.onboarding_form?.businessRegistrationUrl && (
                    <a href={customer.onboarding_form.businessRegistrationUrl} target="_blank" rel="noreferrer" className="text-sm text-blue-600 hover:underline flex items-center gap-1">
                      📄 Business Registration
                    </a>
                  )}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

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
                  <SelectItem value="starter">Starter — ₹25/min · 2 concurrent calls · Deepgram</SelectItem>
                  <SelectItem value="pro">Pro — ₹18/min · 10 concurrent calls · ElevenLabs</SelectItem>
                  <SelectItem value="elite">Elite — ₹12/min · Unlimited calls · ElevenLabs</SelectItem>
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
