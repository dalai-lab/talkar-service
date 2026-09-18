"use client";

import React, { useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { 
  BookOpen, 
  Search, 
  Layers, 
  Workflow, 
  LayoutGrid, 
  Sliders, 
  DollarSign, 
  HelpCircle, 
  UserCheck, 
  CreditCard, 
  MinusCircle, 
  Settings2, 
  RefreshCw, 
  Ban, 
  ShieldAlert, 
  Bell, 
  PhoneCall, 
  Bot, 
  CheckCircle2, 
  AlertTriangle, 
  ArrowRight,
  ExternalLink,
  ChevronRight,
  Radio,
  Building,
  ListTodo,
  TrendingUp,
  Wallet,
  Activity,
  Megaphone,
  Key
} from "lucide-react";

export default function AdminHelpPage() {
  const [searchQuery, setSearchQuery] = useState("");
  const [activeTab, setActiveTab] = useState<
    "architecture" | "onboarding" | "screens" | "buttons" | "billing" | "troubleshooting"
  >("onboarding");

  const tabs = [
    { id: "onboarding", label: "Client Onboarding (5 Steps)", icon: Workflow },
    { id: "buttons", label: "Master Button Encyclopedia", icon: Sliders },
    { id: "screens", label: "Admin Screens Directory", icon: LayoutGrid },
    { id: "architecture", label: "Architecture & Systems", icon: Layers },
    { id: "billing", label: "Billing & Unit Economics", icon: DollarSign },
    { id: "troubleshooting", label: "Troubleshooting & FAQs", icon: HelpCircle },
  ] as const;

  const buttonDirectory = [
    {
      name: "Impersonate",
      screen: "Customer Detail > Account Actions",
      icon: UserCheck,
      color: "text-[#fe6905]",
      badge: "Access & Support",
      description: "Generates a temporary administrative superuser session in Dograh and opens the customer's portal in a new browser tab.",
      sideEffects: "Calls POST /admin/customers/{id}/impersonate -> issues superuser JWT without requiring customer's password. You see exactly what the client sees.",
      tips: "Use this to configure voice prompts, test agent responses, or diagnose client-reported UI errors."
    },
    {
      name: "Test Notification",
      screen: "Customer Detail > Account Actions",
      icon: Bell,
      color: "text-amber-600",
      badge: "Access & Support",
      description: "Dispatches a manual test alert (in-app notification bell and/or email) directly to the customer's contact email.",
      sideEffects: "Calls POST /admin/customers/{id}/test-notification -> inserts notification row and invokes Resend/SMTP email service.",
      tips: "Ideal for verifying that the client's email delivery and in-app notification bell are functioning properly."
    },
    {
      name: "Credit (+ Balance)",
      screen: "Customer Detail > Account Actions",
      icon: CreditCard,
      color: "text-emerald-600",
      badge: "Wallet & Balance",
      description: "Manually adds funds in INR to the customer's pre-paid wallet balance.",
      sideEffects: "Calls POST /admin/customers/{id}/credit -> multiplies input Rupees by 100 into paise, updates Wallet.balance_paise, and writes an audit record in WalletTransaction.",
      tips: "Use for promotional credits, wire transfers, offline payments, or goodwill credits."
    },
    {
      name: "Deduct (- Balance)",
      screen: "Customer Detail > Account Actions",
      icon: MinusCircle,
      color: "text-amber-600",
      badge: "Wallet & Balance",
      description: "Debits a specified INR amount from the customer's wallet balance.",
      sideEffects: "Calls POST /admin/customers/{id}/deduct -> subtracts amount in paise, requires a mandatory reason, and creates a debit audit record.",
      tips: "Used for manual adjustments, custom telephony setup fees, or clawing back erroneous credits."
    },
    {
      name: "Tier (Standard)",
      screen: "Customer Detail > Account Actions",
      icon: Sliders,
      color: "text-blue-600",
      badge: "Plan & Pricing",
      description: "Switches the customer between standard platform tiers: Starter, Growth, Pro, or Elite.",
      sideEffects: "Calls PATCH /admin/customers/{id} -> updates Subscription.plan and approved_tier, triggering re-provisioning of rate limits.",
      tips: "Standard tiers have predefined per-minute rates (₹5/min Starter, ₹7/min Growth, etc.). For custom pricing, use 'Set Custom Plan' instead."
    },
    {
      name: "Set / Edit Custom Plan",
      screen: "Customer Detail > Account Actions",
      icon: Settings2,
      color: "text-purple-600",
      badge: "Plan & Pricing",
      description: "Configures enterprise-grade pricing overrides: per-minute rate (in ₹), concurrent call limit, max call duration, activation deposit, model selection (LLM, TTS, STT), and free phone numbers.",
      sideEffects: "Calls POST /admin/customers/{id}/set-custom-pricing -> updates Subscription.plan to 'custom', sets custom_config JSON, and automatically cascades configuration to all linked sub-organizations.",
      tips: "Rates are entered in Rupees (e.g., ₹5.50/min) and converted automatically to paise (550 paise) on submit."
    },
    {
      name: "Remove Custom Plan",
      screen: "Customer Detail > Account Actions",
      icon: Ban,
      color: "text-red-600",
      badge: "Plan & Pricing",
      description: "Deletes the custom pricing override and reverts the customer back to standard tier pricing.",
      sideEffects: "Calls PATCH /admin/customers/{id} -> resets Subscription.plan to 'starter' or previously approved tier and removes custom quotas.",
      tips: "Only visible when the customer is currently on a custom plan."
    },
    {
      name: "Retry Sync",
      screen: "Customer Detail > Account Actions",
      icon: RefreshCw,
      color: "text-slate-600",
      badge: "Status & Sync",
      description: "Forces a fresh background synchronization with Dograh and telephony carriers.",
      sideEffects: "Calls POST /admin/customers/{id}/provision/retry -> queues a background worker to ensure the Dograh organization, agent prompts, and carrier bindings exist.",
      tips: "Click this if a customer's Dograh Org ID says 'Unprovisioned' or if network timeouts occurred during approval."
    },
    {
      name: "Suspend",
      screen: "Customer Detail > Account Actions",
      icon: Ban,
      color: "text-rose-600",
      badge: "Status & Sync",
      description: "Suspends account access and immediately blocks all inbound and outbound telephone calls.",
      sideEffects: "Calls POST /admin/customers/{id}/suspend -> sets Customer.status = 'suspended', records suspension reason & custom message, and zeroes out Dograh CONCURRENT_CALL_LIMIT = 0 so no calls can connect.",
      tips: "A red suspended banner will appear in the customer portal explaining why the account was halted."
    },
    {
      name: "Unsuspend",
      screen: "Customer Detail > Account Actions",
      icon: ShieldAlert,
      color: "text-emerald-600",
      badge: "Status & Sync",
      description: "Lifts the suspension, restores active account status, and reactivates full call concurrency in Dograh.",
      sideEffects: "Calls POST /admin/customers/{id}/unsuspend -> sets status = 'active', clears suspension reason, and restores Dograh CONCURRENT_CALL_LIMIT back to their tier limit.",
      tips: "Immediately restores calling capabilities without requiring the client to wait or make a new deposit."
    },
    {
      name: "Review & Approve",
      screen: "Applications Queue",
      icon: CheckCircle2,
      color: "text-[#fe6905]",
      badge: "Applications",
      description: "Approves an incoming onboarding application and assigns the starting tier.",
      sideEffects: "If integration fee = ₹0, transitions customer directly to 'agent_building' and moves them into the Build Queue. If fee > ₹0, sets status to 'approved' and generates a Razorpay payment order link for the client.",
      tips: "Agent building is free by default (₹0). Only specify a custom fee if the client asked for bespoke CRM webhook pipelines."
    },
    {
      name: "Request Info",
      screen: "Applications Queue",
      icon: HelpCircle,
      color: "text-sky-600",
      badge: "Applications",
      description: "Sends a request back to the applicant for additional clarifications or missing documents.",
      sideEffects: "Calls POST /admin/applications/{id}/request-info -> updates status to 'info_requested' and emails the message to the customer.",
      tips: "The customer can log into their portal and resubmit their brief."
    },
    {
      name: "Reject Application",
      screen: "Applications Queue",
      icon: Ban,
      color: "text-red-600",
      badge: "Applications",
      description: "Rejects the onboarding application with a mandatory explanation.",
      sideEffects: "Calls POST /admin/applications/{id}/reject -> sets status to 'rejected', records rejection reason, and notifies the applicant with a cool-off reapplication period (default: 30 days).",
      tips: "Rejected applicants are blocked from generating call traffic."
    },
    {
      name: "Mark Build Ready",
      screen: "Agent Build Queue",
      icon: Bot,
      color: "text-[#fe6905]",
      badge: "Build Queue",
      description: "Finalizes voice agent setup and moves customer from 'agent_building' to 'active'.",
      sideEffects: "Calls PATCH /admin/build-queue/{id}/ready -> allows overriding agent name and assigned phone numbers, sets status = 'active', and dispatches a 'Your Agent is Ready' email notification.",
      tips: "Only click this after you have verified the agent's prompts and telephony numbers in the Dograh engine."
    },
    {
      name: "Assign Number",
      screen: "Customer Detail > Assigned Numbers",
      icon: PhoneCall,
      color: "text-[#fe6905]",
      badge: "Telephony",
      description: "Records a carrier telephony phone number (in E.164 format) assigned to this customer.",
      sideEffects: "Calls POST /admin/customers/{id}/assign-phone-number -> saves the record in customer_phone_numbers table.",
      tips: "Numbers are not purchased by this button. You must purchase the number in your Plivo/Twilio account first, then record it here."
    },
    {
      name: "Save All CRM Links",
      screen: "Customer Detail > CRM Integrations",
      icon: ExternalLink,
      color: "text-[#fe6905]",
      badge: "Integrations",
      description: "Configures external CRM and tool links (e.g. HubSpot, Zoho, LeadSquared) for this client.",
      sideEffects: "Calls PATCH /admin/customers/{id}/crm-links -> persists the array into Customer.crm_links.",
      tips: "These links appear inside the customer portal as an authentic collapsible navigation menu!"
    }
  ];

  const filteredButtons = buttonDirectory.filter(b => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      b.name.toLowerCase().includes(q) ||
      b.description.toLowerCase().includes(q) ||
      b.screen.toLowerCase().includes(q) ||
      b.badge.toLowerCase().includes(q)
    );
  });

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-16">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-4 border-b border-slate-200/70">
        <div>
          <div className="flex items-center gap-2.5">
            <div className="h-8 w-8 rounded-lg bg-gradient-to-br from-[#fe6905] to-[#e55e04] flex items-center justify-center text-white shadow-sm">
              <BookOpen className="w-4 h-4" />
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-900">
              Admin Operations Guide & Help Center
            </h1>
            <Badge className="bg-orange-50 text-orange-800 border-orange-200 text-xs font-semibold">
              Talkar v2.4
            </Badge>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            Comprehensive manual covering client onboarding, administrative controls, financial billing, and troubleshooting.
          </p>
        </div>

        {/* Quick Search Bar */}
        <div className="relative w-full sm:w-80">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400 pointer-events-none" />
          <Input 
            placeholder="Search buttons, steps, or topics..." 
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9 h-9 text-xs bg-white border-slate-200 text-slate-800 placeholder:text-slate-400 shadow-sm"
          />
        </div>
      </div>

      {/* Category Tabs */}
      <div className="flex flex-wrap items-center gap-1.5 p-1.5 bg-slate-100/90 rounded-xl border border-slate-200/70">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs font-medium transition-all cursor-pointer ${
                isActive
                  ? "bg-white text-slate-900 shadow-sm font-semibold border border-slate-200/60"
                  : "text-slate-600 hover:text-slate-900 hover:bg-white/60"
              }`}
            >
              <Icon className={`w-3.5 h-3.5 ${isActive ? "text-[#fe6905]" : "text-slate-400"}`} />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>

      {/* TAB CONTENT: 1. CLIENT ONBOARDING (5 STEPS) */}
      {activeTab === "onboarding" && (
        <div className="space-y-6">
          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-5 pb-3 border-b border-slate-100">
              <CardTitle className="text-sm font-bold text-slate-900 uppercase tracking-wider flex items-center gap-2">
                <Workflow className="w-4 h-4 text-[#fe6905]" /> The End-to-End Client Onboarding Lifecycle
              </CardTitle>
              <p className="text-xs text-slate-500 font-normal mt-0.5">
                How an applicant moves from initial website brief submission to fully active live AI voice agents.
              </p>
            </CardHeader>
            <CardContent className="p-5 space-y-6 text-xs">
              {/* Step 1 */}
              <div className="flex gap-4 items-start">
                <div className="h-7 w-7 rounded-full bg-slate-900 text-white flex items-center justify-center font-bold text-xs shrink-0 mt-0.5">
                  1
                </div>
                <div className="space-y-1.5 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-slate-900 text-sm">Application Brief Submission</h3>
                    <Badge variant="outline" className="text-[10px] bg-slate-50 text-slate-700">Status: under_review / pending_approval</Badge>
                  </div>
                  <p className="text-slate-600 leading-relaxed">
                    The customer signs up on the Talkar website and completes their onboarding brief. They specify their company legal name, contact person, primary use case (Inbound / Outbound / Both), target languages (e.g. Hindi, English), estimated monthly call volume, CRM integrations, and prompt specifications. They may also upload their GST Certificate and Business Registration documents.
                  </p>
                  <div className="bg-slate-50 p-2.5 rounded-lg border border-slate-200/70 text-slate-600 text-[11px]">
                    📍 <strong>Where in Admin:</strong> Applications Queue (<code className="text-[#fe6905]">/applications</code>).
                  </div>
                </div>
              </div>

              {/* Step 2 */}
              <div className="flex gap-4 items-start">
                <div className="h-7 w-7 rounded-full bg-slate-900 text-white flex items-center justify-center font-bold text-xs shrink-0 mt-0.5">
                  2
                </div>
                <div className="space-y-1.5 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-slate-900 text-sm">Admin Review & Document Verification</h3>
                    <Badge variant="outline" className="text-[10px] bg-amber-50 text-amber-800">Action: View Details / Request Info</Badge>
                  </div>
                  <p className="text-slate-600 leading-relaxed">
                    Click <strong>View Details</strong> to review the submitted prompt, target languages, and open uploaded PDF/image documents in the built-in document viewer. If documents are blurry or the use case is unclear, click <strong>Request Info</strong> to trigger an email to the client asking for clarifications.
                  </p>
                </div>
              </div>

              {/* Step 3 */}
              <div className="flex gap-4 items-start">
                <div className="h-7 w-7 rounded-full bg-[#fe6905] text-white flex items-center justify-center font-bold text-xs shrink-0 mt-0.5">
                  3
                </div>
                <div className="space-y-1.5 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-slate-900 text-sm">Approval & Setup Fee Decision</h3>
                    <Badge variant="outline" className="text-[10px] bg-purple-50 text-purple-700">Action: Review & Approve</Badge>
                  </div>
                  <p className="text-slate-600 leading-relaxed">
                    When you click <strong>Review & Approve</strong>, you select their approved tier (Starter, Pro, Elite) and decide if a custom integration fee applies:
                  </p>
                  <ul className="list-disc pl-5 space-y-1 text-slate-600">
                    <li><strong>Standard Build (₹0 fee):</strong> Agent build is completely free. The customer status immediately moves to <code className="bg-slate-100 px-1 py-0.5 rounded">agent_building</code> and enters the Build Queue.</li>
                    <li><strong>Custom API Integration Fee (e.g. ₹5,000):</strong> If the client requested custom CRM webhooks or database integrations, enter the fee. A Razorpay order is automatically generated and sent to their dashboard. Once they pay, they move into the Build Queue.</li>
                  </ul>
                </div>
              </div>

              {/* Step 4 */}
              <div className="flex gap-4 items-start">
                <div className="h-7 w-7 rounded-full bg-slate-900 text-white flex items-center justify-center font-bold text-xs shrink-0 mt-0.5">
                  4
                </div>
                <div className="space-y-1.5 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-slate-900 text-sm">Engineering Build & Telephony Setup</h3>
                    <Badge variant="outline" className="text-[10px] bg-blue-50 text-blue-700">Status: agent_building</Badge>
                  </div>
                  <p className="text-slate-600 leading-relaxed">
                    The customer is now in the <strong>Agent Build Queue</strong> (<code className="text-[#fe6905]">/build-queue</code>). The engineering team provisions the AI agent prompt inside Dograh, selects the appropriate voice models, and purchases/configures their telephony numbers in Plivo.
                  </p>
                </div>
              </div>

              {/* Step 5 */}
              <div className="flex gap-4 items-start">
                <div className="h-7 w-7 rounded-full bg-emerald-600 text-white flex items-center justify-center font-bold text-xs shrink-0 mt-0.5">
                  5
                </div>
                <div className="space-y-1.5 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-slate-900 text-sm">Mark Build Ready & Customer Go-Live</h3>
                    <Badge className="text-[10px] bg-emerald-50 text-emerald-700 border-emerald-200">Status: active</Badge>
                  </div>
                  <p className="text-slate-600 leading-relaxed">
                    In the Build Queue, click <strong>Mark Build Ready</strong>. You can customize the completion message and override the agent name or phone number. When submitted, the account transitions to <strong>Active</strong>, the Dograh concurrent call limits are enabled, and an automated email is dispatched to the client welcoming them to start making calls!
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* TAB CONTENT: 2. MASTER BUTTON ENCYCLOPEDIA */}
      {activeTab === "buttons" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between pb-1">
            <h2 className="text-sm font-bold text-slate-900 uppercase tracking-wider flex items-center gap-2">
              <Sliders className="w-4 h-4 text-[#fe6905]" /> Master Action & Button Directory
            </h2>
            <span className="text-xs text-slate-400">
              Showing {filteredButtons.length} of {buttonDirectory.length} controls
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
            {filteredButtons.map((btn, idx) => {
              const Icon = btn.icon;
              return (
                <Card key={idx} className="bg-white border border-slate-200/80 rounded-xl shadow-none hover:border-slate-300 transition-all flex flex-col justify-between">
                  <CardContent className="p-4 space-y-2.5">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <div className="h-7 w-7 rounded-lg bg-slate-50 border border-slate-200/70 flex items-center justify-center shrink-0">
                          <Icon className={`w-4 h-4 ${btn.color}`} />
                        </div>
                        <div>
                          <h3 className="font-bold text-slate-900 text-xs">{btn.name}</h3>
                          <p className="text-[10px] text-slate-400">{btn.screen}</p>
                        </div>
                      </div>
                      <Badge variant="outline" className="text-[10px] bg-slate-50 font-medium">
                        {btn.badge}
                      </Badge>
                    </div>

                    <p className="text-xs text-slate-700 leading-relaxed">
                      {btn.description}
                    </p>

                    <div className="bg-slate-50/80 p-2.5 rounded-lg border border-slate-200/70 space-y-1 text-[11px]">
                      <p className="text-slate-800">
                        <strong className="text-slate-900">⚡ API & Side Effects:</strong> {btn.sideEffects}
                      </p>
                      <p className="text-slate-500 pt-0.5">
                        💡 <strong>Pro-tip:</strong> {btn.tips}
                      </p>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* TAB CONTENT: 3. ADMIN SCREENS DIRECTORY */}
      {activeTab === "screens" && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-4 pb-2 border-b border-slate-100 flex flex-row items-center gap-2">
              <ListTodo className="w-4 h-4 text-[#fe6905]" />
              <CardTitle className="text-xs font-bold text-slate-900 uppercase tracking-wider">1. Applications Queue (/applications)</CardTitle>
            </CardHeader>
            <CardContent className="p-4 text-xs space-y-2 text-slate-600">
              <p>Where prospective customers appear after completing their brief on the website.</p>
              <p><strong>Primary Tasks:</strong> Review company details, examine uploaded GST & registration documents, request missing information, reject fraudulent submissions, or approve into build queue.</p>
            </CardContent>
          </Card>

          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-4 pb-2 border-b border-slate-100 flex flex-row items-center gap-2">
              <Building className="w-4 h-4 text-[#fe6905]" />
              <CardTitle className="text-xs font-bold text-slate-900 uppercase tracking-wider">2. Agent Build Queue (/build-queue)</CardTitle>
            </CardHeader>
            <CardContent className="p-4 text-xs space-y-2 text-slate-600">
              <p>Holds customers whose setup/deposit requirements are cleared and are currently undergoing agent prompt engineering and telephony number assignment.</p>
              <p><strong>Primary Tasks:</strong> Inspect engineering briefs, prepare prompt configurations in Dograh, assign carrier numbers, and trigger the "Mark Build Ready" go-live workflow.</p>
            </CardContent>
          </Card>

          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-4 pb-2 border-b border-slate-100 flex flex-row items-center gap-2">
              <UserCheck className="w-4 h-4 text-[#fe6905]" />
              <CardTitle className="text-xs font-bold text-slate-900 uppercase tracking-wider">3. Customer Directory & Detail (/customers)</CardTitle>
            </CardHeader>
            <CardContent className="p-4 text-xs space-y-2 text-slate-600">
              <p>The central hub for managing all provisioned and active customer organizations.</p>
              <p><strong>Primary Tasks:</strong> Search by company or email, inspect master vs sub-organization relationships, issue manual credits or debits, configure custom per-minute pricing, suspend/unsuspend, and link CRM portals.</p>
            </CardContent>
          </Card>

          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-4 pb-2 border-b border-slate-100 flex flex-row items-center gap-2">
              <PhoneCall className="w-4 h-4 text-[#fe6905]" />
              <CardTitle className="text-xs font-bold text-slate-900 uppercase tracking-wider">4. Phone Number Requests (/phone-number-requests)</CardTitle>
            </CardHeader>
            <CardContent className="p-4 text-xs space-y-2 text-slate-600">
              <p>Lists requests submitted by clients asking for additional phone lines, toll-free DIDs, or extra concurrency channels.</p>
              <p><strong>Primary Tasks:</strong> Filter pending requests, evaluate requested volume and use case, purchase numbers on Plivo/Twilio, and approve by supplying the new numbers.</p>
            </CardContent>
          </Card>

          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-4 pb-2 border-b border-slate-100 flex flex-row items-center gap-2">
              <Wallet className="w-4 h-4 text-[#fe6905]" />
              <CardTitle className="text-xs font-bold text-slate-900 uppercase tracking-wider">5. Platform Wallet Overview (/wallet-overview)</CardTitle>
            </CardHeader>
            <CardContent className="p-4 text-xs space-y-2 text-slate-600">
              <p>Monitors platform-wide pre-paid wallet floats and liquidity.</p>
              <p><strong>Primary Tasks:</strong> Track total floating customer deposits, review low-balance warnings (accounts below ₹100), and verify whether auto-recharge is enabled for at-risk accounts.</p>
            </CardContent>
          </Card>

          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-4 pb-2 border-b border-slate-100 flex flex-row items-center gap-2">
              <Activity className="w-4 h-4 text-[#fe6905]" />
              <CardTitle className="text-xs font-bold text-slate-900 uppercase tracking-wider">6. Live Call Activity (/call-activity)</CardTitle>
            </CardHeader>
            <CardContent className="p-4 text-xs space-y-2 text-slate-600">
              <p>Real-time telemetry measuring active voice streams connected across Dograh cluster gateways.</p>
              <p><strong>Primary Tasks:</strong> Polls the cluster gateway every 5 seconds to provide immediate visibility into network traffic spikes or telephony anomalies.</p>
            </CardContent>
          </Card>

          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-4 pb-2 border-b border-slate-100 flex flex-row items-center gap-2">
              <TrendingUp className="w-4 h-4 text-[#fe6905]" />
              <CardTitle className="text-xs font-bold text-slate-900 uppercase tracking-wider">7. Profitability & Margins (/profitability)</CardTitle>
            </CardHeader>
            <CardContent className="p-4 text-xs space-y-2 text-slate-600">
              <p>Granular financial unit economics analysis down to the minute.</p>
              <p><strong>Primary Tasks:</strong> Compare billed revenue against underlying provider costs (Plivo carrier + Deepgram STT + ElevenLabs/Smallest TTS + LLM tokens) to ensure healthy gross margins (&gt;50%).</p>
            </CardContent>
          </Card>

          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-4 pb-2 border-b border-slate-100 flex flex-row items-center gap-2">
              <Megaphone className="w-4 h-4 text-[#fe6905]" />
              <CardTitle className="text-xs font-bold text-slate-900 uppercase tracking-wider">8. System Announcements (/announcements)</CardTitle>
            </CardHeader>
            <CardContent className="p-4 text-xs space-y-2 text-slate-600">
              <p>Broadcast critical operational messages, feature rollouts, and scheduled maintenance windows.</p>
              <p><strong>Primary Tasks:</strong> Compose rich notifications, choose delivery channels (In-App notifications, Email broadcasts, or both), and schedule future dispatch times.</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* TAB CONTENT: 4. ARCHITECTURE & SYSTEMS */}
      {activeTab === "architecture" && (
        <div className="space-y-6">
          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-5 pb-3 border-b border-slate-100">
              <CardTitle className="text-sm font-bold text-slate-900 uppercase tracking-wider flex items-center gap-2">
                <Layers className="w-4 h-4 text-[#fe6905]" /> Talkar Internal System Architecture
              </CardTitle>
            </CardHeader>
            <CardContent className="p-5 space-y-4 text-xs text-slate-700 leading-relaxed">
              <p>
                Talkar is engineered as a decoupled high-concurrency voice AI orchestration layer on top of Dograh. The admin panel communicates securely via JWT-authenticated REST APIs to the backend service.
              </p>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-3.5 pt-2">
                <div className="bg-slate-50 p-3.5 rounded-lg border border-slate-200 space-y-1.5">
                  <h4 className="font-bold text-slate-900 text-xs flex items-center gap-1.5">
                    <Radio className="w-3.5 h-3.5 text-[#fe6905]" /> Talkar Service (API)
                  </h4>
                  <p className="text-[11px] text-slate-500">
                    FastAPI + PostgreSQL backend managing customer profiles, pre-paid wallets, Razorpay webhooks, and sub-organization hierarchy.
                  </p>
                </div>

                <div className="bg-slate-50 p-3.5 rounded-lg border border-slate-200 space-y-1.5">
                  <h4 className="font-bold text-slate-900 text-xs flex items-center gap-1.5">
                    <Bot className="w-3.5 h-3.5 text-blue-600" /> Dograh Engine
                  </h4>
                  <p className="text-[11px] text-slate-500">
                    Low-latency voice runtime coordinating WebRTC audio streams, speech-to-text, LLM context, and text-to-speech synthesis in real time.
                  </p>
                </div>

                <div className="bg-slate-50 p-3.5 rounded-lg border border-slate-200 space-y-1.5">
                  <h4 className="font-bold text-slate-900 text-xs flex items-center gap-1.5">
                    <DollarSign className="w-3.5 h-3.5 text-emerald-600" /> Pre-paid Billing Engine
                  </h4>
                  <p className="text-[11px] text-slate-500">
                    Per-second / per-minute balance deduction from wallet float. Auto-blocks calling capacity if balance hits zero or negative.
                  </p>
                </div>
              </div>

              <div className="bg-amber-50/70 border border-amber-200/80 rounded-lg p-3 text-[11px] text-amber-900 space-y-1 mt-3">
                <p className="font-semibold">Important Security Rule: Superuser Impersonation</p>
                <p>
                  When you click <strong>Impersonate</strong>, the admin service requests a short-lived bearer token directly from the Dograh Superuser Gateway. This token bypasses the client's login screen and immediately sets up an authenticated session. Always close impersonation tabs once support diagnostics are complete.
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* TAB CONTENT: 5. BILLING & UNIT ECONOMICS */}
      {activeTab === "billing" && (
        <div className="space-y-6">
          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-5 pb-3 border-b border-slate-100">
              <CardTitle className="text-sm font-bold text-slate-900 uppercase tracking-wider flex items-center gap-2">
                <DollarSign className="w-4 h-4 text-emerald-600" /> Currency, Wallet Float & Unit Economics
              </CardTitle>
            </CardHeader>
            <CardContent className="p-5 space-y-5 text-xs text-slate-700 leading-relaxed">
              <div className="space-y-2">
                <h3 className="font-bold text-slate-900 text-xs">1. Currency Format: Indian Rupees (₹) vs. Paise</h3>
                <p>
                  All database transactions in the Talkar backend are stored as integers representing <strong>Paise</strong> (₹1.00 = 100 paise) to eliminate floating point rounding errors.
                </p>
                <ul className="list-disc pl-5 space-y-1 text-slate-600">
                  <li>In the Admin UI, you always input and view values in standard <strong>Rupees (₹)</strong> with decimal paise support (e.g. ₹5.00/min or ₹5,000 credit).</li>
                  <li>When you submit credit or deduct forms, the frontend automatically handles conversion: <code className="bg-slate-100 px-1 py-0.5 rounded">value_paise = rupees * 100</code>.</li>
                </ul>
              </div>

              <div className="space-y-2 pt-2 border-t border-slate-100">
                <h3 className="font-bold text-slate-900 text-xs">2. Unit Economics Breakdown (Cost vs. Revenue)</h3>
                <p>
                  Every minute of call time incurs costs across four core providers:
                </p>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 pt-1">
                  <div className="bg-slate-50 p-2.5 rounded border border-slate-200">
                    <span className="font-bold text-slate-900 block">📞 Plivo Telephony</span>
                    <span className="text-[11px] text-slate-500">~₹0.45 - ₹0.60 / min</span>
                  </div>
                  <div className="bg-slate-50 p-2.5 rounded border border-slate-200">
                    <span className="font-bold text-slate-900 block">🎤 STT (Deepgram)</span>
                    <span className="text-[11px] text-slate-500">~₹0.35 - ₹0.40 / min</span>
                  </div>
                  <div className="bg-slate-50 p-2.5 rounded border border-slate-200">
                    <span className="font-bold text-slate-900 block">🔊 TTS (ElevenLabs / Smallest)</span>
                    <span className="text-[11px] text-slate-500">~₹0.60 - ₹1.50 / min</span>
                  </div>
                  <div className="bg-slate-50 p-2.5 rounded border border-slate-200">
                    <span className="font-bold text-slate-900 block">🧠 LLM Tokens (GPT-4o Mini)</span>
                    <span className="text-[11px] text-slate-500">~₹0.30 - ₹0.50 / min</span>
                  </div>
                </div>
                <p className="text-[11px] text-slate-500 pt-1">
                  <strong>Total Cost per Minute:</strong> ~₹1.80 to ₹2.90 depending on model selection. At standard ₹5.00/min pricing, gross profit margins remain above <strong>50%</strong>.
                </p>
              </div>

              <div className="space-y-2 pt-2 border-t border-slate-100">
                <h3 className="font-bold text-slate-900 text-xs">3. Master vs. Sub-Organization Billing</h3>
                <p>
                  Enterprise customers can spawn sub-organizations (e.g. branch offices, distinct product teams). In Talkar:
                </p>
                <ul className="list-disc pl-5 space-y-1 text-slate-600">
                  <li><strong>Master Org:</strong> Holds the primary pre-paid wallet, receives all top-up payments, and configures the custom rate card.</li>
                  <li><strong>Sub-Orgs:</strong> Share the master wallet's balance and inherit all custom tier rates automatically. Call charges from sub-orgs deduct directly from the master float.</li>
                </ul>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* TAB CONTENT: 6. TROUBLESHOOTING & FAQS */}
      {activeTab === "troubleshooting" && (
        <div className="space-y-4">
          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-4 pb-2 border-b border-slate-100">
              <CardTitle className="text-xs font-bold text-slate-900 uppercase tracking-wider">
                Q1: A customer's calls are blocked / failing, but their status says "Active". What is wrong?
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 text-xs text-slate-600 space-y-2 leading-relaxed">
              <p>
                This almost always occurs due to one of two reasons:
              </p>
              <ol className="list-decimal pl-5 space-y-1">
                <li><strong>Zero or Negative Wallet Balance:</strong> If the customer's balance drops below ₹0, the automated billing cron sets their call concurrency limit to 0 to prevent unpaid carrier debt. Check their balance in Customer Detail or Wallet Overview. If needed, grant manual credit or advise them to recharge.</li>
                <li><strong>Unsuspend Sync Drift:</strong> If an account was previously suspended, ensure you used the official <strong>Unsuspend</strong> button. This explicitly calls Dograh to restore <code className="bg-slate-100 px-1 py-0.5 rounded">CONCURRENT_CALL_LIMIT</code> back to their tier limit (e.g. 2, 5, or 100).</li>
              </ol>
            </CardContent>
          </Card>

          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-4 pb-2 border-b border-slate-100">
              <CardTitle className="text-xs font-bold text-slate-900 uppercase tracking-wider">
                Q2: How do I purchase and link phone numbers?
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 text-xs text-slate-600 space-y-1.5 leading-relaxed">
              <p>
                Phone numbers are <strong>not automatically bought</strong> by clicking buttons in the admin panel.
              </p>
              <p>
                First, purchase the phone number in your Plivo or Twilio carrier console and configure the webhook URL to point to your Dograh telephony endpoint. Then, navigate to the customer's detail page, scroll down to <strong>Assigned Phone Numbers</strong>, and enter the number in standard E.164 format (+91...) along with the carrier ID for record keeping.
              </p>
            </CardContent>
          </Card>

          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-4 pb-2 border-b border-slate-100">
              <CardTitle className="text-xs font-bold text-slate-900 uppercase tracking-wider">
                Q3: How do external CRM links work in the customer portal?
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 text-xs text-slate-600 space-y-1.5 leading-relaxed">
              <p>
                Inside the customer detail page under <strong>CRM Integrations & External Portals</strong>, click <strong>Add CRM Link</strong>. You can specify a friendly title (e.g., "Admissions Pipeline", "HubSpot Leads") and the URL.
              </p>
              <p>
                When you click <strong>Save All CRM Links</strong>, these links appear automatically in that customer's sidebar portal under an authentic collapsible "CRM Portals" dropdown menu!
              </p>
            </CardContent>
          </Card>

          <Card className="bg-white border border-slate-200/80 rounded-xl shadow-none">
            <CardHeader className="p-4 pb-2 border-b border-slate-100">
              <CardTitle className="text-xs font-bold text-slate-900 uppercase tracking-wider">
                Q4: What happens when an account is suspended?
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 text-xs text-slate-600 space-y-1.5 leading-relaxed">
              <p>
                When an admin clicks <strong>Suspend</strong>:
              </p>
              <ul className="list-disc pl-5 space-y-1">
                <li>The customer's status becomes <code className="bg-rose-50 text-rose-700 px-1 py-0.5 rounded">suspended</code>.</li>
                <li>A persistent red alert banner is displayed at the top of the customer's portal informing them that calling is halted and providing the selected reason.</li>
                <li>The customer's Dograh organization has its concurrent call capacity immediately reduced to 0, ensuring no calls can be initiated or received.</li>
                <li>Clicking <strong>Unsuspend</strong> lifts all restrictions immediately and restores full call capacity.</li>
              </ul>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
