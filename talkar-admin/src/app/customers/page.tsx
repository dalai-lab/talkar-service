"use client";
import { adminFetch } from "@/lib/api";

import React, { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useRouter } from "next/navigation";


export default function CustomersPage() {
  const [customers, setCustomers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const router = useRouter();

  useEffect(() => {
    fetchCustomers();
  }, []);

  const fetchCustomers = async () => {
    try {
      const res = await adminFetch(`/admin/customers`);
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

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Customer Directory</h2>
          <p className="text-muted-foreground mt-2">
            View and manage all Talkar customers.
          </p>
        </div>
      </div>

      <div className="flex items-center space-x-2 max-w-sm">
        <Input 
          placeholder="Search by company or email..." 
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>ID</TableHead>
                <TableHead>Company</TableHead>
                <TableHead>Contact</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8">Loading customers...</TableCell>
                </TableRow>
              ) : customers.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">No customers found.</TableCell>
                </TableRow>
              ) : (
                customers.filter(c => 
                  c.company_name?.toLowerCase().includes(search.toLowerCase()) || 
                  c.contact_email?.toLowerCase().includes(search.toLowerCase())
                ).map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="font-mono text-muted-foreground">#{c.id}</TableCell>
                    <TableCell className="font-medium">{c.company_name}</TableCell>
                    <TableCell>
                      {c.contact_name}
                      <br />
                      <span className="text-xs text-muted-foreground">{c.contact_email}</span>
                    </TableCell>
                    <TableCell>
                      <Badge variant={c.status === 'active' ? 'default' : 'secondary'}>
                        {c.status.replace("_", " ")}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button variant="outline" size="sm" onClick={() => router.push(`/customers/${c.id}`)}>Manage</Button>
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
