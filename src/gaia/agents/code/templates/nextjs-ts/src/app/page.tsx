// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-24">
      <div className="z-10 max-w-5xl w-full items-center justify-center font-mono text-sm">
        <h1 className="text-4xl font-bold mb-4">Welcome to Next.js!</h1>
        <p className="text-lg mb-4">
          This is a full-stack Next.js application with TypeScript, Tailwind CSS, and Prisma ORM.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-8">
          <div className="p-6 border border-gray-200 rounded-lg">
            <h2 className="text-xl font-semibold mb-2">App Router</h2>
            <p className="text-gray-600">Using Next.js 14+ App Router for file-based routing</p>
          </div>
          <div className="p-6 border border-gray-200 rounded-lg">
            <h2 className="text-xl font-semibold mb-2">Prisma ORM</h2>
            <p className="text-gray-600">Type-safe database access with SQLite</p>
          </div>
          <div className="p-6 border border-gray-200 rounded-lg">
            <h2 className="text-xl font-semibold mb-2">TypeScript</h2>
            <p className="text-gray-600">End-to-end type safety</p>
          </div>
          <div className="p-6 border border-gray-200 rounded-lg">
            <h2 className="text-xl font-semibold mb-2">Tailwind CSS</h2>
            <p className="text-gray-600">Utility-first CSS framework</p>
          </div>
        </div>
      </div>
    </main>
  );
}
