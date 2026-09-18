"use client";

import React from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { ShieldCheck, LogOut, ChevronRight } from "lucide-react";

const ROUTE_NAMES: Record<string, string> = {
  "/applications": "Applications Queue",
  "/customers": "Customer Directory",
  "/support-requests": "Support & Feature Requests",
  "/build-queue": "Agent Build Queue",
  "/phone-number-requests": "Phone Number Requests",
  "/wallet-overview": "Wallet & Float Overview",
  "/call-activity": "Live Call Activity",
  "/profitability": "Profitability Dashboard",
  "/team": "Team Management",
};

export function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLoginPage = pathname === "/login";

  if (isLoginPage) {
    return <div className="min-h-screen bg-[#fafafa]">{children}</div>;
  }

  const currentTitle = ROUTE_NAMES[pathname] || (pathname.startsWith("/customers/") ? "Customer Details" : "Operations Console");

  const handleLogout = () => {
    document.cookie = "talkar_admin_token=; path=/; max-age=0; SameSite=Lax";
    window.location.href = "/login";
  };

  return (
    <div className="flex min-h-screen bg-[#fafafa] text-zinc-900 antialiased selection:bg-[#fe6905]/15 selection:text-[#fe6905]">
      <Sidebar />
      <div className="flex-1 ml-64 flex flex-col min-w-0">
        {/* Minimal Modern Top Header */}
        <header className="h-14 border-b border-zinc-200/80 bg-white/90 backdrop-blur-md sticky top-0 z-30 px-8 flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs font-medium text-zinc-500">
            <span className="text-zinc-400 font-semibold tracking-wider uppercase text-[10px]">Talkar Admin</span>
            <ChevronRight className="w-3.5 h-3.5 text-zinc-300" />
            <span className="text-zinc-800 font-semibold">{currentTitle}</span>
          </div>

          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 px-2.5 py-1 rounded-full bg-emerald-50 border border-emerald-200/70 text-emerald-700 text-xs font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
              <span>Console Active</span>
            </div>

            <div className="h-4 w-px bg-zinc-200" />

            <div className="flex items-center gap-2 text-xs text-zinc-600">
              <ShieldCheck className="w-4 h-4 text-[#fe6905]" />
              <span className="font-medium hidden sm:inline">Admin Session</span>
            </div>

            <button
              onClick={handleLogout}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium text-zinc-500 hover:text-zinc-900 hover:bg-zinc-100 transition-colors cursor-pointer"
              title="Sign out of admin session"
            >
              <LogOut className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Logout</span>
            </button>
          </div>
        </header>

        {/* Main Content Area */}
        <main className="flex-1 p-6 md:p-8 max-w-7xl w-full mx-auto">
          {children}
        </main>
      </div>
    </div>
  );
}
