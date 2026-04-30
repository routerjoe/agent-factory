CREATE TABLE "agents" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"anthropic_agent_id" text NOT NULL,
	"anthropic_environment_id" text NOT NULL,
	"name" text NOT NULL,
	"description" text,
	"system_prompt" text NOT NULL,
	"tools" jsonb NOT NULL,
	"schedule" text,
	"status" text DEFAULT 'draft' NOT NULL,
	"spec" jsonb NOT NULL,
	"parent_session_id" text,
	"created_at" timestamp with time zone DEFAULT now(),
	"updated_at" timestamp with time zone DEFAULT now(),
	CONSTRAINT "agents_anthropic_agent_id_unique" UNIQUE("anthropic_agent_id")
);
--> statement-breakpoint
CREATE TABLE "evals" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"agent_id" uuid NOT NULL,
	"test_cases" jsonb NOT NULL,
	"results" jsonb NOT NULL,
	"passed" integer NOT NULL,
	"failed" integer NOT NULL,
	"pass_rate" numeric NOT NULL,
	"ran_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "runs" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"agent_id" uuid NOT NULL,
	"anthropic_run_id" text NOT NULL,
	"status" text NOT NULL,
	"tokens_used" integer,
	"cost_cents" integer,
	"duration_ms" integer,
	"error" text,
	"ran_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "sessions" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"anthropic_session_id" text NOT NULL,
	"initial_request" text NOT NULL,
	"status" text DEFAULT 'interviewing' NOT NULL,
	"resulting_agent_id" uuid,
	"transcript" jsonb,
	"created_at" timestamp with time zone DEFAULT now(),
	"updated_at" timestamp with time zone DEFAULT now(),
	CONSTRAINT "sessions_anthropic_session_id_unique" UNIQUE("anthropic_session_id")
);
--> statement-breakpoint
CREATE TABLE "templates" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" text NOT NULL,
	"archetype" text NOT NULL,
	"description" text,
	"system_prompt_template" text NOT NULL,
	"default_tools" jsonb NOT NULL,
	"default_schedule" text,
	"example_use_cases" text[],
	"created_at" timestamp with time zone DEFAULT now(),
	CONSTRAINT "templates_name_unique" UNIQUE("name")
);
--> statement-breakpoint
CREATE TABLE "tool_use_events" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"anthropic_session_id" text NOT NULL,
	"custom_tool_use_id" text NOT NULL,
	"tool_name" text NOT NULL,
	"input" jsonb NOT NULL,
	"status" text DEFAULT 'received' NOT NULL,
	"result" jsonb,
	"posted_at" timestamp with time zone,
	"error" text,
	"created_at" timestamp with time zone DEFAULT now(),
	"updated_at" timestamp with time zone DEFAULT now(),
	CONSTRAINT "tool_use_events_session_tool_use_unique" UNIQUE("anthropic_session_id","custom_tool_use_id")
);
--> statement-breakpoint
ALTER TABLE "evals" ADD CONSTRAINT "evals_agent_id_agents_id_fk" FOREIGN KEY ("agent_id") REFERENCES "public"."agents"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "runs" ADD CONSTRAINT "runs_agent_id_agents_id_fk" FOREIGN KEY ("agent_id") REFERENCES "public"."agents"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "sessions" ADD CONSTRAINT "sessions_resulting_agent_id_agents_id_fk" FOREIGN KEY ("resulting_agent_id") REFERENCES "public"."agents"("id") ON DELETE no action ON UPDATE no action;