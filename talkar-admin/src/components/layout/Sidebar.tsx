"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { 
  Users, 
  Settings, 
  LogOut, 
  LayoutDashboard, 
  ListTodo, 
  Wallet, 
  Activity, 
  TrendingUp, 
  Building 
} from "lucide-react";

const NAV_ITEMS = [
  { title: "Applications", href: "/applications", icon: ListTodo },
  { title: "Customers", href: "/customers", icon: Users },
  { title: "Build Queue", href: "/build-queue", icon: Building },
  { title: "Wallet Overview", href: "/wallet-overview", icon: Wallet },
  { title: "Call Activity", href: "/call-activity", icon: Activity },
  { title: "Profitability", href: "/profitability", icon: TrendingUp },
  { title: "Team", href: "/team", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <div className="w-64 bg-slate-900 text-white h-screen flex flex-col fixed left-0 top-0">
      <div className="p-6">
        <h1 className="text-2xl font-bold tracking-tight">Talkar Admin</h1>
      </div>
      <nav className="flex-1 px-4 space-y-1">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-3 py-2 rounded-md transition-colors ${
                isActive ? "bg-slate-800 text-white" : "text-slate-300 hover:bg-slate-800 hover:text-white"
              }`}
            >
              <item.icon className="h-5 w-5" />
              {item.title}
            </Link>
          );
        })}
      </nav>
      <div className="p-4 border-t border-slate-800">
        <button className="flex items-center gap-3 px-3 py-2 w-full rounded-md text-slate-300 hover:bg-slate-800 hover:text-white transition-colors">
          <LogOut className="h-5 w-5" />
          Logout
        </button>
      </div>
    </div>
  );
}
