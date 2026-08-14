"use client";
import { adminFetch } from "@/lib/api";

import React, { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";


export default function BuildQueuePage() {
  const [customers, setCustomers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchQueue();
  }, []);

  const fetchQueue = async () => {
    try {
      const res = await adminFetch(`/admin/build-queue`);
      if (res.ok) {
        const data = await res.json();
        setCustomers(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleMarkReady = async (customerId: number) => {
    try {
      const res = await adminFetch(`/admin/build-queue/${customerId}/ready`, {
        method: "PATCH"
      });
      if (res.ok) {
        fetchQueue();
      }
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Agent Build Queue</h2>
          <p className="text-muted-foreground mt-2">
            Customers whose payments have cleared and are waiting for manual agent setup.
          </p>
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Customer</TableHead>
                <TableHead>Use Case</TableHead>
                <TableHead>Language</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8">Loading queue...</TableCell>
                </TableRow>
              ) : customers.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">No customers in build queue.</TableCell>
                </TableRow>
              ) : (
                customers.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="font-medium">
                      {c.company_name}
                      <br />
                      <span className="text-xs text-muted-foreground">{c.contact_name}</span>
                    </TableCell>
                    <TableCell className="max-w-xs truncate">
                      {c.onboarding_form?.useCaseType || "N/A"}
                    </TableCell>
                    <TableCell>
                      {c.onboarding_form?.languages || "N/A"}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="bg-blue-100 text-blue-800 hover:bg-blue-100">
                        Agent Building
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right space-x-2">
                      <Button variant="outline" size="sm" onClick={async () => {
                        try {
                          const res = await adminFetch(`/admin/build-queue/${c.id}/assign`, { method: "PATCH" });
                          if (res.ok) {
                            const data = await res.json();
                            if (data.access_token) {
                              // Open Dograh impersonation route in a new tab
                              const dograhUrl = process.env.NEXT_PUBLIC_DOGRAH_URL || "https://talkar.in";
                              window.open(`${dograhUrl}/auth/impersonate?token=${data.access_token}&refresh_token=${data.refresh_token || ''}`, "_blank");
                            } else {
                              alert("Assigned, but no magic link could be generated.");
                            }
                            fetchQueue();
                          } else {
                            const error = await res.json();
                            alert(`Failed to assign: ${error.detail || res.statusText}`);
                          }
                        } catch (e) {
                          alert("Network error. Could not assign.");
                        }
                      }}>Impersonate & Build</Button>
                      <Button size="sm" onClick={() => handleMarkReady(c.id)}>Mark as Ready</Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
