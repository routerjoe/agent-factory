import {
  integer,
  jsonb,
  numeric,
  pgTable,
  text,
  timestamp,
  unique,
  uuid,
} from "drizzle-orm/pg-core";

export const agents = pgTable("agents", {
  id: uuid("id").primaryKey().defaultRandom(),
  anthropicAgentId: text("anthropic_agent_id").notNull().unique(),
  anthropicEnvironmentId: text("anthropic_environment_id").notNull(),
  name: text("name").notNull(),
  description: text("description"),
  systemPrompt: text("system_prompt").notNull(),
  tools: jsonb("tools").notNull(),
  schedule: text("schedule"),
  status: text("status").notNull().default("draft"),
  spec: jsonb("spec").notNull(),
  parentSessionId: text("parent_session_id"),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow(),
});

export const sessions = pgTable("sessions", {
  id: uuid("id").primaryKey().defaultRandom(),
  anthropicSessionId: text("anthropic_session_id").notNull().unique(),
  initialRequest: text("initial_request").notNull(),
  status: text("status").notNull().default("interviewing"),
  resultingAgentId: uuid("resulting_agent_id").references(() => agents.id),
  transcript: jsonb("transcript"),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow(),
});

export const toolUseEvents = pgTable(
  "tool_use_events",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    anthropicSessionId: text("anthropic_session_id").notNull(),
    customToolUseId: text("custom_tool_use_id").notNull(),
    toolName: text("tool_name").notNull(),
    input: jsonb("input").notNull(),
    status: text("status").notNull().default("received"),
    result: jsonb("result"),
    postedAt: timestamp("posted_at", { withTimezone: true }),
    error: text("error"),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow(),
  },
  (table) => [
    unique("tool_use_events_session_tool_use_unique").on(
      table.anthropicSessionId,
      table.customToolUseId,
    ),
  ],
);

export const evals = pgTable("evals", {
  id: uuid("id").primaryKey().defaultRandom(),
  agentId: uuid("agent_id")
    .notNull()
    .references(() => agents.id),
  testCases: jsonb("test_cases").notNull(),
  results: jsonb("results").notNull(),
  passed: integer("passed").notNull(),
  failed: integer("failed").notNull(),
  passRate: numeric("pass_rate").notNull(),
  ranAt: timestamp("ran_at", { withTimezone: true }).defaultNow(),
});

export const templates = pgTable("templates", {
  id: uuid("id").primaryKey().defaultRandom(),
  name: text("name").notNull().unique(),
  archetype: text("archetype").notNull(),
  description: text("description"),
  systemPromptTemplate: text("system_prompt_template").notNull(),
  defaultTools: jsonb("default_tools").notNull(),
  defaultSchedule: text("default_schedule"),
  exampleUseCases: text("example_use_cases").array(),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

export const runs = pgTable("runs", {
  id: uuid("id").primaryKey().defaultRandom(),
  agentId: uuid("agent_id")
    .notNull()
    .references(() => agents.id),
  anthropicRunId: text("anthropic_run_id").notNull(),
  status: text("status").notNull(),
  tokensUsed: integer("tokens_used"),
  costCents: integer("cost_cents"),
  durationMs: integer("duration_ms"),
  error: text("error"),
  ranAt: timestamp("ran_at", { withTimezone: true }).defaultNow(),
});

export type Agent = typeof agents.$inferSelect;
export type NewAgent = typeof agents.$inferInsert;
export type Session = typeof sessions.$inferSelect;
export type NewSession = typeof sessions.$inferInsert;
export type ToolUseEvent = typeof toolUseEvents.$inferSelect;
export type NewToolUseEvent = typeof toolUseEvents.$inferInsert;
export type Eval = typeof evals.$inferSelect;
export type NewEval = typeof evals.$inferInsert;
export type Template = typeof templates.$inferSelect;
export type NewTemplate = typeof templates.$inferInsert;
export type Run = typeof runs.$inferSelect;
export type NewRun = typeof runs.$inferInsert;
