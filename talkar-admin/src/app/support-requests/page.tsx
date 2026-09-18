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
  RefreshCw,
  MessageSquare,
  AlertCircle,
  Inbox,
  Filter
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
        return <Badge className="bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100 font-medium">Approved</Badge>;
      case "resolved":
        return <Badge className="bg-green-50 text-green-700 border-green-200 hover:bg-green-100 font-medium">Resolved</Badge>;
      case "in_progress":
        return <Badge className="bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100 font-medium">In Progress</Badge>;
      case "rejected":
        return <Badge className="bg-red-50 text-red-700 border-red-200 hover:bg-red-100 font-medium">Rejected</Badge>;
      case "closed":
        return <Badge className="bg-slate-100 text-slate-700 border-slate-200 hover:bg-slate-200 font-medium">Closed</Badge>;
      default:
        return <Badge className="bg-amber-50 text-amber-800 border-amber-200 hover:bg-amber-100 font-medium">Open</Badge>;
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-2 border-b border-slate-200/70">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 flex items-center gap-2.5">
            <div className="p-2 rounded-lg bg-indigo-50 border border-indigo-100 text-indigo-600">
              <MessageSquare className="w-5 h-5" />
            </div>
            Support & Feature Requests
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Review incoming customer inquiries, manage feature backlogs, and post direct replies.
          </p>
        </div>

        <Button
          variant="outline"
          size="sm"
          onClick={fetchRequests}
          disabled={loading}
          className="h-9 px-3 text-xs bg-white hover:bg-slate-50 border-slate-200 text-slate-700 shadow-none font-medium cursor-pointer"
        >
          <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Metric Cards - Flat & Minimal */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3.5">
        <div className="bg-white border border-slate-200/80 rounded-xl p-4 shadow-none">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-500 font-medium">Total Tickets</span>
            <Inbox className="w-4 h-4 text-slate-400" />
          </div>
          <p className="text-2xl font-bold text-slate-900 mt-2">{totalCount}</p>
        </div>

        <div className="bg-white border border-slate-200/80 rounded-xl p-4 shadow-none">
          <div className="flex items-center justify-between">
            <span className="text-xs text-amber-700 font-medium">Open / Pending</span>
            <span className="w-2 h-2 rounded-full bg-amber-500" />
          </div>
          <p className="text-2xl font-bold text-amber-600 mt-2">{openCount}</p>
        </div>

        <div className="bg-white border border-slate-200/80 rounded-xl p-4 shadow-none">
          <div className="flex items-center justify-between">
            <span className="text-xs text-blue-700 font-medium">In Progress</span>
            <span className="w-2 h-2 rounded-full bg-blue-500" />
          </div>
          <p className="text-2xl font-bold text-blue-600 mt-2">{inProgressCount}</p>
        </div>

        <div className="bg-white border border-slate-200/80 rounded-xl p-4 shadow-none">
          <div className="flex items-center justify-between">
            <span className="text-xs text-emerald-700 font-medium">Approved</span>
            <span className="w-2 h-2 rounded-full bg-emerald-500" />
          </div>
          <p className="text-2xl font-bold text-emerald-600 mt-2">{approvedCount}</p>
        </div>

        <div className="bg-white border border-slate-200/80 rounded-xl p-4 shadow-none col-span-2 sm:col-span-1">
          <div className="flex items-center justify-between">
            <span className="text-xs text-green-700 font-medium">Resolved</span>
            <span className="w-2 h-2 rounded-full bg-green-500" />
          </div>
          <p className="text-2xl font-bold text-green-600 mt-2">{resolvedCount}</p>
        </div>
      </div>

      {/* Filter & Controls Bar */}
      <div className="flex flex-col sm:flex-row gap-3 items-center justify-between bg-white border border-slate-200/80 p-3 rounded-xl shadow-none">
        {/* Search */}
        <div className="relative w-full sm:w-80">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400 pointer-events-none" />
          <Input
            placeholder="Search company, email, or subject..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9 h-9 text-xs bg-slate-50/50 border-slate-200 text-slate-800 placeholder:text-slate-400 focus:bg-white"
          />
        </div>

        {/* Filters */}
        <div className="flex items-center gap-2.5 w-full sm:w-auto overflow-x-auto justify-between sm:justify-end">
          {/* Type Segment Control */}
          <div className="flex items-center bg-slate-100 p-0.5 rounded-lg text-xs border border-slate-200/60">
            <button
              onClick={() => setTypeFilter("all")}
              className={`px-3 py-1 rounded-md transition-all font-medium cursor-pointer ${
                typeFilter === "all"
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              All Types
            </button>
            <button
              onClick={() => setTypeFilter("support")}
              className={`px-3 py-1 rounded-md transition-all font-medium cursor-pointer ${
                typeFilter === "support"
                  ? "bg-white text-indigo-700 shadow-sm font-semibold"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Support
            </button>
            <button
              onClick={() => setTypeFilter("feature_request")}
              className={`px-3 py-1 rounded-md transition-all font-medium cursor-pointer ${
                typeFilter === "feature_request"
                  ? "bg-white text-purple-700 shadow-sm font-semibold"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Features
            </button>
          </div>

          {/* Status Filter */}
          <div className="flex items-center gap-1.5">
            <Filter className="w-3.5 h-3.5 text-slate-400" />
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="h-8 px-2.5 text-xs bg-white border border-slate-200 text-slate-700 rounded-lg focus:outline-none focus:ring-1 focus:ring-slate-300 font-medium cursor-pointer"
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
      </div>

      {/* Main Table */}
      <Card className="bg-white border border-slate-200/80 rounded-xl overflow-hidden shadow-none">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-50/70 border-b border-slate-200/80 hover:bg-slate-50/70">
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Date</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Customer</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Type / Subject</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Status</TableHead>
                <TableHead className="text-slate-600 font-semibold text-xs py-3">Admin Reply</TableHead>
                <TableHead className="text-right text-slate-600 font-semibold text-xs py-3 pr-4">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-12 text-slate-400">
                    <div className="w-5 h-5 border-2 border-indigo-600 border-t-transparent rounded-full animate-spin mx-auto mb-2" />
                    <span className="text-xs">Loading requests...</span>
                  </TableCell>
                </TableRow>
              ) : requests.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-12 text-slate-400 text-xs">
                    No support or feature requests found matching the current filters.
                  </TableCell>
                </TableRow>
              ) : (
                requests.map((req) => (
                  <TableRow key={req.id} className="border-b border-slate-100 hover:bg-slate-50/50 transition-colors">
                    <TableCell className="text-xs text-slate-500 whitespace-nowrap">
                      <div>{req.created_at ? new Date(req.created_at).toLocaleDateString() : "-"}</div>
                      <span className="text-[10px] font-mono text-slate-400">#{req.id}</span>
                    </TableCell>

                    <TableCell className="text-xs max-w-[200px]">
                      <div className="font-semibold text-slate-900 truncate">
                        {req.customer?.company_name || "Unknown Company"}
                      </div>
                      <div className="text-[11px] text-slate-500 truncate">
                        {req.customer?.contact_email}
                      </div>
                      {req.customer?.dograh_org_id && (
                        <span className="inline-block mt-0.5 text-[10px] font-mono text-slate-400">
                          Org #{req.customer.dograh_org_id}
                        </span>
                      )}
                    </TableCell>

                    <TableCell className="max-w-md">
                      <div className="flex items-center gap-1.5 mb-1 flex-wrap">
                        {req.type === "feature_request" ? (
                          <span className="inline-flex items-center gap-1 text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-purple-50 text-purple-700 border border-purple-200">
                            <Sparkles className="w-2.5 h-2.5" /> Feature
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 border border-indigo-200">
                            <HelpCircle className="w-2.5 h-2.5" /> Support
                          </span>
                        )}
                        <span className="font-semibold text-xs text-slate-900">{req.subject}</span>
                      </div>
                      <p className="text-xs text-slate-500 line-clamp-2 leading-relaxed">
                        {req.description}
                      </p>
                    </TableCell>

                    <TableCell className="whitespace-nowrap">
                      {renderStatusBadge(req.status)}
                    </TableCell>

                    <TableCell className="max-w-xs text-xs">
                      {req.admin_note ? (
                        <div className="text-slate-700 line-clamp-2">
                          <span className="text-indigo-600 font-medium">Replied: </span>
                          {req.admin_note}
                        </div>
                      ) : (
                        <span className="text-slate-400 italic text-[11px]">No reply yet</span>
                      )}
                    </TableCell>

                    <TableCell className="text-right whitespace-nowrap pr-4">
                      <div className="flex items-center justify-end gap-1.5">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => openUpdate(req)}
                          className="h-7 px-2.5 text-xs font-medium border-slate-200 bg-white hover:bg-slate-50 text-slate-800 shadow-none cursor-pointer"
                        >
                          Review & Reply
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => confirmDelete(req)}
                          className="h-7 w-7 p-0 text-slate-400 hover:text-red-600 hover:bg-red-50 cursor-pointer"
                          title="Delete Request"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
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
        <DialogContent className="max-w-2xl bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold flex items-center justify-between pr-6 border-b pb-3">
              <span className="truncate">Review: {selectedReq?.subject}</span>
              {selectedReq?.type === "feature_request" ? (
                <Badge className="bg-purple-50 text-purple-700 border-purple-200 text-xs">Feature Request</Badge>
              ) : (
                <Badge className="bg-indigo-50 text-indigo-700 border-indigo-200 text-xs">Support Ticket</Badge>
              )}
            </DialogTitle>
          </DialogHeader>

          <div className="py-2 space-y-4 text-xs">
            {/* Customer Details Box */}
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 p-3.5 bg-slate-50 border border-slate-200/80 rounded-lg">
              <div>
                <p className="text-slate-500 font-medium">Customer</p>
                <p className="font-semibold text-slate-900 mt-0.5">{selectedReq?.customer?.company_name}</p>
              </div>
              <div>
                <p className="text-slate-500 font-medium">Contact Email</p>
                <p className="text-slate-800 mt-0.5 break-all font-mono text-[11px]">{selectedReq?.customer?.contact_email}</p>
              </div>
              <div>
                <p className="text-slate-500 font-medium">Submission Date</p>
                <p className="text-slate-800 mt-0.5">
                  {selectedReq?.created_at ? new Date(selectedReq.created_at).toLocaleString() : "-"}
                </p>
              </div>
            </div>

            {/* Request Description */}
            <div className="space-y-1.5">
              <label className="text-slate-700 font-medium">Customer Description</label>
              <div className="p-3 bg-slate-50 border border-slate-200/80 rounded-lg whitespace-pre-wrap leading-relaxed max-h-40 overflow-y-auto text-slate-800">
                {selectedReq?.description}
              </div>
            </div>

            {/* Quick Status Buttons */}
            <div className="space-y-1.5">
              <label className="text-slate-700 font-medium">Quick Status Change</label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setStatus("approved")}
                  className={`h-7 text-xs border ${status === "approved" ? "bg-emerald-600 text-white border-emerald-600 font-semibold" : "border-slate-200 text-emerald-700 hover:bg-emerald-50"}`}
                >
                  <CheckCircle2 className="w-3 h-3 mr-1" /> Approve
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setStatus("in_progress")}
                  className={`h-7 text-xs border ${status === "in_progress" ? "bg-blue-600 text-white border-blue-600 font-semibold" : "border-slate-200 text-blue-700 hover:bg-blue-50"}`}
                >
                  <Clock className="w-3 h-3 mr-1" /> In Progress
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setStatus("resolved")}
                  className={`h-7 text-xs border ${status === "resolved" ? "bg-green-600 text-white border-green-600 font-semibold" : "border-slate-200 text-green-700 hover:bg-green-50"}`}
                >
                  <CheckCircle2 className="w-3 h-3 mr-1" /> Resolve
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setStatus("rejected")}
                  className={`h-7 text-xs border ${status === "rejected" ? "bg-red-600 text-white border-red-600 font-semibold" : "border-slate-200 text-red-700 hover:bg-red-50"}`}
                >
                  <XCircle className="w-3 h-3 mr-1" /> Reject
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setStatus("closed")}
                  className={`h-7 text-xs border ${status === "closed" ? "bg-slate-800 text-white border-slate-800 font-semibold" : "border-slate-200 text-slate-600 hover:bg-slate-100"}`}
                >
                  Close
                </Button>
              </div>
            </div>

            {/* Status Dropdown */}
            <div className="space-y-1.5">
              <label className="text-slate-700 font-medium">Selected Status</label>
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="w-full h-9 px-3 text-xs bg-white border border-slate-200 text-slate-800 rounded-lg focus:outline-none focus:border-indigo-500"
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
                <label className="text-slate-700 font-medium">
                  Admin Reply / Customer Note
                </label>
                <span className="text-[10px] text-indigo-600 font-medium">
                  Visible to customer in their portal
                </span>
              </div>
              <textarea
                value={adminNote}
                onChange={(e) => setAdminNote(e.target.value)}
                placeholder="Write your response, resolution notes, or ETA for this request..."
                rows={4}
                className="w-full p-3 text-xs bg-white border border-slate-200 text-slate-800 rounded-lg focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
              />
            </div>
          </div>

          <DialogFooter className="gap-2 sm:gap-0 border-t pt-3">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsUpdateOpen(false)}
              className="text-xs border-slate-200"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleUpdate}
              disabled={isSaving}
              className="text-xs bg-indigo-600 hover:bg-indigo-700 text-white font-medium"
            >
              {isSaving ? "Saving..." : "Save Changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* DELETE CONFIRMATION DIALOG */}
      <Dialog open={isDeleteOpen} onOpenChange={setIsDeleteOpen}>
        <DialogContent className="max-w-md bg-white border-slate-200 text-slate-900 rounded-xl">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold text-red-600 flex items-center gap-2">
              <AlertCircle className="w-5 h-5 text-red-600" />
              Remove Request #{reqToDelete?.id}
            </DialogTitle>
          </DialogHeader>
          <div className="py-2 text-xs text-slate-600 space-y-2">
            <p>
              Are you sure you want to permanently delete this request from{" "}
              <strong className="text-slate-900">{reqToDelete?.customer?.company_name}</strong>?
            </p>
            <p className="p-2.5 rounded bg-slate-50 border border-slate-200 text-slate-700 italic">
              &quot;{reqToDelete?.subject}&quot;
            </p>
            <p className="text-red-600 text-[11px] font-medium">
              This action cannot be undone.
            </p>
          </div>
          <DialogFooter className="gap-2 sm:gap-0 border-t pt-3">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsDeleteOpen(false)}
              className="text-xs border-slate-200"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={handleDelete}
              disabled={isDeleting}
              className="text-xs font-medium"
            >
              {isDeleting ? "Deleting..." : "Permanently Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
