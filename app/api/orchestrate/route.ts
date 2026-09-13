import { NextResponse } from "next/server";

const AGENTS = ["Researcher", "Coder", "Tester", "Reviewer"] as const;
const schema = {
  type: "object",
  properties: {
    summary: { type: "string" },
    agents: {
      type: "array",
      items: {
        type: "object",
        properties: {
          name: { type: "string", enum: AGENTS },
          assignment: { type: "string" },
          reason: { type: "string" },
        },
        required: ["name", "assignment", "reason"],
        additionalProperties: false,
      },
    },
  },
  required: ["summary", "agents"],
  additionalProperties: false,
};

export async function POST(request: Request) {
  try {
    const key = request.headers.get("x-openrouter-key");
    if (!key?.startsWith("sk-or-")) return NextResponse.json({ error: "Connect an OpenRouter API key first." }, { status: 401 });
    const body = await request.json() as { task?: unknown; model?: unknown };
    if (typeof body.task !== "string" || !body.task.trim() || body.task.length > 4000) {
      return NextResponse.json({ error: "Task must be between 1 and 4000 characters." }, { status: 400 });
    }
    const model = typeof body.model === "string" && body.model.trim() ? body.model.trim() : "~openai/gpt-sol-latest";
    const response = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
        "HTTP-Referer": "https://axiom-agent-control.quara0n.chatgpt.site",
        "X-OpenRouter-Title": "Axiom",
      },
      body: JSON.stringify({
        model,
        temperature: 0.1,
        messages: [
          {
            role: "system",
            content: `You are the Orchestrator for Axiom. Turn the user's goal into a minimal agent workflow.
Choose only agents that genuinely add value:
- Researcher: investigates requirements, domain facts, user needs, or current information.
- Coder: creates or changes software and files.
- Tester: verifies behavior, edge cases, and defects. Select when Coder is selected.
- Reviewer: checks the combined result against the original goal. Always select Reviewer.
Do not perform the work. Give each selected agent one specific, outcome-focused assignment. Preserve the user's language when practical.`,
          },
          { role: "user", content: body.task.trim() },
        ],
        response_format: { type: "json_schema", json_schema: { name: "axiom_workflow", strict: true, schema } },
      }),
    });
    const result = await response.json() as { choices?: Array<{ message?: { content?: string } }>; error?: { message?: string } };
    if (!response.ok) return NextResponse.json({ error: result.error?.message || "OpenRouter request failed." }, { status: response.status });
    const content = result.choices?.[0]?.message?.content;
    if (!content) return NextResponse.json({ error: "The model returned no workflow." }, { status: 502 });
    const parsed = JSON.parse(content) as { summary: string; agents: Array<{ name: string; assignment: string; reason: string }> };
    const seen = new Set<string>();
    const selected = parsed.agents.filter(agent => AGENTS.includes(agent.name as typeof AGENTS[number]) && !seen.has(agent.name) && seen.add(agent.name));
    if (!selected.some(agent => agent.name === "Reviewer")) selected.push({ name: "Reviewer", assignment: "Check the combined result against the original goal.", reason: "Every workflow needs a final quality check." });
    return NextResponse.json({
      summary: parsed.summary,
      model,
      agents: [
        { name: "Orchestrator", assignment: "Understand the goal, select the team and coordinate every handoff.", reason: "Orchestrator is always active." },
        ...selected,
      ],
    });
  } catch {
    return NextResponse.json({ error: "The orchestration response could not be processed." }, { status: 502 });
  }
}
