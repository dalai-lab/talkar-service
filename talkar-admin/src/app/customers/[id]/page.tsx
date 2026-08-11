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

  // Plan upgrade modal
  const [isPlanOpen, setIsPlanOpen] = useState(false);
  const [newPlan, setNewPlan] = useState("");
  const [planLoading, setPlanLoading] = useState(false);

  useEffect(() => {
    fetchCustomer();
  }, [id]);

  const fetchCustomer = async () => {
    try {
      const res = await adminFetch(`/admin/customers/${id}`);
      if (res.ok) {
        const data = await res.json();
        setCustomer(data);
        setNewPlan(data.onboarding_form?.approved_plan || "");
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

  const handleUpgradePlan = async () => {
    if (!newPlan) return;
    setPlanLoading(true);
    try {
      const res = await adminFetch(`/admin/customers/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan: newPlan })
      });
      if (res.ok) {
        setIsPlanOpen(false);
        alert(`Plan updated to "${newPlan}" and Dograh config has been re-provisioned!`);
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
      const res = await adminFetch(`/admin/customers/${id}/deny-upgrade`, { method: "POST" });
      if (res.ok) fetchCustomer();
    } catch (e) {
      console.error(e);
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

  if (loading) return <div>Loading...</div>;
  if (!customer) return <div>Customer not found.</div>;

  const currentPlan = customer.onboarding_form?.approved_plan || "None";
  const serviceType = customer.onboarding_form?.wantsBuildForMe === false ? "Self-Serve" : "Managed (Build for me)";

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
            <Badge variant="secondary">{serviceType}</Badge>
          </div>
        </div>
        <div className="space-x-2">
          <Button variant="outline" onClick={() => setIsCreditOpen(true)}>Grant Manual Credit</Button>
          <Button variant="outline" onClick={() => setIsPlanOpen(true)}>Upgrade / Change Plan</Button>
          <Button variant="outline" onClick={handleRetryProvisioning}>Retry Provisioning</Button>
          <Button variant="destructive" onClick={handleSuspend}>Suspend Account</Button>
        </div>
      </div>

      {customer.onboarding_form?.plan_upgrade_requested && (
        <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-4">
          <p className="font-medium">⚠️ Upgrade Request Pending</p>
          <p className="text-sm text-muted-foreground mt-1">
            Customer requested upgrade to <strong>{customer.onboarding_form.plan_upgrade_requested}</strong>
            {" "}on {new Date(customer.onboarding_form.plan_upgrade_requested_at).toLocaleDateString()}.
          </p>
          <div className="mt-3 flex gap-2">
            <Button 
              onClick={() => {
                setNewPlan(customer.onboarding_form.plan_upgrade_requested);
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
              <Label className="text-muted-foreground text-xs">Current Plan</Label>
              <div className="flex gap-2 items-center mt-1">
                <Badge>{currentPlan}</Badge>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

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

      {/* Plan Upgrade Modal */}
      <Dialog open={isPlanOpen} onOpenChange={setIsPlanOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Upgrade / Change Plan</DialogTitle>
            <DialogDescription>
              Changing the plan will immediately update the customer&apos;s Dograh config (LLM model, concurrent call limits, TTS provider) and subscription pricing.
            </DialogDescription>
          </DialogHeader>
          <div className="py-4 space-y-4">
            <div className="space-y-2">
              <Label>Current Plan</Label>
              <Badge className="block w-fit">{currentPlan}</Badge>
            </div>
            <div className="space-y-2">
              <Label>New Plan</Label>
              <Select value={newPlan} onValueChange={(v) => v && setNewPlan(v)}>
                <SelectTrigger><SelectValue placeholder="Select plan" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="starter">Starter — ₹5,000/mo · ₹18/min · 2 concurrent calls</SelectItem>
                  <SelectItem value="pro">Pro — ₹15,000/mo · ₹14/min · 10 concurrent calls</SelectItem>
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
              {planLoading ? "Updating..." : "Confirm Plan Change"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
