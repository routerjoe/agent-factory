import { count } from "drizzle-orm";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { db } from "@/lib/db";
import { agents } from "@/lib/db/schema";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const [{ value: agentCount }] = await db
    .select({ value: count() })
    .from(agents);

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
        <p className="text-muted-foreground">
          Your Foundry-managed Claude agents.
        </p>
      </div>

      {agentCount === 0 ? (
        <Card className="border-dashed">
          <CardHeader>
            <CardTitle>No agents yet</CardTitle>
            <CardDescription>
              Foundry will create agents from a natural-language description in
              Phase 2C. The interview flow isn&apos;t built yet.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button disabled>Create an agent</Button>
          </CardContent>
        </Card>
      ) : (
        <p className="text-sm text-muted-foreground">
          {agentCount} agent{agentCount === 1 ? "" : "s"} configured.
        </p>
      )}
    </div>
  );
}
