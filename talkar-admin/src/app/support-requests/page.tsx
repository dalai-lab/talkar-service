"use client";

import React, { useEffect, useState, useCallback } from "react";
import { adminFetch } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { 
  HelpCircle, 
  Sparkles, 
  Search, 
  Trash2, 
  CheckCircle2, 
  XCircle, 
  Clock, 
  RefreshCw
} from "lucide-react";

export default function SupportRequestsPage() {
  const [requests, setRequests] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  // Filters
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");

  // Modals & Active items
  const [selectedReq, setSelectedReq] = useState<any | null>(null);
  const [isUpdateOpen, setIsUpdateOpen] = useState(false);
  const [status, setStatus] = useState("");
  const [adminNote, setAdminNote] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  // Delete modal
  const [reqToDelete, setReqToDelete] = useState<any | null>(null);
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const fetchRequests = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (typeFilter !== "all") params.append("req_type", typeFilter);
      if (statusFilter !== "all") params.append("status", statusFilter);
      if (searchQuery.trim()) params.append("search", searchQuery.trim());

      const res = await adminFetch(`/admin/support-requests?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setRequests(Array.isArray(data) ? data : []);
      }
    } catch (e) {
      console.error("Failed to fetch support requests", e);
    } finally {
      setLoading(false);
    }
  }, [typeFilter, statusFilter, searchQuery]);

  useEffect(() => {
    fetchRequests();
  }, [fetchRequests]);

  const openUpdate = (req: any) => {
    setSelectedReq(req);
    setStatus(req.status || "open");
    setAdminNote(req.admin_note || "");
    setIsUpdateOpen(true);
  };

  const handleUpdate = async () => {
    if (!selectedReq) return;
    setIsSaving(true);
    try {
      const res = await adminFetch(`/admin/support-requests/${selectedReq.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, admin_note: adminNote })
      });
      if (res.ok) {
        setIsUpdateOpen(false);
        fetchRequests();
      }
    } catch (e) {
      console.error("Failed to update support request", e);
    } finally {
      setIsSaving(false);
    }
  };

  const confirmDelete = (req: any) => {
    setReqToDelete(req);
    setIsDeleteOpen(true);
  };

  const handleDelete = async () => {
    if (!reqToDelete) return;
    setIsDeleting(true);
    try {
      const res = await adminFetch(`/admin/support-requests/${reqToDelete.id}`, {
        method: "DELETE"
      });
      if (res.ok) {
        setIsDeleteOpen(false);
        setReqToDelete(null);
        fetchRequests();
      }
    } catch (e) {
      console.error("Failed to delete support request", e);
    } finally {
      setIsDeleting(false);
    }
  };

  // Metrics
  const totalCount = requests.length;
  const openCount = requests.filter(r => r.status === "open").length;
  const inProgressCount = requests.filter(r => r.status === "in_progress").length;
  const approvedCount = requests.filter(r => r.status === "approved").length;
  const resolvedCount = requests.filter(r => r.status === "resolved").length;

  const renderStatusBadge = (st: string) => {
    switch (st) {
      case "approved":
        return <Badge className="bg-emerald-600 hover:bg-emerald-700 text-white">Approved</Badge>;
      case "resolved":
        return <Badge className="bg-green-600 hover:bg-green-700 text-white">Resolved</Badge>;
      case "in_progress":
        return <Badge className="bg-blue-600 hover:bg-blue-700 text-white">In Progress</Badge>;
      case "rejected":
        return <Badge variant="destructive">Rejected</Badge>;
      case "closed":
        return <Badge variant="secondary">Closed</Badge>;
      default:
        return <Badge className="bg-amber-500 hover:bg-amber-600 text-white">Open</Badge>;
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h2 className="text-3xl font-bold tracking-tight text-white flex items-center gap-2">
            <HelpCircle className="w-8 h-8 text-indigo-400" />
            Support & Feature Requests
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            Review customer tickets, approve feature requests, post direct replies, or manage backlog.
          </p>
        </div>

        <Button
          variant="outline"
          size="sm"
          onClick={fetchRequests}
          disabled={loading}
          className="w-fit"
        >
          <RefreshCw className={`w-4 h-4 mr-1.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Metrics Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-3">
          <p className="text-xs text-muted-foreground font-medium">Total</p>
          <p className="text-2xl font-bold text-white mt-0.5">{totalCount}</p>
        </div>
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-3">
          <p className="text-xs text-amber-400 font-medium">Open / Pending</p>
          <p className="text-2xl font-bold text-amber-300 mt-0.5">{openCount}</p>
        </div>
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-3">
          <p className="text-xs text-blue-400 font-medium">In Progress</p>
          <p className="text-2xl font-bold text-blue-300 mt-0.5">{inProgressCount}</p>
        </div>
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-3">
          <p className="text-xs text-emerald-400 font-medium">Approved</p>
          <p className="text-2xl font-bold text-emerald-300 mt-0.5">{approvedCount}</p>
        </div>
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 col-span-2 sm:col-span-1">
          <p className="text-xs text-green-400 font-medium">Resolved</p>
          <p className="text-2xl font-bold text-green-300 mt-0.5">{resolvedCount}</p>
        </div>
      </div>

      {/* Controls / Filter Bar */}
      <div className="flex flex-col sm:flex-row gap-3 items-center justify-between bg-slate-900/60 border border-slate-800 p-3 rounded-lg">
        {/* Search */}
        <div className="relative w-full sm:w-72">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search company, email, or subject..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9 h-9 text-xs bg-slate-950 border-slate-800"
          />
        </div>

        {/* Filters */}
        <div className="flex items-center gap-2 w-full sm:w-auto overflow-x-auto">
          {/* Type Filter */}
          <div className="flex items-center bg-slate-950 border border-slate-800 rounded-md p-0.5 text-xs">
            <button
              onClick={() => setTypeFilter("all")}
              className={`px-2.5 py-1 rounded transition-colors ${typeFilter === "all" ? "bg-indigo-600 text-white font-semibold" : "text-muted-foreground hover:text-white"}`}
            >
              All Types
            </button>
            <button
              onClick={() => setTypeFilter("support")}
              className={`px-2.5 py-1 rounded transition-colors ${typeFilter === "support" ? "bg-indigo-600 text-white font-semibold" : "text-muted-foreground hover:text-white"}`}
            >
              Support
            </button>
            <button
              onClick={() => setTypeFilter("feature_request")}
              className={`px-2.5 py-1 rounded transition-colors ${typeFilter === "feature_request" ? "bg-purple-600 text-white font-semibold" : "text-muted-foreground hover:text-white"}`}
            >
              Features
            </button>
          </div>

          {/* Status Filter */}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="h-9 px-3 text-xs bg-slate-950 border border-slate-800 text-white rounded-md"
          >
            <option value="all">All Statuses</option>
            <option value="open">Open</option>
            <option value="in_progress">In Progress</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="resolved">Resolved</option>
            <option value="closed">Closed</option>
          </select>
        </div>
      </div>

      {/* Main Table */}
      <Card className="bg-slate-900 border-slate-800">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-800 hover:bg-transparent">
                <TableHead className="text-slate-400">Date</TableHead>
                <TableHead className="text-slate-400">Customer</TableHead>
                <TableHead className="text-slate-400">Type / Subject</TableHead>
                <TableHead className="text-slate-400">Status</TableHead>
                <TableHead className="text-slate-400">Admin Response</TableHead>
                <TableHead className="text-right text-slate-400">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-12 text-muted-foreground">
                    <div className="w-5 h-5 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin mx-auto mb-2" />
                    Loading requests...
                  </TableCell>
                </TableRow>
              ) : requests.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-12 text-muted-foreground">
                    No support or feature requests found matching the current filters.
                  </TableCell>
                </TableRow>
              ) : (
                requests.map((req) => (
                  <TableRow key={req.id} className="border-slate-800 hover:bg-slate-800/50">
                    <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                      {req.created_at ? new Date(req.created_at).toLocaleDateString() : "-"}
                      <div className="text-[10px] text-slate-500">#{req.id}</div>
                    </TableCell>

                    <TableCell className="font-medium text-xs">
                      <span className="text-white font-semibold">
                        {req.customer?.company_name || "Unknown Company"}
                      </span>
                      <div className="text-[11px] text-muted-foreground">
                        {req.customer?.contact_email}
                      </div>
                      {req.customer?.dograh_org_id && (
                        <span className="text-[10px] font-mono text-slate-500">
                          Org ID: {req.customer.dograh_org_id}
                        </span>
                      )}
                    </TableCell>

                    <TableCell className="max-w-md">
                      <div className="flex items-center gap-1.5 mb-1">
                        {req.type === "feature_request" ? (
                          <span className="inline-flex items-center gap-1 text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300 border border-purple-500/30">
                            <Sparkles className="w-2.5 h-2.5" /> Feature
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                            <HelpCircle className="w-2.5 h-2.5" /> Support
                          </span>
                        )}
                        <span className="font-semibold text-xs text-white">{req.subject}</span>
                      </div>
                      <p className="text-xs text-muted-foreground line-clamp-2 leading-normal">
                        {req.description}
                      </p>
                    </TableCell>

                    <TableCell>{renderStatusBadge(req.status)}</TableCell>

                    <TableCell className="max-w-xs text-xs text-muted-foreground">
                      {req.admin_note ? (
                        <div className="truncate text-slate-300">
                          <span className="text-indigo-400 font-medium">Replied: </span>
                          {req.admin_note}
                        </div>
                      ) : (
                        <span className="text-slate-600 italic">No reply yet</span>
                      )}
                    </TableCell>

                    <TableCell className="text-right whitespace-nowrap">
                      <div className="flex items-center justify-end gap-1.5">
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => openUpdate(req)}
                          className="h-8 text-xs font-semibold"
                        >
                          Review & Reply
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => confirmDelete(req)}
                          className="h-8 w-8 p-0 text-slate-400 hover:text-red-400 hover:bg-red-950/40"
                          title="Delete Request"
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* REVIEW & REPLY DIALOG */}
      <Dialog open={isUpdateOpen} onOpenChange={setIsUpdateOpen}>
        <DialogContent className="max-w-2xl bg-slate-900 border-slate-800 text-white">
          <DialogHeader>
            <DialogTitle className="text-lg font-bold flex items-center justify-between pr-6">
              <span>Review: {selectedReq?.subject}</span>
              {selectedReq?.type === "feature_request" ? (
                <Badge className="bg-purple-600 text-white text-xs">Feature Request</Badge>
              ) : (
                <Badge className="bg-indigo-600 text-white text-xs">Support Ticket</Badge>
              )}
            </DialogTitle>
          </DialogHeader>

          <div className="py-3 space-y-4 text-xs">
            {/* Customer Details Box */}
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 p-3 bg-slate-950 border border-slate-800 rounded-lg">
              <div>
                <p className="text-slate-500 font-semibold">Customer</p>
                <p className="font-bold text-white mt-0.5">{selectedReq?.customer?.company_name}</p>
              </div>
              <div>
                <p className="text-slate-500 font-semibold">Contact Email</p>
                <p className="text-white mt-0.5 break-all">{selectedReq?.customer?.contact_email}</p>
              </div>
              <div>
                <p className="text-slate-500 font-semibold">Submission Date</p>
                <p className="text-white mt-0.5">
                  {selectedReq?.created_at ? new Date(selectedReq.created_at).toLocaleString() : "-"}
                </p>
              </div>
            </div>

            {/* Request Description */}
            <div className="space-y-1.5">
              <label className="text-slate-400 font-semibold">Customer Description</label>
              <div className="p-3 bg-slate-950 border border-slate-800 rounded-lg whitespace-pre-wrap leading-relaxed max-h-48 overflow-y-auto text-slate-200">
                {selectedReq?.description}
              </div>
            </div>

            {/* Quick Status Buttons */}
            <div className="space-y-1.5">
              <label className="text-slate-400 font-semibold">Quick Status Actions</label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setStatus("approved")}
                  className={`h-7 text-xs border ${status === "approved" ? "bg-emerald-600 text-white border-emerald-500" : "border-slate-700 text-emerald-400 hover:bg-emerald-950/40"}`}
                >
                  <CheckCircle2 className="w-3 h-3 mr-1" /> Approve
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setStatus("in_progress")}
                  className={`h-7 text-xs border ${status === "in_progress" ? "bg-blue-600 text-white border-blue-500" : "border-slate-700 text-blue-400 hover:bg-blue-950/40"}`}
                >
                  <Clock className="w-3 h-3 mr-1" /> In Progress
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setStatus("resolved")}
                  className={`h-7 text-xs border ${status === "resolved" ? "bg-green-600 text-white border-green-500" : "border-slate-700 text-green-400 hover:bg-green-950/40"}`}
                >
                  <CheckCircle2 className="w-3 h-3 mr-1" /> Resolve
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setStatus("rejected")}
                  className={`h-7 text-xs border ${status === "rejected" ? "bg-red-600 text-white border-red-500" : "border-slate-700 text-red-400 hover:bg-red-950/40"}`}
                >
                  <XCircle className="w-3 h-3 mr-1" /> Reject
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setStatus("closed")}
                  className={`h-7 text-xs border ${status === "closed" ? "bg-slate-700 text-white border-slate-600" : "border-slate-700 text-slate-400 hover:bg-slate-800"}`}
                >
                  Close
                </Button>
              </div>
            </div>

            {/* Status Dropdown */}
            <div className="space-y-1.5">
              <label className="text-slate-400 font-semibold">Current Status</label>
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="w-full h-9 px-3 text-xs bg-slate-950 border border-slate-800 text-white rounded-md"
              >
                <option value="open">Open (Pending Review)</option>
                <option value="in_progress">In Progress</option>
                <option value="approved">Approved (Scheduled / Accepted)</option>
                <option value="resolved">Resolved (Completed)</option>
                <option value="rejected">Rejected (Declined)</option>
                <option value="closed">Closed</option>
              </select>
            </div>

            {/* Admin Reply */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-slate-400 font-semibold">
                  Admin Reply / Team Note
                </label>
                <span className="text-[10px] text-indigo-400 font-medium">
                  Visible to customer in their portal
                </span>
              </div>
              <textarea
                value={adminNote}
                onChange={(e) => setAdminNote(e.target.value)}
                placeholder="Write your response, resolution notes, or ETA for this request..."
                rows={4}
                className="w-full p-2.5 text-xs bg-slate-950 border border-slate-800 text-white rounded-md focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsUpdateOpen(false)}
              className="text-xs border-slate-800"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleUpdate}
              disabled={isSaving}
              className="text-xs bg-indigo-600 hover:bg-indigo-500 text-white font-semibold"
            >
              {isSaving ? "Saving..." : "Save Changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* DELETE CONFIRMATION DIALOG */}
      <Dialog open={isDeleteOpen} onOpenChange={setIsDeleteOpen}>
        <DialogContent className="max-w-md bg-slate-900 border-slate-800 text-white">
          <DialogHeader>
            <DialogTitle className="text-base font-bold text-red-400 flex items-center gap-2">
              <Trash2 className="w-5 h-5" />
              Remove Request #{reqToDelete?.id}
            </DialogTitle>
          </DialogHeader>
          <div className="py-2 text-xs text-slate-300 space-y-2">
            <p>
              Are you sure you want to permanently delete this request from{" "}
              <strong>{reqToDelete?.customer?.company_name}</strong>?
            </p>
            <p className="text-slate-500 italic">
              &quot;{reqToDelete?.subject}&quot;
            </p>
            <p className="text-amber-400/90 text-[11px]">
              This action cannot be undone.
            </p>
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsDeleteOpen(false)}
              className="text-xs border-slate-800"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={handleDelete}
              disabled={isDeleting}
              className="text-xs font-semibold"
            >
              {isDeleting ? "Deleting..." : "Permanently Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
