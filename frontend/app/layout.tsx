import type { Metadata } from "next";
import type { ReactNode } from "react";

import { ToastProvider } from "@/components/Toasts";

import "./globals.css";

export const metadata: Metadata = {
  title: "visorADN — plasmid editor",
  description:
    "View and edit circular DNA sequences with a reversible operation history.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="h-full bg-slate-50 text-slate-900">
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
