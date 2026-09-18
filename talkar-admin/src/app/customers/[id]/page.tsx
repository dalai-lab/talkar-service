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
import { 
  ArrowLeft, 
  ExternalLink, 
  FileText, 
  Download, 
  UserCheck, 
  CreditCard, 
  MinusCircle, 
  Sliders, 
  Settings2, 
  AlertTriangle, 
  RefreshCw, 
  Ban, 
  PhoneCall, 
  Bot, 
  ShieldAlert
} from "lucide-react";

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

  // Custom Plan modal
  const [isCustomPlanOpen, setIsCustomPlanOpen] = useState(false);
  const [customPlanLoading, setCustomPlanLoading] = useState(false);
  const [customPricing, setCustomPricing] = useState({
    per_minute_rate_paise: "500",
    concurrent_call_limit: "100",
    max_call_duration_seconds: "3600",
    activation_deposit_paise: "1000000",
    llm_model: "gpt-4o",
    tts_provider: "elevenlabs",
    stt_provider: "deepgram",
    free_phone_numbers: "5",
    custom_plan_label: "Custom Enterprise",
  });

  const [phoneNumbers, setPhoneNumbers] = useState<any[]>([]);
  const [agents, setAgents] = useState<any[]>([]);
  const [subscription, setSubscription] = useState<any>(null);
  const [showRawJson, setShowRawJson] = useState(false);
  const [phoneNumberInput, setPhoneNumberInput] = useState("");
  const [plivoIdInput, setPlivoIdInput] = useState("");
  const [isAssigningPhone, setIsAssigningPhone] = useState(false);
  const [isImpersonating, setIsImpersonating] = useState(false);

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
      const subRes = await adminFetch(`/admin/customers/${id}/subscription`);
      if (subRes.ok) {
        const subData = await subRes.json();
        setSubscription(subData);
        if (subData?.plan === "custom" && subData?.custom_config) {
          const cc = subData.custom_config;
          setCustomPricing({
            per_minute_rate_paise: String(subData.per_minute_rate_paise ?? "500"),
            concurrent_call_limit: String(cc.concurrent_call_limit ?? "100"),
            max_call_duration_seconds: String(cc.max_call_duration_seconds ?? "3600"),
            activation_deposit_paise: String(cc.activation_deposit_paise ?? "1000000"),
            llm_model: cc.llm_model ?? "gpt-4o",
            tts_provider: cc.tts_provider ?? "elevenlabs",
            stt_provider: cc.stt_provider ?? "deepgram",
            free_phone_numbers: String(cc.free_phone_numbers ?? "5"),
            custom_plan_label: subData.custom_plan_label ?? "Custom Enterprise",
          });
        }
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

  const handleSetCustomPricing = async () => {
    setCustomPlanLoading(true);
    try {
      const payload = {
        per_minute_rate_paise: parseInt(customPricing.per_minute_rate_paise),
        concurrent_call_limit: parseInt(customPricing.concurrent_call_limit),
        max_call_duration_seconds: parseInt(customPricing.max_call_duration_seconds),
        activation_deposit_paise: parseInt(customPricing.activation_deposit_paise),
        llm_model: customPricing.llm_model,
        tts_provider: customPricing.tts_provider,
        stt_provider: customPricing.stt_provider,
        free_phone_numbers: parseInt(customPricing.free_phone_numbers),
        custom_plan_label: customPricing.custom_plan_label,
        trigger_reprovisioning: true,
      };
      const res = await adminFetch(`/admin/customers/${id}/set-custom-pricing`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        setIsCustomPlanOpen(false);
        alert("Custom pricing set and customer reprovisioned!");
        fetchCustomer();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`Failed to set custom pricing: ${err.detail || "Unknown error"}`);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setCustomPlanLoading(false);
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

  const handleImpersonate = async () => {
    setIsImpersonating(true);
    try {
      const res = await adminFetch(`/admin/customers/${id}/impersonate`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        const token = data.access_token;
        const refreshToken = data.refresh_token;
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

  if (loading) {
    return (
      <div className="py-24 text-center text-slate-400">
        <div className="w-6 h-6 border-2 border-[#fe6905] border-t-transparent rounded-full animate-spin mx-auto mb-3" />
        <p className="text-xs">Loading customer details...</p>
      </div>
    );
  }

  if (!customer) {
    return (
      <div className="py-24 text-center text-slate-500">
        <p className="text-sm">Customer not found.</p>
        <Button variant="link" onClick={() => router.push('/customers')} className="mt-2 text-xs">
          ← Return to directory
        </Button>
      </div>
    );
  }

  const currentPlan = customer.onboarding_form?.approved_tier || "None";

  return (
    <div className="space-y-6">
      {/* Header & Back Navigation */}
      <div>
        <Button 
          variant="ghost" 
          className="p-0 h-auto mb-3 text-xs text-slate-500 hover:text-slate-900 cursor-pointer inline-flex items-center gap-1.5"
          onClick={() => router.push('/customers')}
        >
          <ArrowLeft className="w-3.5 h-3.5" /> Back to Customer Directory
        </Button>

        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4 pb-4 border-b border-slate-200/70">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold tracking-tight text-slate-900">{customer.company_name}</h1>
              <Badge variant="outline" className="text-xs font-mono bg-white">ID #{customer.id}</Badge>
              <Badge className={customer.status === 'active' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-slate-100 text-slate-700 border-slate-200'}>
                {customer.status}
              </Badge>
            </div>
            <p className="text-xs text-slate-500 mt-1">
              Contact: {customer.contact_name} ({customer.contact_email})
            </p>
          </div>

          {/* Action Toolbar */}
          <div className="flex flex-wrap items-center gap-2">
            <Button 
              size="sm" 
              onClick={handleImpersonate} 
              disabled={isImpersonating}
              className="h-8 text-xs bg-[#fe6905] hover:bg-[#e55e04] text-white font-medium"
            >
              <UserCheck className="w-3.5 h-3.5 mr-1.5" />
              {isImpersonating ? "Connecting..." : "Impersonate"}
            </Button>
            <Button variant="outline" size="sm" onClick={() => setIsCreditOpen(true)} className="h-8 text-xs border-slate-200 bg-white hover:bg-slate-50">
              <CreditCard className="w-3.5 h-3.5 mr-1.5 text-emerald-600" /> Credit
            </Button>
            <Button variant="outline" size="sm" onClick={() => setIsDeductOpen(true)} className="h-8 text-xs border-slate-200 bg-white hover:bg-slate-50">
              <MinusCircle className="w-3.5 h-3.5 mr-1.5 text-amber-600" /> Deduct
            </Button>
            <Button variant="outline" size="sm" onClick={() => setIsPlanOpen(true)} className="h-8 text-xs border-slate-200 bg-white hover:bg-slate-50">
              <Sliders className="w-3.5 h-3.5 mr-1.5 text-blue-600" /> Tier
            </Button>
            <Button variant="outline" size="sm" onClick={() => setIsCustomPlanOpen(true)} className="h-8 text-xs border-purple-200 bg-purple-50/50 hover:bg-purple-100 text-purple-700">
              <Settings2 className="w-3.5 h-3.5 mr-1.5" />
              {subscription?.plan === "custom" ? "Edit Custom Plan" : "Set Custom Plan"}
            </Button>
            {subscription?.plan === "custom" && (
              <Button
                variant="outline"
                size="sm"
                className="h-8 text-xs border-red-200 bg-red-50/50 hover:bg-red-100 text-red-600"
                onClick={async () => {
                  if (!confirm("Remove custom plan and revert to their current standard tier?")) return;
                  const res = await adminFetch(`/admin/customers/${id}`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ tier: customer.onboarding_form?.approved_tier === "custom" ? "starter" : (customer.onboarding_form?.approved_tier || "starter") })
                  });
                  if (res.ok) { alert("Custom plan removed. Reverted to standard tier."); fetchCustomer(); }
                  else { const e = await res.json().catch(()=>({})); alert(`Failed: ${e.detail}`); }
                }}
              >Remove Custom Plan</Button>
            )}
            <Button variant="outline" size="sm" onClick={handleRetryProvisioning} className="h-8 text-xs border-slate-200 bg-white hover:bg-slate-50">
              <RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Retry Sync
            </Button>
            <Button variant="outline" size="sm" onClick={handleSuspend} className="h-8 text-xs border-red-200 text-red-600 hover:bg-red-50">
              <Ban className="w-3.5 h-3.5 mr-1.5" /> Suspend
            </Button>
          </div>
        </div>
      </div>

      {/* Upgrade Request Pending Alert */}
      {customer.onboarding_form?.tier_upgrade_requested && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <p className="font-semibold text-xs text-amber-900 flex items-center gap-1.5">
              <AlertTriangle className="w-4 h-4 text-amber-600" />
              Tier Upgrade Request Pending
            </p>
            <p className="text-xs text-amber-800 mt-1">
              Customer requested upgrade to <strong className="uppercase font-bold">{customer.onboarding_form.tier_upgrade_requested}</strong> tier.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button 
              size="sm"
              className="h-7 px-3 text-xs bg-amber-600 hover:bg-amber-700 text-white font-medium"
              onClick={() => {
                setNewPlan(customer.onboarding_form.tier_upgrade_requested);
                setIsPlanOpen(true);
              }}
            >
              Approve Upgrade
            </Button>
            <Button variant="outline" size="sm" onClick={handleDenyUpgrade} className="h-7 px-3 text-xs border-amber-200 bg-white text-amber-800 hover:bg-amber-50">
              Deny Request
            </Button>
          </div>
        </div>
      )}

      {/* Information Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Contact Information */}
        <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
          <CardHeader className="p-4 pb-2 border-b border-slate-100"><CardTitle className="text-xs font-semibold text-slate-900 uppercase tracking-wider">Contact Information</CardTitle></CardHeader>
          <CardContent className="p-4 space-y-3 text-xs">
            <div>
              <Label className="text-slate-400 text-[11px] font-medium">Primary Contact Name</Label>
              <p className="font-semibold text-slate-900 mt-0.5">{customer.contact_name || customer.onboarding_form?.pocName || "N/A"}</p>
            </div>
            {customer.onboarding_form?.pocDesignation && (
              <div>
                <Label className="text-slate-400 text-[11px] font-medium">Designation / Role</Label>
                <p className="text-slate-800 mt-0.5">{customer.onboarding_form.pocDesignation}</p>
              </div>
            )}
            <div>
              <Label className="text-slate-400 text-[11px] font-medium">Email Address</Label>
              <p className="text-slate-800 font-mono mt-0.5">{customer.contact_email || "N/A"}</p>
            </div>
            <div>
              <Label className="text-slate-400 text-[11px] font-medium">Phone Number</Label>
              <p className="text-slate-800 font-mono mt-0.5">{customer.contact_phone || customer.onboarding_form?.pocPhone || "N/A"}</p>
            </div>
          </CardContent>
        </Card>

        {/* Business Profile */}
        <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
          <CardHeader className="p-4 pb-2 border-b border-slate-100"><CardTitle className="text-xs font-semibold text-slate-900 uppercase tracking-wider">Business & Company Profile</CardTitle></CardHeader>
          <CardContent className="p-4 space-y-3 text-xs">
            <div>
              <Label className="text-slate-400 text-[11px] font-medium">Company / Legal Name</Label>
              <p className="font-semibold text-slate-900 mt-0.5">{customer.company_name || customer.onboarding_form?.businessName || "N/A"}</p>
            </div>
            <div>
              <Label className="text-slate-400 text-[11px] font-medium">Industry</Label>
              <p className="text-slate-800 mt-0.5">{customer.industry || customer.onboarding_form?.industry || "N/A"}</p>
            </div>
            <div>
              <Label className="text-slate-400 text-[11px] font-medium">GST Number</Label>
              <p className="font-mono text-slate-800 mt-0.5">{customer.onboarding_form?.gstNumber || "N/A"}</p>
            </div>
            <div>
              <Label className="text-slate-400 text-[11px] font-medium">Company Size</Label>
              <p className="text-slate-800 mt-0.5">{customer.onboarding_form?.companySize || "N/A"}</p>
            </div>
            <div>
              <Label className="text-slate-400 text-[11px] font-medium">Website</Label>
              {customer.onboarding_form?.websiteUrl ? (
                <p className="mt-0.5">
                  <a 
                    href={customer.onboarding_form.websiteUrl.startsWith("http") ? customer.onboarding_form.websiteUrl : `https://${customer.onboarding_form.websiteUrl}`}
                    target="_blank" 
                    rel="noreferrer" 
                    className="text-[#fe6905] hover:underline inline-flex items-center gap-1"
                  >
                    {customer.onboarding_form.websiteUrl} <ExternalLink className="w-3 h-3" />
                  </a>
                </p>
              ) : (
                <p className="text-slate-400 mt-0.5">N/A</p>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Agent & Use Case Requirements */}
        <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none md:col-span-2">
          <CardHeader className="p-4 pb-2 border-b border-slate-100"><CardTitle className="text-xs font-semibold text-slate-900 uppercase tracking-wider">Agent & Use Case Requirements</CardTitle></CardHeader>
          <CardContent className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
            <div>
              <Label className="text-slate-400 text-[11px] font-medium">Call Type / Direction</Label>
              <p className="font-semibold capitalize text-slate-800 mt-0.5">{customer.onboarding_form?.useCaseType || "N/A"}</p>
            </div>
            <div>
              <Label className="text-slate-400 text-[11px] font-medium">Expected Monthly Call Volume</Label>
              <p className="text-slate-800 mt-0.5">{customer.onboarding_form?.callVolume || "N/A"}</p>
            </div>
            <div>
              <Label className="text-slate-400 text-[11px] font-medium">Target Languages</Label>
              <p className="text-slate-800 mt-0.5">{customer.onboarding_form?.languages || "N/A"}</p>
            </div>
            <div>
              <Label className="text-slate-400 text-[11px] font-medium">CRM & Software Integrations</Label>
              <p className="text-slate-800 mt-0.5">{customer.onboarding_form?.integrations || "N/A"}</p>
            </div>
            <div className="md:col-span-2">
              <Label className="text-slate-400 text-[11px] font-medium">Use Case Description & Prompt Specifications</Label>
              <p className="mt-1 whitespace-pre-wrap bg-slate-50 p-3 rounded-lg border border-slate-200 text-slate-800 leading-relaxed text-xs">
                {customer.onboarding_form?.useCaseDescription || "No detailed description provided."}
              </p>
            </div>
            {customer.onboarding_form?.needsApiIntegration && (
              <div className="md:col-span-2 bg-blue-50/70 border border-blue-200 rounded-lg p-3">
                <Label className="text-blue-900 font-semibold text-[11px] block mb-1">Custom API Integration Requested</Label>
                <p className="text-blue-800 text-xs">{customer.onboarding_form.apiIntegrationDetails || "Requested, details pending."}</p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Documents & System Info */}
        <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none md:col-span-2">
          <CardHeader className="p-4 pb-2 border-b border-slate-100"><CardTitle className="text-xs font-semibold text-slate-900 uppercase tracking-wider">Verification Documents & System Identifiers</CardTitle></CardHeader>
          <CardContent className="p-4 space-y-4 text-xs">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <Label className="text-slate-400 text-[11px] font-medium">Dograh Org ID</Label>
                <p className="font-mono text-slate-800 mt-0.5 font-semibold">{customer.dograh_org_id || "Unprovisioned"}</p>
              </div>
              <div>
                <Label className="text-slate-400 text-[11px] font-medium">Master Billing Org ID</Label>
                <p className="font-mono text-slate-800 mt-0.5 font-semibold">{customer.billing_org_id ? `#${customer.billing_org_id}` : "Self (Master)"}</p>
              </div>
              <div>
                <Label className="text-slate-400 text-[11px] font-medium">Current Tier & Quotas</Label>
                <div className="mt-1 flex items-center gap-2">
                  <Badge className="bg-orange-50 text-orange-800 border-orange-200 font-medium">{currentPlan}</Badge>
                  {subscription?.plan === "custom" && subscription?.custom_plan_label && (
                    <Badge variant="outline" className="text-xs text-purple-700 border-purple-300">{subscription.custom_plan_label}</Badge>
                  )}
                </div>
                {subscription?.plan === "custom" && subscription?.custom_config && (
                  <div className="mt-2 text-xs text-slate-500 space-y-0.5">
                    <p>⚡ {subscription.per_minute_rate_paise} paise/min · {subscription.custom_config.concurrent_call_limit} concurrent calls</p>
                    <p>🤖 LLM: {subscription.custom_config.llm_model} · TTS: {subscription.custom_config.tts_provider} · STT: {subscription.custom_config.stt_provider}</p>
                  </div>
                )}
              </div>
            </div>

            {(customer.onboarding_form?.gstCertificateUrl || customer.onboarding_form?.businessRegistrationUrl) ? (
              <div className="pt-3 border-t border-slate-100">
                <Label className="text-slate-500 text-[11px] mb-2 block font-semibold">Submitted Verification Documents</Label>
                <div className="flex flex-wrap gap-3">
                  {customer.onboarding_form?.gstCertificateUrl && (
                    <DocumentViewer title="GST Certificate" dataUrl={customer.onboarding_form.gstCertificateUrl} />
                  )}
                  {customer.onboarding_form?.businessRegistrationUrl && (
                    <DocumentViewer title="Business Registration" dataUrl={customer.onboarding_form.businessRegistrationUrl} />
                  )}
                </div>
              </div>
            ) : (
              <div className="pt-3 border-t border-slate-100 text-slate-400 text-xs">
                No verification documents uploaded.
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Raw Form Submission Data Viewer */}
      {customer.onboarding_form && (
        <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
          <CardHeader className="flex flex-row items-center justify-between p-4 py-3 border-b border-slate-100">
            <CardTitle className="text-xs font-semibold text-slate-900 uppercase tracking-wider">Full Form Submission Data (JSON)</CardTitle>
            <Button 
              variant="ghost" 
              size="sm" 
              onClick={() => setShowRawJson(!showRawJson)}
              className="text-xs text-slate-500 hover:text-slate-900 h-7"
            >
              {showRawJson ? "Hide Raw Data" : "Show Raw Data"}
            </Button>
          </CardHeader>
          {showRawJson && (
            <CardContent className="p-4">
              <pre className="bg-slate-950 text-slate-100 p-4 rounded-lg text-xs font-mono overflow-auto max-h-80 border border-slate-800">
                {JSON.stringify(customer.onboarding_form, null, 2)}
              </pre>
            </CardContent>
          )}
        </Card>
      )}

      {/* Agents & Billing Rates */}
      <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
        <CardHeader className="p-4 pb-2 border-b border-slate-100"><CardTitle className="text-xs font-semibold text-slate-900 uppercase tracking-wider flex items-center gap-2"><Bot className="w-4 h-4 text-[#fe6905]" /> Agents & Billing Rates</CardTitle></CardHeader>
        <CardContent className="p-4 space-y-3">
          {agents.length === 0 ? (
            <p className="text-xs text-slate-400 py-2">No agents provisioned for this customer.</p>
          ) : (
            <div className="space-y-2.5">
              {agents.map((ag) => (
                <div key={ag.id} className="flex flex-col md:flex-row justify-between items-start md:items-center bg-slate-50/70 border border-slate-200 p-3.5 rounded-lg gap-3">
                  <div>
                    <p className="font-semibold text-sm text-slate-900">{ag.name}</p>
                    <div className="flex gap-2 mt-1">
                      <Badge variant="outline" className="text-[10px] bg-white">ID: {ag.id}</Badge>
                      <Badge variant="outline" className="text-[10px] bg-white">Org: {ag.dograh_org_id || 'N/A'}</Badge>
                      <Badge className="text-[10px] bg-emerald-50 text-emerald-700 border-emerald-200">{ag.status}</Badge>
                    </div>
                  </div>
                  <div className="flex items-center gap-2.5 bg-white p-2 rounded-lg border border-slate-200">
                    <Label className="text-xs font-medium text-slate-600 whitespace-nowrap">Per-Minute Rate (paise)</Label>
                    <Input 
                      type="number" 
                      placeholder={currentPlan === 'starter' ? '2500' : currentPlan === 'pro' ? '1800' : '1200'}
                      defaultValue={ag.per_minute_rate_paise ?? ""}
                      className="w-28 text-right font-mono text-xs h-8 bg-white border-slate-200"
                      onBlur={(e) => handleUpdateRate(ag.id, e.target.value)}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Phone Numbers */}
      <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
        <CardHeader className="p-4 pb-2 border-b border-slate-100"><CardTitle className="text-xs font-semibold text-slate-900 uppercase tracking-wider flex items-center gap-2"><PhoneCall className="w-4 h-4 text-[#fe6905]" /> Assigned Phone Numbers</CardTitle></CardHeader>
        <CardContent className="p-4 space-y-4">
          <div className="bg-slate-50/70 p-3.5 rounded-lg border border-slate-200 space-y-3">
            <h4 className="font-semibold text-xs text-slate-800">Assign New Telephony Number</h4>
            <div className="flex flex-col sm:flex-row gap-3 items-end">
              <div className="space-y-1 flex-1">
                <Label className="text-[11px] text-slate-500 font-medium">Phone Number (E.164)</Label>
                <Input placeholder="+919876543210" value={phoneNumberInput} onChange={e => setPhoneNumberInput(e.target.value)} className="h-8 text-xs bg-white border-slate-200" />
              </div>
              <div className="space-y-1 flex-1">
                <Label className="text-[11px] text-slate-500 font-medium">Plivo / Twilio ID (optional)</Label>
                <Input placeholder="e.g. 1234567890" value={plivoIdInput} onChange={e => setPlivoIdInput(e.target.value)} className="h-8 text-xs bg-white border-slate-200" />
              </div>
              <Button onClick={handleAssignPhone} disabled={isAssigningPhone || !phoneNumberInput} className="h-8 text-xs bg-[#fe6905] hover:bg-[#e55e04] text-white cursor-pointer">
                {isAssigningPhone ? "Assigning..." : "Assign Number"}
              </Button>
            </div>
          </div>
          
          <div className="pt-2">
            <h4 className="font-semibold text-xs text-slate-800 mb-2">Current Active Numbers</h4>
            {phoneNumbers.length === 0 ? (
              <p className="text-xs text-slate-400 py-1">No phone numbers assigned to this customer.</p>
            ) : (
              <div className="space-y-2">
                {phoneNumbers.map((pn) => (
                  <div key={pn.id} className="flex justify-between items-center bg-white border border-slate-200 p-3 rounded-lg text-xs">
                    <div>
                      <p className="font-mono font-semibold text-slate-900">{pn.number}</p>
                      {pn.plivo_number_id && <p className="text-[11px] text-slate-400">Provider ID: {pn.plivo_number_id}</p>}
                    </div>
                    <Badge className="bg-emerald-50 text-emerald-700 border-emerald-200 font-medium">{pn.status}</Badge>
                  </div>
                ))}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Grant Credit Modal */}
      <Dialog open={isCreditOpen} onOpenChange={setIsCreditOpen}>
        <DialogContent className="bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold border-b pb-3">Grant Manual Credit</DialogTitle>
          </DialogHeader>
          <div className="py-3 space-y-3 text-xs">
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Amount (INR)</Label>
              <Input type="number" placeholder="e.g. 5000" value={creditAmount} onChange={e => setCreditAmount(e.target.value)} className="h-9 text-xs bg-white border-slate-200" />
            </div>
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Description / Reason</Label>
              <Input placeholder="e.g. Apology for downtime" value={creditDesc} onChange={e => setCreditDesc(e.target.value)} className="h-9 text-xs bg-white border-slate-200" />
            </div>
          </div>
          <DialogFooter className="border-t pt-3">
            <Button variant="outline" size="sm" onClick={() => setIsCreditOpen(false)} className="text-xs border-slate-200">Cancel</Button>
            <Button size="sm" onClick={handleGrantCredit} disabled={!creditAmount || !creditDesc} className="text-xs bg-[#fe6905] hover:bg-[#e55e04] text-white">Grant Credit</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Deduct Balance Modal */}
      <Dialog open={isDeductOpen} onOpenChange={setIsDeductOpen}>
        <DialogContent className="bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold border-b pb-3 text-red-600">Deduct Balance</DialogTitle>
          </DialogHeader>
          <div className="py-3 space-y-3 text-xs">
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Amount (INR)</Label>
              <Input type="number" placeholder="e.g. 5000" value={deductAmount} onChange={e => setDeductAmount(e.target.value)} className="h-9 text-xs bg-white border-slate-200" />
            </div>
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Reason</Label>
              <Input placeholder="e.g. Phone number fee" value={deductReason} onChange={e => setDeductReason(e.target.value)} className="h-9 text-xs bg-white border-slate-200" />
            </div>
          </div>
          <DialogFooter className="border-t pt-3">
            <Button variant="outline" size="sm" onClick={() => setIsDeductOpen(false)} className="text-xs border-slate-200">Cancel</Button>
            <Button size="sm" variant="destructive" onClick={handleDeduct} disabled={!deductAmount || !deductReason} className="text-xs">Deduct</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Plan Upgrade Modal */}
      <Dialog open={isPlanOpen} onOpenChange={setIsPlanOpen}>
        <DialogContent className="bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold border-b pb-3">Change Customer Tier</DialogTitle>
            <DialogDescription className="text-xs text-slate-500 pt-1">
              Updating the tier will re-provision Dograh quotas (LLM, TTS, concurrent calls) immediately.
            </DialogDescription>
          </DialogHeader>
          <div className="py-3 space-y-3 text-xs">
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Current Tier</Label>
              <Badge className="bg-slate-100 text-slate-700 border-slate-200 block w-fit font-medium">{currentPlan}</Badge>
            </div>
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">New Tier</Label>
              <Select value={newPlan} onValueChange={(v) => v && setNewPlan(v)}>
                <SelectTrigger className="h-9 text-xs bg-white border-slate-200"><SelectValue placeholder="Select tier" /></SelectTrigger>
                <SelectContent className="bg-white border-slate-200">
                  <SelectItem value="starter">Starter — ₹6/min · 2 concurrent · Deepgram TTS</SelectItem>
                  <SelectItem value="growth">Growth — ₹6/min · 2 concurrent · Smallest AI TTS (Indian voices)</SelectItem>
                  <SelectItem value="pro">Pro — ₹4/min · 10 concurrent · ElevenLabs TTS</SelectItem>
                  <SelectItem value="elite">Elite — ₹12/min · 50 concurrent · ElevenLabs TTS</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <p className="text-[11px] text-amber-600 bg-amber-50 p-2 rounded border border-amber-200">
              ⚠️ This will re-run provisioning and update their Dograh workspace immediately.
            </p>
          </div>
          <DialogFooter className="border-t pt-3">
            <Button variant="outline" size="sm" onClick={() => setIsPlanOpen(false)} className="text-xs border-slate-200">Cancel</Button>
            <Button size="sm" onClick={handleUpgradePlan} disabled={!newPlan || planLoading} className="text-xs bg-[#fe6905] hover:bg-[#e55e04] text-white">
              {planLoading ? "Updating..." : "Confirm Tier Change"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Custom Plan Modal */}
      <Dialog open={isCustomPlanOpen} onOpenChange={setIsCustomPlanOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold border-b pb-3">Set Custom Enterprise Pricing</DialogTitle>
            <DialogDescription className="text-xs text-slate-500 pt-1">
              Deploy custom pricing and quota limits for high-volume enterprise customers.
            </DialogDescription>
          </DialogHeader>
          <div className="py-3 space-y-3 grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Custom Plan Label</Label>
              <Input 
                value={customPricing.custom_plan_label} 
                onChange={e => setCustomPricing({...customPricing, custom_plan_label: e.target.value})} 
                className="h-8 text-xs bg-white border-slate-200"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Call Rate (Paise/min)</Label>
              <Input 
                type="number" 
                value={customPricing.per_minute_rate_paise} 
                onChange={e => setCustomPricing({...customPricing, per_minute_rate_paise: e.target.value})} 
                className="h-8 text-xs bg-white border-slate-200"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Concurrent Call Limit</Label>
              <Input 
                type="number" 
                value={customPricing.concurrent_call_limit} 
                onChange={e => setCustomPricing({...customPricing, concurrent_call_limit: e.target.value})} 
                className="h-8 text-xs bg-white border-slate-200"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Max Call Duration (seconds)</Label>
              <Input 
                type="number" 
                value={customPricing.max_call_duration_seconds} 
                onChange={e => setCustomPricing({...customPricing, max_call_duration_seconds: e.target.value})} 
                className="h-8 text-xs bg-white border-slate-200"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Activation Deposit (Paise)</Label>
              <Input 
                type="number" 
                value={customPricing.activation_deposit_paise} 
                onChange={e => setCustomPricing({...customPricing, activation_deposit_paise: e.target.value})} 
                className="h-8 text-xs bg-white border-slate-200"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Free Phone Numbers Included</Label>
              <Input 
                type="number" 
                value={customPricing.free_phone_numbers} 
                onChange={e => setCustomPricing({...customPricing, free_phone_numbers: e.target.value})} 
                className="h-8 text-xs bg-white border-slate-200"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">LLM Model</Label>
              <Select value={customPricing.llm_model || ""} onValueChange={(v) => setCustomPricing({...customPricing, llm_model: v || ""})}>
                <SelectTrigger className="h-8 text-xs bg-white border-slate-200"><SelectValue /></SelectTrigger>
                <SelectContent className="bg-white border-slate-200">
                  <SelectItem value="gpt-4o-mini">gpt-4o-mini</SelectItem>
                  <SelectItem value="gpt-4o">gpt-4o</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">TTS Provider</Label>
              <Select value={customPricing.tts_provider || ""} onValueChange={(v) => setCustomPricing({...customPricing, tts_provider: v || ""})}>
                <SelectTrigger className="h-8 text-xs bg-white border-slate-200"><SelectValue /></SelectTrigger>
                <SelectContent className="bg-white border-slate-200">
                  <SelectItem value="elevenlabs">ElevenLabs</SelectItem>
                  <SelectItem value="deepgram">Deepgram</SelectItem>
                  <SelectItem value="smallest_ai">Smallest AI</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5 md:col-span-2">
              <Label className="text-slate-700 font-medium">STT Provider</Label>
              <Select value={customPricing.stt_provider || ""} onValueChange={(v) => setCustomPricing({...customPricing, stt_provider: v || ""})}>
                <SelectTrigger className="h-8 text-xs bg-white border-slate-200"><SelectValue /></SelectTrigger>
                <SelectContent className="bg-white border-slate-200">
                  <SelectItem value="deepgram">Deepgram</SelectItem>
                  <SelectItem value="smallest">Smallest AI</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter className="border-t pt-3">
            <Button variant="outline" size="sm" onClick={() => setIsCustomPlanOpen(false)} className="text-xs border-slate-200">Cancel</Button>
            <Button size="sm" onClick={handleSetCustomPricing} disabled={customPlanLoading} className="text-xs bg-[#fe6905] hover:bg-[#e55e04] text-white">
              {customPlanLoading ? "Deploying..." : "Deploy Custom Plan"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
