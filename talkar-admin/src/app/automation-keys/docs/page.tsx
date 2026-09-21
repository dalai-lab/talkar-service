import React from 'react';
import { BookOpen, Key, Bell, ShieldBan, CreditCard, Activity, ArrowLeft } from 'lucide-react';
import Link from 'next/link';

export default function ApiDocsPage() {
  return (
    <div className="min-h-screen bg-[#040506] text-zinc-300 p-8 pt-12">
      <div className="max-w-5xl mx-auto space-y-12">
        {/* Header */}
        <div>
          <div className="flex items-center gap-4 mb-4">
            <Link href="/automation-keys" className="text-zinc-500 hover:text-zinc-300 transition-colors">
              <ArrowLeft className="w-5 h-5" />
            </Link>
            <div className="h-10 w-10 rounded-xl bg-[#fe6905]/10 border border-[#fe6905]/20 flex items-center justify-center">
              <BookOpen className="w-5 h-5 text-[#fe6905]" />
            </div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Automation API Reference</h1>
          </div>
          <p className="text-sm text-zinc-400 max-w-3xl leading-relaxed">
            The Talkar Automation API enables you to integrate billing, account management, and notification flows directly with external tools like <strong>n8n</strong>, <strong>Stripe</strong>, or <strong>Make.com</strong>.
          </p>
        </div>

        {/* Auth Section */}
        <div className="bg-zinc-900/40 border border-zinc-800/80 rounded-2xl p-6">
          <div className="flex items-center gap-2 mb-4">
            <Key className="w-5 h-5 text-[#fe6905]" />
            <h2 className="text-lg font-semibold text-white">Authentication</h2>
          </div>
          <p className="text-sm text-zinc-400 mb-4">
            All API requests must include your Automation API Key in the headers. Generate keys in the Automation Keys section of your dashboard.
          </p>
          <div className="bg-black border border-zinc-800 rounded-lg p-4 font-mono text-sm">
            <div className="text-zinc-500 mb-2"># Base URL: https://billing.talkar.in/automation/v1</div>
            <div className="text-emerald-400">X-Talkar-API-Key: <span className="text-zinc-300">tkr_auto_XXXXXXXXXXXXXXXXXXXXXXXX</span></div>
            <div className="text-emerald-400">Content-Type: <span className="text-zinc-300">application/json</span></div>
          </div>
        </div>

        {/* Notifications Section */}
        <div className="space-y-6">
          <div className="flex items-center gap-2 border-b border-zinc-800/80 pb-3">
            <Bell className="w-5 h-5 text-[#fe6905]" />
            <h2 className="text-xl font-semibold text-white">Notifications & Communications</h2>
          </div>
          
          <div className="bg-zinc-900/40 border border-zinc-800/80 rounded-2xl p-6">
            <div className="flex items-center gap-3 mb-2">
              <span className="px-2 py-0.5 rounded text-[10px] font-bold tracking-wider bg-blue-500/10 text-blue-400 border border-blue-500/20">POST</span>
              <code className="text-sm text-zinc-200 font-semibold">/customers/&#123;dograh_org_id&#125;/notify</code>
            </div>
            <p className="text-sm text-zinc-400 mb-4">
              Dispatch an in-app alert or an email directly to a customer. Powered by an enterprise-grade ARQ queue to protect your email sender reputation.
              <br/><span className="text-[#fe6905] font-semibold mt-1 inline-block">Required Scope: notify</span>
            </p>
            <div className="bg-black border border-zinc-800 rounded-lg p-4 font-mono text-xs text-zinc-300">
              <pre>
&#123;
  "content": &#123;
    "title": "Welcome to Talkar",
    "body": "Your integration is successful.",
    "alert_type": "success" // info, warning, error, success
  &#125;,
  "delivery": &#123;
    "in_app_notification": true,
    "send_email": true
  &#125;
&#125;
              </pre>
            </div>
          </div>
        </div>

        {/* Billing Section */}
        <div className="space-y-6">
          <div className="flex items-center gap-2 border-b border-zinc-800/80 pb-3">
            <CreditCard className="w-5 h-5 text-[#fe6905]" />
            <h2 className="text-xl font-semibold text-white">Billing & Wallet</h2>
          </div>
          
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="bg-zinc-900/40 border border-zinc-800/80 rounded-2xl p-6">
              <div className="flex items-center gap-3 mb-2">
                <span className="px-2 py-0.5 rounded text-[10px] font-bold tracking-wider bg-blue-500/10 text-blue-400 border border-blue-500/20">POST</span>
                <code className="text-xs text-zinc-200 font-semibold">/customers/&#123;dograh_org_id&#125;/credit</code>
              </div>
              <p className="text-xs text-zinc-400 mb-4">Add funds to a customer's wallet.<br/><span className="text-[#fe6905] font-semibold">Required Scope: credit</span></p>
              <div className="bg-black border border-zinc-800 rounded-lg p-3 font-mono text-[11px] text-zinc-300">
                <pre>&#123;&#10;  "amount_rupees": 500.0,&#10;  "description": "Stripe top-up"&#10;&#125;</pre>
              </div>
            </div>

            <div className="bg-zinc-900/40 border border-zinc-800/80 rounded-2xl p-6">
              <div className="flex items-center gap-3 mb-2">
                <span className="px-2 py-0.5 rounded text-[10px] font-bold tracking-wider bg-blue-500/10 text-blue-400 border border-blue-500/20">POST</span>
                <code className="text-xs text-zinc-200 font-semibold">/customers/&#123;dograh_org_id&#125;/deduct</code>
              </div>
              <p className="text-xs text-zinc-400 mb-4">Remove funds from a wallet.<br/><span className="text-[#fe6905] font-semibold">Required Scope: deduct</span></p>
              <div className="bg-black border border-zinc-800 rounded-lg p-3 font-mono text-[11px] text-zinc-300">
                <pre>&#123;&#10;  "amount_rupees": 100.0,&#10;  "reason": "Monthly fee"&#10;&#125;</pre>
              </div>
            </div>
            
            <div className="bg-zinc-900/40 border border-zinc-800/80 rounded-2xl p-6 md:col-span-2">
              <div className="flex items-center gap-3 mb-2">
                <span className="px-2 py-0.5 rounded text-[10px] font-bold tracking-wider bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">GET</span>
                <code className="text-xs text-zinc-200 font-semibold">/customers/&#123;dograh_org_id&#125;/wallet</code>
              </div>
              <p className="text-xs text-zinc-400 mb-1">Fetch current balance.<br/><span className="text-[#fe6905] font-semibold">Required Scope: wallet_read</span></p>
            </div>
          </div>
        </div>

        {/* Account Mgmt Section */}
        <div className="space-y-6">
          <div className="flex items-center gap-2 border-b border-zinc-800/80 pb-3">
            <ShieldBan className="w-5 h-5 text-[#fe6905]" />
            <h2 className="text-xl font-semibold text-white">Account Management</h2>
          </div>
          
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="bg-zinc-900/40 border border-zinc-800/80 rounded-2xl p-6">
              <div className="flex items-center gap-3 mb-2">
                <span className="px-2 py-0.5 rounded text-[10px] font-bold tracking-wider bg-blue-500/10 text-blue-400 border border-blue-500/20">POST</span>
                <code className="text-xs text-zinc-200 font-semibold">/customers/&#123;dograh_org_id&#125;/suspend</code>
              </div>
              <p className="text-xs text-zinc-400 mb-4">Suspend workspace and block calls.<br/><span className="text-[#fe6905] font-semibold">Required Scope: suspend</span></p>
              <div className="bg-black border border-zinc-800 rounded-lg p-3 font-mono text-[11px] text-zinc-300">
                <pre>&#123;&#10;  "reason": "zero_balance",&#10;  "custom_message": "Top up to restore."&#10;&#125;</pre>
              </div>
            </div>

            <div className="bg-zinc-900/40 border border-zinc-800/80 rounded-2xl p-6">
              <div className="flex items-center gap-3 mb-2">
                <span className="px-2 py-0.5 rounded text-[10px] font-bold tracking-wider bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">GET</span>
                <code className="text-xs text-zinc-200 font-semibold">/customers/&#123;dograh_org_id&#125;</code>
              </div>
              <p className="text-xs text-zinc-400 mb-1">Fetch basic metadata and status.<br/><span className="text-[#fe6905] font-semibold">Required Scope: customer_read</span></p>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}