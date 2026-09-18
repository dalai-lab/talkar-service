import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { AdminShell } from "@/components/layout/AdminShell";

const inter = Inter({
  variable: "--font-sans",
  subsets: ["latin"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Talkar Admin Panel",
  description: "Internal administrative portal for Talkar operations.",
  icons: {
    icon: "/talkar-mark.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable}`}>
      <body className="font-sans antialiased bg-[#fafafa] text-zinc-900 min-h-screen">
        <AdminShell>{children}</AdminShell>
      </body>
    </html>
  );
}
