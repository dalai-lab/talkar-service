"use client";

import React, { useEffect, useState } from "react";
import { adminFetch } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Key, Trash2, Plus, Copy, AlertTriangle, CheckCircle2 } from "lucide-react";

interface ApiKey {
  id: number;
  name: string;
  scopes: string[];
  rate_limit_per_minute: number;
  is_active: boolean;
  created_at: string;
  last_used_at: string | null;
}

const AVAILABLE_SCOPES = [
  { id: "credit", label: "Credit", desc: "Add funds to wallets" },
  { id: "deduct", label: "Deduct", desc: "Remove funds from wallets" },
  { id: "wallet_read", label: "Wallet Read", desc: "View balances" },
  { id: "suspend", label: "Suspend", desc: "Suspend/Unsuspend accounts" },
  { id: "customer_read", label: "Customer Read", desc: "View customer details" },
  { id: "stats_read", label: "Stats Read", desc: "View call stats and alerts" },
];

export default function AutomationKeysPage() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);

  // Dialog State
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [newKeyName, setNewKeyName] = useState("");
  const [selectedScopes, setSelectedScopes] = useState<string[]>([]);
  const [rateLimit, setRateLimit] = useState(60);
  
  // Success State
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    fetchKeys();
  }, []);

  const fetchKeys = async () => {
    try {
      setLoading(true);
      const res = await adminFetch("/admin/automation-keys");
      if (res.ok) {
        const data = await res.json();
        setKeys(data);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    if (!newKeyName || selectedScopes.length === 0) return;
    
    try {
      const res = await adminFetch("/admin/automation-keys", {
        method: "POST",
        body: JSON.stringify({
          name: newKeyName,
          scopes: selectedScopes,
          rate_limit_per_minute: rateLimit
        })
      });
      
      if (res.ok) {
        const data = await res.json();
        setCreatedKey(data.raw_key);
        fetchKeys();
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Are you sure you want to permanently revoke this key? Any integrations using it will immediately break.")) return;
    
    try {
      const res = await adminFetch(`/admin/automation-keys/${id}`, { method: "DELETE" });
      if (res.ok) {
        fetchKeys();
      }
    } catch (err) {
      console.error(err);
    }
  };

  const toggleScope = (scopeId: string) => {
    setSelectedScopes(prev => 
      prev.includes(scopeId) ? prev.filter(s => s !== scopeId) : [...prev, scopeId]
    );
  };

  const handleCopy = () => {
    if (createdKey) {
      navigator.clipboard.writeText(createdKey);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const resetDialog = () => {
    setIsDialogOpen(false);
    setCreatedKey(null);
    setNewKeyName("");
    setSelectedScopes([]);
    setRateLimit(60);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Automation API Keys</h1>
          <p className="text-sm text-zinc-500 mt-1">Manage secure access keys for n8n and other integrations.</p>
        </div>
        <Button onClick={() => setIsDialogOpen(true)} className="bg-[#fe6905] hover:bg-[#e55e04] text-white">
          <Plus className="w-4 h-4 mr-2" />
          Generate New Key
        </Button>
      </div>

      {loading ? (
        <div className="h-32 flex items-center justify-center text-zinc-400 text-sm">Loading keys...</div>
      ) : keys.length === 0 ? (
        <Card className="p-12 flex flex-col items-center justify-center text-center border-dashed bg-zinc-50/50">
          <div className="w-12 h-12 rounded-full bg-[#fe6905]/10 flex items-center justify-center mb-4">
            <Key className="w-6 h-6 text-[#fe6905]" />
          </div>
          <h3 className="text-lg font-semibold mb-1">No API Keys Yet</h3>
          <p className="text-sm text-zinc-500 mb-6 max-w-sm">Create an API key to securely connect Talkar to your external automation tools like n8n.</p>
          <Button onClick={() => setIsDialogOpen(true)} variant="outline">Generate First Key</Button>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4">
          {keys.map((k) => (
            <Card key={k.id} className="p-5 flex items-start justify-between">
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <h3 className="font-semibold text-zinc-900">{k.name}</h3>
                  <Badge variant="secondary" className="bg-emerald-100 text-emerald-800 hover:bg-emerald-100 border-none">Active</Badge>
                </div>
                
                <div className="flex flex-wrap gap-1.5">
                  {k.scopes.map(s => (
                    <Badge key={s} variant="outline" className="text-[10px] font-mono text-zinc-600 bg-zinc-50">{s}</Badge>
                  ))}
                </div>

                <div className="text-[11px] text-zinc-500 flex items-center gap-4">
                  <span>Created: {new Date(k.created_at).toLocaleDateString()}</span>
                  <span>Last Used: {k.last_used_at ? new Date(k.last_used_at).toLocaleString() : 'Never'}</span>
                  <span>Rate Limit: {k.rate_limit_per_minute} RPM</span>
                </div>
              </div>
              
              <Button variant="ghost" size="icon" onClick={() => handleDelete(k.id)} className="text-zinc-400 hover:text-red-600 hover:bg-red-50">
                <Trash2 className="w-4 h-4" />
              </Button>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={isDialogOpen} onOpenChange={(open) => !open && resetDialog()}>
        <DialogContent className="sm:max-w-md">
          {!createdKey ? (
            <>
              <DialogHeader>
                <DialogTitle>Generate Automation Key</DialogTitle>
                <DialogDescription>
                  Create a new API key for external integrations. Be sure to grant only the permissions needed.
                </DialogDescription>
              </DialogHeader>
              
              <div className="space-y-5 py-4">
                <div className="space-y-1.5">
                  <Label>Key Name</Label>
                  <Input 
                    placeholder="e.g. n8n Production" 
                    value={newKeyName} 
                    onChange={e => setNewKeyName(e.target.value)} 
                  />
                </div>

                <div className="space-y-2">
                  <Label>Permissions (Scopes)</Label>
                  <div className="grid grid-cols-2 gap-2">
                    {AVAILABLE_SCOPES.map(scope => (
                      <div 
                        key={scope.id}
                        onClick={() => toggleScope(scope.id)}
                        className={`p-3 rounded-md border text-sm cursor-pointer transition-colors ${
                          selectedScopes.includes(scope.id) 
                            ? 'border-[#fe6905] bg-[#fe6905]/5 text-[#fe6905]' 
                            : 'border-zinc-200 hover:border-zinc-300 bg-white'
                        }`}
                      >
                        <div className="font-semibold text-xs mb-0.5">{scope.label}</div>
                        <div className={`text-[10px] leading-tight ${selectedScopes.includes(scope.id) ? 'text-[#fe6905]/70' : 'text-zinc-500'}`}>
                          {scope.desc}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label>Rate Limit (Req / Minute)</Label>
                  <Input 
                    type="number" 
                    value={rateLimit} 
                    onChange={e => setRateLimit(Number(e.target.value))} 
                    min={1} max={1000}
                  />
                </div>
              </div>
              
              <DialogFooter>
                <Button variant="outline" onClick={resetDialog}>Cancel</Button>
                <Button 
                  onClick={handleCreate} 
                  disabled={!newKeyName || selectedScopes.length === 0}
                  className="bg-[#fe6905] hover:bg-[#e55e04] text-white"
                >
                  Generate Key
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <DialogHeader>
                <div className="w-12 h-12 rounded-full bg-emerald-100 flex items-center justify-center mx-auto mb-4">
                  <CheckCircle2 className="w-6 h-6 text-emerald-600" />
                </div>
                <DialogTitle className="text-center text-xl">Key Generated Successfully</DialogTitle>
              </DialogHeader>
              
              <div className="py-6 space-y-4">
                <div className="p-3 bg-amber-50 border border-amber-200 rounded-md flex items-start gap-3 text-amber-800">
                  <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5 text-amber-600" />
                  <div className="text-sm">
                    <p className="font-semibold mb-1">Please copy this key now.</p>
                    <p>For your security, it will <strong>never be shown again</strong>. If you lose it, you will need to generate a new one.</p>
                  </div>
                </div>
                
                <div className="relative group">
                  <Input 
                    readOnly 
                    value={createdKey} 
                    className="pr-24 font-mono text-sm bg-zinc-50 h-12"
                  />
                  <Button 
                    size="sm" 
                    className="absolute right-1.5 top-1.5 h-9 transition-all"
                    onClick={handleCopy}
                    variant={copied ? "default" : "secondary"}
                  >
                    {copied ? 'Copied!' : <><Copy className="w-3.5 h-3.5 mr-1.5" /> Copy</>}
                  </Button>
                </div>
              </div>

              <DialogFooter className="sm:justify-center">
                <Button onClick={resetDialog} className="w-full">
                  I have copied the key
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}