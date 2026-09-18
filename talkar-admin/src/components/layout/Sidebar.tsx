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
  Radio,
  Cpu
} from "lucide-react";

const NAV_ITEMS = [
  { title: "Applications", href: "/applications", icon: ListTodo, badge: null },
  { title: "Customers", href: "/customers", icon: Users, badge: null },
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
    <aside className="w-64 bg-slate-950 text-slate-300 h-screen flex flex-col fixed left-0 top-0 border-r border-slate-800/80 z-40 select-none">
      {/* Brand Header */}
      <div className="p-5 pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-lg bg-indigo-600 flex items-center justify-center text-white font-bold shadow-sm">
            <Cpu className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <h1 className="text-base font-semibold text-white tracking-tight">Talkar</h1>
              <span className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                Admin
              </span>
            </div>
            <p className="text-[11px] text-slate-400">Internal Operations</p>
          </div>
        </div>
      </div>

      {/* Navigation Links */}
      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
        <div className="px-2 pb-2 text-[10px] font-bold text-slate-500 uppercase tracking-wider">
          Platform Management
        </div>
        {NAV_ITEMS.map((item) => {
          const isActive = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href + "/"));
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`group flex items-center justify-between px-3 py-2 rounded-lg text-xs font-medium transition-colors ${
                isActive
                  ? "bg-indigo-600 text-white shadow-sm"
                  : "text-slate-400 hover:text-white hover:bg-slate-900"
              }`}
            >
              <div className="flex items-center gap-2.5">
                <item.icon className={`h-4 w-4 ${isActive ? "text-white" : "text-slate-400 group-hover:text-slate-200"}`} />
                <span>{item.title}</span>
              </div>
              {item.badge && (
                <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.2 rounded-full bg-emerald-500/20 text-emerald-400 font-semibold border border-emerald-500/30">
                  <span className="w-1 h-1 rounded-full bg-emerald-400 animate-pulse" />
                  {item.badge}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* Footer / User Profile & Logout */}
      <div className="p-3 border-t border-slate-800/80 bg-slate-950/60">
        <div className="flex items-center justify-between p-2 rounded-lg bg-slate-900/60 border border-slate-800/80 mb-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="h-7 w-7 rounded-md bg-slate-800 flex items-center justify-center text-xs font-semibold text-indigo-300 border border-slate-700">
              TA
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium text-white truncate leading-tight">Admin Console</p>
              <p className="text-[10px] text-emerald-400 flex items-center gap-1 leading-tight mt-0.5">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                Authenticated
              </p>
            </div>
          </div>
        </div>

        <button
          onClick={handleLogout}
          className="flex items-center justify-center gap-2 px-3 py-2 w-full rounded-lg text-xs font-medium text-slate-400 hover:text-red-400 hover:bg-red-950/30 border border-transparent hover:border-red-900/40 transition-colors cursor-pointer"
        >
          <LogOut className="h-4 w-4" />
          <span>Sign Out</span>
        </button>
      </div>
    </aside>
  );
}
