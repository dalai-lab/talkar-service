"use client";

import { adminFetch } from "@/lib/api";
import React, { useState } from "react";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Cpu, ShieldCheck, AlertCircle, ArrowRight, Loader2 } from "lucide-react";

export default function AdminLoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    try {
      const res = await adminFetch(`/admin/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password })
      });

      if (res.ok) {
        const data = await res.json();
        // Set cookie with SameSite=Lax
        document.cookie = `talkar_admin_token=${data.access_token}; path=/; max-age=86400; SameSite=Lax`;
        // Use window.location.href to guarantee a fresh, authenticated browser transition (fixing the refresh bug!)
        window.location.href = "/applications";
      } else {
        const err = await res.json().catch(() => ({}));
        setError(err.detail || "Invalid credentials. Please verify your admin details.");
      }
    } catch (err) {
      setError("An error occurred connecting to the admin service.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-[#f8fafc] p-4 antialiased">
      {/* Background subtle decoration */}
      <div className="absolute inset-0 bg-[radial-gradient(#e2e8f0_1px,transparent_1px)] [background-size:16px_16px] opacity-60 pointer-events-none" />

      <div className="w-full max-w-md relative z-10">
        {/* Brand Header */}
        <div className="text-center mb-6">
          <div className="inline-flex h-12 w-12 rounded-xl bg-indigo-600 items-center justify-center text-white shadow-sm mb-3">
            <Cpu className="h-6 w-6" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">Talkar Admin</h1>
          <p className="text-xs text-slate-500 mt-1">Internal Operations & Management Console</p>
        </div>

        <Card className="border border-slate-200/80 shadow-sm bg-white rounded-xl overflow-hidden">
          <CardHeader className="p-6 pb-4 border-b border-slate-100">
            <CardTitle className="text-base font-semibold text-slate-900 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-indigo-600" />
              Secure Authentication
            </CardTitle>
            <CardDescription className="text-xs text-slate-500">
              Enter your authorized staff credentials to continue.
            </CardDescription>
          </CardHeader>

          <form onSubmit={handleLogin}>
            <CardContent className="p-6 space-y-4">
              {error && (
                <div className="flex items-start gap-2 bg-red-50 text-red-700 p-3 rounded-lg text-xs border border-red-200/80">
                  <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-red-600" />
                  <span>{error}</span>
                </div>
              )}

              <div className="space-y-1.5">
                <Label htmlFor="email" className="text-xs font-medium text-slate-700">
                  Admin Email
                </Label>
                <Input 
                  id="email" 
                  type="email" 
                  placeholder="admin@talkar.ai" 
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="h-10 text-sm bg-white border-slate-200"
                  required
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="password" className="text-xs font-medium text-slate-700">
                  Password
                </Label>
                <Input 
                  id="password" 
                  type="password" 
                  placeholder="••••••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="h-10 text-sm bg-white border-slate-200"
                  required
                />
              </div>
            </CardContent>

            <CardFooter className="p-6 pt-0 flex flex-col gap-3">
              <Button 
                type="submit" 
                disabled={loading}
                className="w-full h-10 text-sm font-medium bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors cursor-pointer"
              >
                {loading ? (
                  <span className="flex items-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Signing in...
                  </span>
                ) : (
                  <span className="flex items-center gap-2">
                    Sign in to Console
                    <ArrowRight className="w-4 h-4" />
                  </span>
                )}
              </Button>
            </CardFooter>
          </form>
        </Card>

        {/* Security badge footer */}
        <p className="text-center text-[11px] text-slate-400 mt-6">
          Authorized personnel only · All administrative activity is logged
        </p>
      </div>
    </div>
  );
}
