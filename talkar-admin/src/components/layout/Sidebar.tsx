"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { 
  Users, 
  Settings, 
  LogOut, 
  ListTodo, 
  Wallet, 
  Activity, 
  TrendingUp, 
  Building,
  Phone,
  HelpCircle,
  Megaphone,
} from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";

const NAV_ITEMS = [
  { title: "Applications", href: "/applications", icon: ListTodo, badge: null },
  { title: "Customers", href: "/customers", icon: Users, badge: null },
  { title: "Announcements", href: "/announcements", icon: Megaphone, badge: null },
  { title: "Support Requests", href: "/support-requests", icon: HelpCircle, badge: null },
  { title: "Build Queue", href: "/build-queue", icon: Building, badge: null },
  { title: "Number Requests", href: "/phone-number-requests", icon: Phone, badge: null },
  { title: "Wallet Overview", href: "/wallet-overview", icon: Wallet, badge: null },
  { title: "Call Activity", href: "/call-activity", icon: Activity, badge: "Live" },
  { title: "Profitability", href: "/profitability", icon: TrendingUp, badge: null },
  { title: "Team", href: "/team", icon: Settings, badge: null },
];

export function Sidebar() {
  const pathname = usePathname();

  const handleLogout = () => {
    document.cookie = "talkar_admin_token=; path=/; max-age=0; SameSite=Lax";
    window.location.href = "/login";
  };

  return (
    <aside className="w-64 bg-[#090a0c] text-zinc-300 h-screen flex flex-col fixed left-0 top-0 border-r border-zinc-800/80 z-40 select-none">
      {/* Brand Header with authentic Talkar SVG */}
      <div className="p-4 pb-4 border-b border-zinc-800/80">
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-xl bg-zinc-900 border border-zinc-800 flex items-center justify-center shrink-0 p-1.5 shadow-inner">
            <BrandLogo mark className="h-6 w-auto" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-bold text-white tracking-tight">Talkar</span>
              <span className="text-[9px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-[#fe6905]/15 text-[#fe6905] border border-[#fe6905]/30">
                Admin
              </span>
            </div>
            <p className="text-[11px] text-zinc-400 truncate">Internal Operations</p>
          </div>
        </div>
      </div>

      {/* Navigation Links */}
      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
        <div className="px-2 pb-2 text-[10px] font-semibold text-zinc-400 uppercase tracking-wider">
          Platform Management
        </div>
        {NAV_ITEMS.map((item) => {
          const isActive = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href + "/"));
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`group relative flex items-center justify-between px-3 py-2 rounded-lg text-xs font-medium transition-all ${
                isActive
                  ? "bg-zinc-800/90 text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06)] border border-zinc-700/60"
                  : "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-900/60"
              }`}
            >
              {isActive && (
                <span className="absolute left-1 top-2 bottom-2 w-0.5 rounded-full bg-[#fe6905]" />
              )}
              <div className="flex items-center gap-2.5">
                <item.icon className={`h-4 w-4 transition-colors ${isActive ? "text-[#fe6905]" : "text-zinc-400 group-hover:text-zinc-300"}`} />
                <span>{item.title}</span>
              </div>
              {item.badge && (
                <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 font-semibold border border-emerald-500/30">
                  <span className="w-1 h-1 rounded-full bg-emerald-400 animate-pulse" />
                  {item.badge}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* Footer / User Profile & Logout */}
      <div className="p-3 border-t border-zinc-800/80 bg-[#07080a]">
        <div className="flex items-center justify-between p-2 rounded-lg bg-zinc-900/60 border border-zinc-800/80 mb-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="h-7 w-7 rounded-md bg-zinc-800 flex items-center justify-center text-xs font-semibold text-zinc-300 border border-zinc-700/60">
              TA
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium text-zinc-200 truncate leading-tight">Admin Console</p>
            </div>
          </div>
        </div>

        <button
          onClick={handleLogout}
          className="flex items-center justify-center gap-2 px-3 py-2 w-full rounded-lg text-xs font-medium text-zinc-400 hover:text-red-400 hover:bg-red-950/20 border border-transparent hover:border-red-900/30 transition-colors cursor-pointer"
        >
          <LogOut className="h-4 w-4" />
          <span>Sign Out</span>
        </button>
      </div>
    </aside>
  );
}
