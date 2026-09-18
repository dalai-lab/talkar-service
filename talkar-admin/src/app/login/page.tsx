"use client";

import { adminFetch } from "@/lib/api";
import React, { useState } from "react";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { ShieldCheck, AlertCircle, ArrowRight, Loader2 } from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";

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
        document.cookie = `talkar_admin_token=${data.access_token}; path=/; max-age=86400; SameSite=Lax`;
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
    <div className="min-h-screen flex flex-col items-center justify-center bg-[#fafafa] p-4 antialiased selection:bg-[#fe6905]/15 selection:text-[#fe6905]">
      <div className="w-full max-w-md relative z-10">
        {/* Brand Header */}
        <div className="text-center mb-6">
          <div className="inline-flex h-12 w-12 rounded-2xl bg-zinc-900 border border-zinc-800 items-center justify-center shadow-inner mb-3 p-2">
            <BrandLogo mark className="h-8 w-auto" />
          </div>
          <div className="flex items-center justify-center gap-1.5">
            <h1 className="text-2xl font-bold tracking-tight text-zinc-900">Talkar</h1>
            <span className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-[#fe6905]/15 text-[#fe6905] border border-[#fe6905]/30">
              Admin
            </span>
          </div>
          <p className="text-xs text-zinc-500 mt-1">Internal Operations & Management Console</p>
        </div>

        <Card className="border border-zinc-200/90 shadow-sm bg-white rounded-xl overflow-hidden">
          <CardHeader className="p-6 pb-4 border-b border-zinc-100">
            <CardTitle className="text-base font-semibold text-zinc-900 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-[#fe6905]" />
              Secure Staff Authentication
            </CardTitle>
            <CardDescription className="text-xs text-zinc-500">
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
                <Label htmlFor="email" className="text-xs font-medium text-zinc-700">
                  Admin Email
                </Label>
                <Input 
                  id="email" 
                  type="email" 
                  placeholder="admin@talkar.in" 
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="h-10 text-sm bg-white border-zinc-200 focus-visible:ring-[#fe6905]/20 focus-visible:border-[#fe6905]"
                  required
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="password" className="text-xs font-medium text-zinc-700">
                  Password
                </Label>
                <Input 
                  id="password" 
                  type="password" 
                  placeholder="••••••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="h-10 text-sm bg-white border-zinc-200 focus-visible:ring-[#fe6905]/20 focus-visible:border-[#fe6905]"
                  required
                />
              </div>
            </CardContent>

            <CardFooter className="p-6 pt-0 flex flex-col gap-3">
              <Button 
                type="submit" 
                disabled={loading}
                className="w-full h-10 text-sm font-medium bg-[#fe6905] hover:bg-[#e55e04] text-white rounded-lg transition-colors cursor-pointer shadow-sm"
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
        <p className="text-center text-[11px] text-zinc-400 mt-6">
          Authorized personnel only · All administrative activity is logged
        </p>
      </div>
    </div>
  );
}
