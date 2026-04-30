import Link from "next/link";
import { UserButton } from "@clerk/nextjs";

import { requireAllowedUser } from "@/lib/auth";

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  await requireAllowedUser();

  return (
    <div className="flex min-h-svh flex-col">
      <header className="flex items-center justify-between border-b px-6 py-4">
        <Link
          href="/dashboard"
          className="text-lg font-semibold tracking-tight"
        >
          Foundry
        </Link>
        <div className="flex items-center gap-4">
          <UserButton />
        </div>
      </header>
      <main className="flex-1 px-6 py-8">{children}</main>
    </div>
  );
}
