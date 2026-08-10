"use client";
import { adminFetch } from "@/lib/api";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";


export default function AdminLoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();

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
        // Set cookie so middleware can read it
        document.cookie = `talkar_admin_token=${data.access_token}; path=/; max-age=86400; samesite=strict`;
        router.push("/applications");
      } else {
        const err = await res.json();
        setError(err.detail || "Invalid credentials");
      }
    } catch (err) {
      setError("An error occurred during login.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 absolute inset-0 z-50">
      <Card className="w-full max-w-md shadow-lg border-0">
        <CardHeader className="space-y-1 text-center bg-slate-900 text-white rounded-t-lg pt-10 pb-8">
          <CardTitle className="text-2xl font-bold tracking-tight">Talkar Admin Portal</CardTitle>
          <CardDescription className="text-slate-300">
            Enter your admin credentials to access the platform.
          </CardDescription>
        </CardHeader>
        <form onSubmit={handleLogin}>
          <CardContent className="space-y-4 pt-8 pb-4">
            {error && (
              <div className="bg-red-50 text-red-600 p-3 rounded-md text-sm border border-red-200">
                {error}
              </div>
            )}
            <div className="space-y-2">
              <Label htmlFor="email">Admin Email</Label>
              <Input 
                id="email" 
                type="email" 
                placeholder="admin@talkar.ai" 
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input 
                id="password" 
                type="password" 
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
          </CardContent>
          <CardFooter className="pb-8">
            <Button className="w-full" type="submit" disabled={loading}>
              {loading ? "Verifying..." : "Secure Login"}
            </Button>
          </CardFooter>
        </form>
      </Card>
    </div>
  );
}
