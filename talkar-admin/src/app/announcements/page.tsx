"use client";

import React, { useEffect, useState, useMemo } from "react";
import { adminFetch } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import { 
  Megaphone, 
  Send, 
  Calendar, 
  Clock, 
  Bell, 
  Mail, 
  Search, 
  Plus, 
  CheckCircle2, 
  AlertTriangle, 
  XCircle, 
  Eye, 
  Ban, 
  Sparkles,
  Layers,
  Wrench,
  AlertCircle
} from "lucide-react";

interface AnnouncementItem {
  id: number;
  title: string;
  body: string;
  type: string;
  channels: string[];
  status: string;
  scheduled_for: string | null;
  sent_at: string | null;
  created_at: string | null;
  recipients_count: number;
  author_name: string;
}

export default function AnnouncementsPage() {
  const [announcements, setAnnouncements] = useState<AnnouncementItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  // Composer Modal
  const [isComposerOpen, setIsComposerOpen] = useState(false);
  const [composeTitle, setComposeTitle] = useState("");
  const [composeBody, setComposeBody] = useState("");
  const [composeType, setComposeType] = useState("general");
  const [sendInApp, setSendInApp] = useState(true);
  const [sendEmail, setSendEmail] = useState(true);
  const [isScheduled, setIsScheduled] = useState(false);
  const [scheduledDate, setScheduledDate] = useState("");
  const [scheduledTime, setScheduledTime] = useState("");
  const [activeTab, setActiveTab] = useState<"edit" | "preview">("edit");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // View Modal
  const [viewItem, setViewItem] = useState<AnnouncementItem | null>(null);

  // Cancel Modal
  const [cancelItem, setCancelItem] = useState<AnnouncementItem | null>(null);
  const [isCancelling, setIsCancelling] = useState(false);

  const fetchAnnouncements = async () => {
    try {
      setLoading(true);
      const res = await adminFetch("/admin/announcements");
      if (res.ok) {
        const data = await res.json();
        setAnnouncements(Array.isArray(data) ? data : []);
      }
    } catch (e) {
      console.error("Failed to fetch announcements:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAnnouncements();
  }, []);

  // Stats calculation
  const stats = useMemo(() => {
    const totalSent = announcements.filter(a => a.status === "sent").length;
    const scheduled = announcements.filter(a => a.status === "scheduled").length;
    const totalRecipients = announcements.reduce((acc, a) => acc + (a.recipients_count || 0), 0);
    const emailBroadcasts = announcements.filter(a => a.channels?.includes("email") && a.status === "sent").length;

    return { totalSent, scheduled, totalRecipients, emailBroadcasts };
  }, [announcements]);

  const filteredAnnouncements = useMemo(() => {
    return announcements.filter(item => {
      if (statusFilter !== "all" && item.status !== statusFilter) return false;
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        return (
          item.title.toLowerCase().includes(q) ||
          item.body.toLowerCase().includes(q) ||
          item.type.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [announcements, statusFilter, searchQuery]);

  const handleOpenComposer = () => {
    setComposeTitle("");
    setComposeBody("");
    setComposeType("general");
    setSendInApp(true);
    setSendEmail(true);
    setIsScheduled(false);
    
    // Default scheduled time: tomorrow at 10:00 AM IST
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    const dateStr = tomorrow.toISOString().split("T")[0];
    setScheduledDate(dateStr);
    setScheduledTime("10:00");
    setActiveTab("edit");
    setIsComposerOpen(true);
  };

  const handleSendAnnouncement = async () => {
    if (!composeTitle.trim()) {
      alert("Please enter a title for the announcement.");
      return;
    }
    if (!composeBody.trim()) {
      alert("Please enter the message body.");
      return;
    }
    if (!sendInApp && !sendEmail) {
      alert("Please select at least one delivery channel (In-App or Email).");
      return;
    }

    let scheduledIso: string | null = null;
    if (isScheduled) {
      if (!scheduledDate || !scheduledTime) {
        alert("Please specify a date and time for scheduled delivery.");
        return;
      }
      const combined = new Date(`${scheduledDate}T${scheduledTime}:00`);
      if (isNaN(combined.getTime())) {
        alert("Invalid scheduled date/time.");
        return;
      }
      if (combined.getTime() <= Date.now()) {
        alert("Scheduled time must be in the future.");
        return;
      }
      scheduledIso = combined.toISOString();
    } else {
      if (!confirm("Are you sure you want to broadcast this announcement to all eligible customers immediately?")) {
        return;
      }
    }

    try {
      setIsSubmitting(true);
      const res = await adminFetch("/admin/announcements", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: composeTitle.trim(),
          body: composeBody.trim(),
          type: composeType,
          send_in_app: sendInApp,
          send_email: sendEmail,
          is_scheduled: isScheduled,
          scheduled_for: scheduledIso
        })
      });

      if (res.ok) {
        const data = await res.json();
        alert(data.message || "Announcement submitted successfully!");
        setIsComposerOpen(false);
        fetchAnnouncements();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`Failed: ${err.detail || "Server error"}`);
      }
    } catch (e) {
      console.error(e);
      alert("Network error sending announcement.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCancelAnnouncement = async () => {
    if (!cancelItem) return;
    try {
      setIsCancelling(true);
      const res = await adminFetch(`/admin/announcements/${cancelItem.id}/cancel`, {
        method: "POST"
      });
      if (res.ok) {
        setCancelItem(null);
        fetchAnnouncements();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`Failed to cancel: ${err.detail || "Server error"}`);
      }
    } catch (e) {
      console.error(e);
      alert("Network error cancelling announcement.");
    } finally {
      setIsCancelling(false);
    }
  };

  const getTypeBadge = (type: string) => {
    switch (type) {
      case "critical":
        return <Badge className="bg-rose-50 text-rose-700 border-rose-200">Critical Alert</Badge>;
      case "maintenance":
        return <Badge className="bg-amber-50 text-amber-700 border-amber-200">Maintenance</Badge>;
      case "update":
        return <Badge className="bg-blue-50 text-blue-700 border-blue-200">Feature Update</Badge>;
      default:
        return <Badge className="bg-slate-100 text-slate-700 border-slate-200">General</Badge>;
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "sent":
        return <Badge className="bg-emerald-50 text-emerald-700 border-emerald-200">✓ Sent</Badge>;
      case "scheduled":
        return <Badge className="bg-amber-50 text-amber-700 border-amber-200">⏳ Scheduled</Badge>;
      case "cancelled":
        return <Badge className="bg-slate-100 text-slate-500 border-slate-200">Cancelled</Badge>;
      default:
        return <Badge variant="outline">{status}</Badge>;
    }
  };

  const formatDateTime = (iso?: string | null) => {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      return d.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
        hour12: true
      });
    } catch {
      return iso;
    }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header & Primary CTA */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-4 border-b border-slate-200/80">
        <div>
          <div className="flex items-center gap-2.5">
            <div className="h-9 w-9 rounded-lg bg-[#fe6905]/10 border border-[#fe6905]/20 flex items-center justify-center text-[#fe6905]">
              <Megaphone className="w-5 h-5" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight text-slate-900">Broadcast Announcements</h1>
              <p className="text-xs text-slate-500">Deliver rich in-app notices and branded email announcements to all customers</p>
            </div>
          </div>
        </div>

        <Button
          onClick={handleOpenComposer}
          className="h-9 text-xs bg-[#fe6905] hover:bg-[#e55e04] text-white font-semibold cursor-pointer flex items-center gap-1.5 shadow-sm"
        >
          <Plus className="w-4 h-4" /> New Announcement
        </Button>
      </div>

      {/* High-Level Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card className="bg-white border-slate-200/80 shadow-none">
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium text-slate-500 uppercase tracking-wider">Total Broadcasts</p>
              <h3 className="text-2xl font-bold text-slate-900 mt-1">{stats.totalSent}</h3>
            </div>
            <div className="h-10 w-10 rounded-full bg-emerald-50 text-emerald-600 flex items-center justify-center">
              <CheckCircle2 className="w-5 h-5" />
            </div>
          </CardContent>
        </Card>

        <Card className="bg-white border-slate-200/80 shadow-none">
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium text-slate-500 uppercase tracking-wider">Scheduled Ahead</p>
              <h3 className="text-2xl font-bold text-slate-900 mt-1">{stats.scheduled}</h3>
            </div>
            <div className="h-10 w-10 rounded-full bg-amber-50 text-amber-600 flex items-center justify-center">
              <Calendar className="w-5 h-5" />
            </div>
          </CardContent>
        </Card>

        <Card className="bg-white border-slate-200/80 shadow-none">
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium text-slate-500 uppercase tracking-wider">In-App Deliveries</p>
              <h3 className="text-2xl font-bold text-slate-900 mt-1">{stats.totalRecipients}</h3>
            </div>
            <div className="h-10 w-10 rounded-full bg-blue-50 text-blue-600 flex items-center justify-center">
              <Bell className="w-5 h-5" />
            </div>
          </CardContent>
        </Card>

        <Card className="bg-white border-slate-200/80 shadow-none">
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium text-slate-500 uppercase tracking-wider">Email Blasts</p>
              <h3 className="text-2xl font-bold text-slate-900 mt-1">{stats.emailBroadcasts}</h3>
            </div>
            <div className="h-10 w-10 rounded-full bg-purple-50 text-purple-600 flex items-center justify-center">
              <Mail className="w-5 h-5" />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Filter & Search Bar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 bg-white border border-slate-200 p-3 rounded-xl">
        <div className="relative w-full sm:w-80">
          <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input
            type="text"
            placeholder="Search broadcasts..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-8 text-xs h-8 bg-slate-50/50 border-slate-200"
          />
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto">
          {["all", "sent", "scheduled", "cancelled"].map((st) => (
            <Button
              key={st}
              size="sm"
              variant={statusFilter === st ? "default" : "outline"}
              onClick={() => setStatusFilter(st)}
              className={`h-7 text-xs capitalize cursor-pointer ${
                statusFilter === st 
                  ? "bg-slate-900 text-white" 
                  : "border-slate-200 text-slate-600 hover:text-slate-900"
              }`}
            >
              {st}
            </Button>
          ))}
        </div>
      </div>

      {/* Announcements Table */}
      <Card className="bg-white border-slate-200/80 rounded-xl overflow-hidden shadow-none">
        <Table>
          <TableHeader>
            <TableRow className="bg-slate-50/70 border-b border-slate-200 text-[11px] text-slate-500 font-semibold uppercase">
              <TableHead className="w-[300px]">Announcement</TableHead>
              <TableHead>Category</TableHead>
              <TableHead>Channels</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Delivery Date</TableHead>
              <TableHead>Recipients</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={7} className="h-32 text-center text-xs text-slate-400">
                  Loading announcements...
                </TableCell>
              </TableRow>
            ) : filteredAnnouncements.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="h-32 text-center text-xs text-slate-400">
                  No announcements found. Click "New Announcement" to compose one.
                </TableCell>
              </TableRow>
            ) : (
              filteredAnnouncements.map((ann) => (
                <TableRow key={ann.id} className="hover:bg-slate-50/60 border-b border-slate-100 text-xs">
                  <TableCell>
                    <div>
                      <p className="font-semibold text-slate-900 text-xs">{ann.title}</p>
                      <p className="text-[11px] text-slate-500 line-clamp-1 mt-0.5">{ann.body}</p>
                    </div>
                  </TableCell>
                  <TableCell>{getTypeBadge(ann.type)}</TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      {ann.channels?.includes("in_app") && (
                        <span className="inline-flex items-center gap-1 text-[10px] font-medium bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded border border-blue-200">
                          <Bell className="w-2.5 h-2.5" /> In-App
                        </span>
                      )}
                      {ann.channels?.includes("email") && (
                        <span className="inline-flex items-center gap-1 text-[10px] font-medium bg-purple-50 text-purple-700 px-1.5 py-0.5 rounded border border-purple-200">
                          <Mail className="w-2.5 h-2.5" /> Email
                        </span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>{getStatusBadge(ann.status)}</TableCell>
                  <TableCell className="text-slate-600 font-mono text-[11px]">
                    {ann.status === "scheduled" ? (
                      <span className="text-amber-700 font-medium">Due: {formatDateTime(ann.scheduled_for)}</span>
                    ) : (
                      formatDateTime(ann.sent_at || ann.created_at)
                    )}
                  </TableCell>
                  <TableCell className="font-medium text-slate-700">
                    {ann.status === "sent" ? `${ann.recipients_count} customers` : "All active"}
                  </TableCell>
                  <TableCell className="text-right space-x-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setViewItem(ann)}
                      className="h-7 px-2 text-xs text-slate-600 hover:text-slate-900 cursor-pointer"
                      title="View Details"
                    >
                      <Eye className="w-3.5 h-3.5 mr-1" /> View
                    </Button>
                    {ann.status === "scheduled" && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setCancelItem(ann)}
                        className="h-7 px-2 text-xs text-rose-600 hover:text-rose-700 hover:bg-rose-50 cursor-pointer"
                        title="Cancel Delivery"
                      >
                        <Ban className="w-3.5 h-3.5 mr-1" /> Cancel
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>

      {/* Composer Modal */}
      <Dialog open={isComposerOpen} onOpenChange={setIsComposerOpen}>
        <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="text-base font-bold text-slate-900 flex items-center gap-2">
              <Megaphone className="w-4 h-4 text-[#fe6905]" />
              Compose Broadcast Announcement
            </DialogTitle>
            <DialogDescription className="text-xs text-slate-500">
              Publish rich announcements to all active customers via notification and email.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2 text-xs">
            {/* Title & Category */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="sm:col-span-2">
                <Label className="text-xs font-semibold text-slate-700 mb-1 block">Announcement Title *</Label>
                <Input
                  placeholder="e.g. Scheduled Maintenance Notice or Platform Update"
                  value={composeTitle}
                  onChange={(e) => setComposeTitle(e.target.value)}
                  className="h-8 text-xs bg-white border-slate-200"
                />
              </div>
              <div>
                <Label className="text-xs font-semibold text-slate-700 mb-1 block">Category</Label>
                <select
                  value={composeType}
                  onChange={(e) => setComposeType(e.target.value)}
                  className="w-full h-8 text-xs rounded-md border border-slate-200 bg-white px-2 text-slate-800 font-medium focus:outline-none focus:ring-1 focus:ring-slate-400"
                >
                  <option value="general">General Notice</option>
                  <option value="update">Product / Feature Update</option>
                  <option value="maintenance">Maintenance</option>
                  <option value="critical">Critical / Urgent</option>
                </select>
              </div>
            </div>

            {/* Delivery Channels */}
            <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg space-y-2">
              <Label className="text-xs font-semibold text-slate-700 block">Delivery Channels</Label>
              <div className="flex flex-wrap gap-4 pt-1">
                <label className="flex items-center gap-2 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={sendInApp}
                    onChange={(e) => setSendInApp(e.target.checked)}
                    className="rounded border-slate-300 text-[#fe6905] focus:ring-[#fe6905]"
                  />
                  <span className="text-xs text-slate-700 flex items-center gap-1">
                    <Bell className="w-3.5 h-3.5 text-blue-600" /> In-App Notification (Bell)
                  </span>
                </label>

                <label className="flex items-center gap-2 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={sendEmail}
                    onChange={(e) => setSendEmail(e.target.checked)}
                    className="rounded border-slate-300 text-[#fe6905] focus:ring-[#fe6905]"
                  />
                  <span className="text-xs text-slate-700 flex items-center gap-1">
                    <Mail className="w-3.5 h-3.5 text-purple-600" /> Email Broadcast (Talkar Themed)
                  </span>
                </label>
              </div>
            </div>

            {/* Scheduled Delivery Toggle */}
            <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-semibold text-slate-800 flex items-center gap-1.5">
                    <Calendar className="w-3.5 h-3.5 text-amber-600" /> Schedule for Later
                  </p>
                  <p className="text-[11px] text-slate-500">Automatically broadcast at a future date and time</p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input 
                    type="checkbox" 
                    checked={isScheduled} 
                    onChange={(e) => setIsScheduled(e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-9 h-5 bg-slate-300 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-[#fe6905]"></div>
                </label>
              </div>

              {isScheduled && (
                <div className="grid grid-cols-2 gap-3 pt-2 border-t border-slate-200/80">
                  <div>
                    <Label className="text-[11px] text-slate-600 mb-1 block">Delivery Date</Label>
                    <Input
                      type="date"
                      value={scheduledDate}
                      onChange={(e) => setScheduledDate(e.target.value)}
                      className="h-8 text-xs bg-white border-slate-200"
                    />
                  </div>
                  <div>
                    <Label className="text-[11px] text-slate-600 mb-1 block">Delivery Time (Local)</Label>
                    <Input
                      type="time"
                      value={scheduledTime}
                      onChange={(e) => setScheduledTime(e.target.value)}
                      className="h-8 text-xs bg-white border-slate-200"
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Write vs Preview Tabs */}
            <div>
              <div className="flex items-center justify-between border-b border-slate-200 pb-1 mb-2">
                <Label className="text-xs font-semibold text-slate-700">Message Body *</Label>
                <div className="flex items-center gap-1">
                  <Button
                    type="button"
                    size="sm"
                    variant={activeTab === "edit" ? "secondary" : "ghost"}
                    onClick={() => setActiveTab("edit")}
                    className="h-6 text-[11px] px-2"
                  >
                    Edit
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={activeTab === "preview" ? "secondary" : "ghost"}
                    onClick={() => setActiveTab("preview")}
                    className="h-6 text-[11px] px-2"
                  >
                    Live Preview
                  </Button>
                </div>
              </div>

              {activeTab === "edit" ? (
                <div>
                  <textarea
                    rows={6}
                    placeholder="Type your announcement. You can use markdown like bullet points (- item), headers (## Heading), or custom links..."
                    value={composeBody}
                    onChange={(e) => setComposeBody(e.target.value)}
                    className="w-full text-xs rounded-lg border border-slate-200 p-2.5 focus:outline-none focus:ring-1 focus:ring-slate-400 font-sans leading-relaxed resize-y bg-white"
                  />
                  <p className="text-[10px] text-slate-400 mt-1">
                    Tip: Bullet points starting with "- " or "* " and headings are automatically styled in both notifications and emails.
                  </p>
                </div>
              ) : (
                <div className="border border-slate-200 rounded-lg p-4 bg-slate-50/50 space-y-3">
                  <div className="flex items-center gap-2">
                    {getTypeBadge(composeType)}
                    <span className="font-semibold text-slate-900 text-sm">{composeTitle || "Announcement Title"}</span>
                  </div>
                  <div className="text-xs text-slate-700 whitespace-pre-wrap leading-relaxed">
                    {composeBody || <span className="text-slate-400 italic">No message content typed yet.</span>}
                  </div>
                </div>
              )}
            </div>
          </div>

          <DialogFooter className="pt-2 border-t border-slate-100 flex items-center justify-between sm:justify-between">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsComposerOpen(false)}
              className="text-xs h-8 border-slate-200"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleSendAnnouncement}
              disabled={isSubmitting}
              className="text-xs h-8 bg-[#fe6905] hover:bg-[#e55e04] text-white font-medium cursor-pointer flex items-center gap-1.5"
            >
              {isSubmitting ? (
                "Processing..."
              ) : isScheduled ? (
                <>
                  <Calendar className="w-3.5 h-3.5" /> Schedule Announcement
                </>
              ) : (
                <>
                  <Send className="w-3.5 h-3.5" /> Broadcast Now
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* View Details Modal */}
      <Dialog open={!!viewItem} onOpenChange={(open) => !open && setViewItem(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <div className="flex items-center gap-2 mb-1">
              {viewItem && getTypeBadge(viewItem.type)}
              {viewItem && getStatusBadge(viewItem.status)}
            </div>
            <DialogTitle className="text-base font-bold text-slate-900">
              {viewItem?.title}
            </DialogTitle>
            <DialogDescription className="text-[11px] text-slate-500">
              Sent by {viewItem?.author_name} • {viewItem?.status === "scheduled" ? `Scheduled for ${formatDateTime(viewItem?.scheduled_for)}` : `Delivered ${formatDateTime(viewItem?.sent_at)}`}
            </DialogDescription>
          </DialogHeader>

          <div className="py-3 space-y-3 text-xs">
            <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg text-slate-800 whitespace-pre-wrap leading-relaxed">
              {viewItem?.body}
            </div>

            <div className="grid grid-cols-2 gap-2 text-[11px] text-slate-500 pt-1">
              <div>
                <span className="font-semibold text-slate-700">Channels:</span>{" "}
                {viewItem?.channels?.join(", ") || "None"}
              </div>
              <div>
                <span className="font-semibold text-slate-700">Recipients:</span>{" "}
                {viewItem?.recipients_count ?? 0} customers
              </div>
            </div>
          </div>

          <DialogFooter>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setViewItem(null)}
              className="text-xs h-8 border-slate-200"
            >
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Cancel Confirmation Modal */}
      <Dialog open={!!cancelItem} onOpenChange={(open) => !open && setCancelItem(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-bold text-slate-900 flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-rose-600" /> Cancel Scheduled Announcement?
            </DialogTitle>
            <DialogDescription className="text-xs text-slate-500">
              Are you sure you want to cancel &ldquo;{cancelItem?.title}&rdquo;? It will not be delivered to customers.
            </DialogDescription>
          </DialogHeader>

          <DialogFooter className="pt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCancelItem(null)}
              className="text-xs h-8"
            >
              Keep Scheduled
            </Button>
            <Button
              size="sm"
              onClick={handleCancelAnnouncement}
              disabled={isCancelling}
              className="text-xs h-8 bg-rose-600 hover:bg-rose-700 text-white font-medium"
            >
              {isCancelling ? "Cancelling..." : "Yes, Cancel Announcement"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

