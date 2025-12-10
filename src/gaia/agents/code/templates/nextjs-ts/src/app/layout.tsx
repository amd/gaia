// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Next.js App",
  description: "Full-stack Next.js application with Prisma ORM",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
