"use client";
import { adminFetch } from "@/lib/api";

import React, { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";


export default function CustomerDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;
  
  const [customer, setCustomer] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  // Modals state
  const [isCreditOpen, setIsCreditOpen] = useState(false);
  const [creditAmount, setCreditAmount] = useState("");
  const [creditDesc, setCreditDesc] = useState("");

  useEffect(() => {
    fetchCustomer();
  }, [id]);

  const fetchCustomer = async () => {
    try {
      const res = await adminFetch(`/admin/customers/${id}`);
      if (res.ok) {
        const data = await res.json();
        setCustomer(data);
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
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleSuspend = async () => {
    if (!confirm("Are you sure you want to suspend this account?")) return;
    try {
      const res = await adminFetch(`/admin/customers/${id}/suspend`, { method: "POST" });
      if (res.ok) {
        fetchCustomer();
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleRetryProvisioning = async () => {
    try {
      const res = await adminFetch(`/admin/customers/${id}/provision/retry`, { method: "POST" });
      if (res.ok) {
        alert("Provisioning queued for retry!");
      }
    } catch (e) {
      console.error(e);
    }
  };

  if (loading) return <div>Loading...</div>;
  if (!customer) return <div>Customer not found.</div>;

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
          <Button variant="outline" onClick={() => setIsCreditOpen(true)}>Grant Manual Credit</Button>
          <Button variant="outline" onClick={handleRetryProvisioning}>Retry Provisioning</Button>
          <Button variant="destructive" onClick={handleSuspend}>Suspend Account</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Contact Information</CardTitle>
          </CardHeader>
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
              <p>{customer.contact_phone || "N/A"}</p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Application Data</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <Label className="text-muted-foreground text-xs">Industry</Label>
              <p>{customer.industry || "N/A"}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">Use Case</Label>
              <p>{customer.onboarding_form?.useCaseType || "N/A"}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">Dograh Integration</Label>
              <p>Org ID: {customer.dograh_org_id || "Unprovisioned"}</p>
            </div>
            <div>
              <Label className="text-muted-foreground text-xs">Plan</Label>
              <div className="flex gap-2 mt-1">
                <Badge>{customer.plan || "None"}</Badge>
                <Button variant="outline" size="sm" onClick={async () => {
                  const newPlan = prompt("Enter new plan (starter, pro, enterprise):");
                  if (newPlan) {
                    await adminFetch(`/admin/customers/${id}`, {
                      method: "PATCH",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ plan: newPlan })
                    });
                    fetchCustomer();
                  }
                }}>Update Plan</Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Dialog open={isCreditOpen} onOpenChange={setIsCreditOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Grant Manual Credit</DialogTitle>
          </DialogHeader>
          <div className="py-4 space-y-4">
            <div className="space-y-2">
              <Label>Amount (INR)</Label>
              <Input type="number" placeholder="e.g. 5000" value={creditAmount} onChange={(e) => setCreditAmount(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label>Description / Reason</Label>
              <Input placeholder="e.g. Apology for downtime" value={creditDesc} onChange={(e) => setCreditDesc(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsCreditOpen(false)}>Cancel</Button>
            <Button onClick={handleGrantCredit} disabled={!creditAmount || !creditDesc}>Grant Credit</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
