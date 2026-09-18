"use client";

import { adminFetch } from "@/lib/api";
import React, { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Settings, UserPlus, Trash2 } from "lucide-react";

export default function TeamPage() {
  const [team, setTeam] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const [isAddOpen, setIsAddOpen] = useState(false);
  const [newAdmin, setNewAdmin] = useState({ name: "", email: "", password: "", role: "admin" });

  useEffect(() => {
    fetchTeam();
  }, []);

  const fetchTeam = async () => {
    try {
      const res = await adminFetch(`/admin/team`);
      if (res.ok) {
        const data = await res.json();
        setTeam(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleAddAdmin = async () => {
    try {
      const res = await adminFetch(`/admin/team`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: newAdmin.email,
          password_hash: newAdmin.password,
          name: newAdmin.name,
          role: newAdmin.role
        })
      });
      if (res.ok) {
        setIsAddOpen(false);
        setNewAdmin({ name: "", email: "", password: "", role: "admin" });
        fetchTeam();
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleRemove = async (id: number) => {
    if (!confirm("Are you sure you want to remove this admin?")) return;
    try {
      const res = await adminFetch(`/admin/team/${id}`, { method: "DELETE" });
      if (res.ok) {
        fetchTeam();
      }
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex justify-between items-center pb-2 border-b border-slate-200/70">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 flex items-center gap-2.5">
            <div className="p-2 rounded-lg bg-indigo-50 border border-indigo-100 text-indigo-600">
              <Settings className="w-5 h-5" />
            </div>
            Team Management
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Manage admin staff and access credentials to the Talkar internal console.
          </p>
        </div>
        <Button 
          onClick={() => setIsAddOpen(true)}
          className="h-8 text-xs bg-indigo-600 hover:bg-indigo-700 text-white font-medium cursor-pointer"
        >
          <UserPlus className="w-3.5 h-3.5 mr-1.5" />
          Add Admin
        </Button>
      </div>

      <Card className="bg-white border border-slate-200/80 rounded-xl overflow-hidden shadow-none">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-50/70 border-b border-slate-200/80 hover:bg-slate-50/70">
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Name</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Email</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Role</TableHead>
                <TableHead className="text-right text-slate-600 font-semibold text-xs py-3 pr-4">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={4} className="text-center py-12 text-slate-400 text-xs">
                    <div className="w-5 h-5 border-2 border-indigo-600 border-t-transparent rounded-full animate-spin mx-auto mb-2" />
                    Loading team members...
                  </TableCell>
                </TableRow>
              ) : team.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={4} className="text-center py-12 text-slate-400 text-xs">
                    No team members found.
                  </TableCell>
                </TableRow>
              ) : (
                team.map((member) => (
                  <TableRow key={member.id} className="border-b border-slate-100 hover:bg-slate-50/50 transition-colors">
                    <TableCell className="font-semibold text-xs text-slate-900">{member.name}</TableCell>
                    <TableCell className="text-xs text-slate-600 font-mono">{member.email}</TableCell>
                    <TableCell className="text-xs">
                      <Badge className="bg-slate-100 text-slate-700 border-slate-200 font-medium capitalize">{member.role}</Badge>
                    </TableCell>
                    <TableCell className="text-right pr-4">
                      <Button 
                        variant="ghost" 
                        size="icon" 
                        className="h-7 w-7 text-slate-400 hover:text-red-600 hover:bg-red-50 cursor-pointer" 
                        onClick={() => handleRemove(member.id)}
                        title="Remove admin"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Add Admin Dialog */}
      <Dialog open={isAddOpen} onOpenChange={setIsAddOpen}>
        <DialogContent className="bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold border-b pb-3">Add New Administrator</DialogTitle>
          </DialogHeader>
          <div className="py-3 space-y-3 text-xs">
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Full Name</Label>
              <Input 
                className="h-9 text-xs bg-white border-slate-200" 
                placeholder="e.g. Rahul Sharma"
                value={newAdmin.name} 
                onChange={(e) => setNewAdmin({ ...newAdmin, name: e.target.value })} 
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Email Address</Label>
              <Input 
                type="email" 
                className="h-9 text-xs bg-white border-slate-200" 
                placeholder="admin@talkar.ai"
                value={newAdmin.email} 
                onChange={(e) => setNewAdmin({ ...newAdmin, email: e.target.value })} 
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-slate-700 font-medium">Password</Label>
              <Input 
                type="password" 
                className="h-9 text-xs bg-white border-slate-200" 
                placeholder="••••••••••••"
                value={newAdmin.password} 
                onChange={(e) => setNewAdmin({ ...newAdmin, password: e.target.value })} 
              />
            </div>
          </div>
          <DialogFooter className="border-t pt-3">
            <Button variant="outline" size="sm" onClick={() => setIsAddOpen(false)} className="text-xs border-slate-200">Cancel</Button>
            <Button size="sm" onClick={handleAddAdmin} disabled={!newAdmin.name || !newAdmin.email || !newAdmin.password} className="text-xs bg-indigo-600 hover:bg-indigo-700 text-white">Save Admin</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
