"use client";

import { useEffect, useRef, useState } from "react";
import { Activity, Bot, Braces, Check, ChevronDown, CirclePlus, FlaskConical, GitBranch, Network, Play, Search, Settings, ShieldCheck, Sparkles } from "lucide-react";

declare global {
  interface Document {
    modelContext?: {
      registerTool: (tool: {
        name: string; title: string; description: string; inputSchema: object;
        annotations: { readOnlyHint: boolean; untrustedContentHint: boolean };
        execute: (input: unknown) => unknown;
      }, options?: { signal?: AbortSignal }) => void | Promise<void>;
    };
  }
}

type AgentState = "idle" | "working" | "done" | "skipped";
const agents = [
  { name: "Orchestrator", role: "Plans and delegates", icon: Network },
  { name: "Researcher", role: "Finds context", icon: Search },
  { name: "Coder", role: "Builds the solution", icon: Braces },
  { name: "Tester", role: "Checks the work", icon: FlaskConical },
  { name: "Reviewer", role: "Final quality pass", icon: ShieldCheck },
];
type WorkStep = { agent: string; text: string; assignment: string };
function decideWorkflow(prompt: string): WorkStep[] {
  const value = prompt.toLowerCase();
  const build = /(build|create|make|code|implement|app|website|dashboard|game|spill|lag|bygg|kode|utvikl)/.test(value);
  const research = build || /(research|find|compare|analyse|analyze|undersøk|finn|sammenlign|sjakk)/.test(value);
  const plan: WorkStep[] = [{ agent: "Orchestrator", text: "Read the goal and assembled the right team", assignment: "Define the outcome, split the work and coordinate every handoff." }];
  if (research) plan.push({ agent: "Researcher", text: "Mapped requirements and useful context", assignment: build ? "Research the domain, expected features and constraints before implementation." : "Investigate the question and return grounded findings." });
  if (build) plan.push({ agent: "Coder", text: "Built the requested solution", assignment: "Use the research and project context to implement the requested product." });
  if (build) plan.push({ agent: "Tester", text: "Checked behavior and found no blockers", assignment: "Test the implementation, report defects and send fixes back to Coder." });
  plan.push({ agent: "Reviewer", text: "Reviewed the complete result", assignment: "Check the work against the original goal and approve the final delivery." });
  return plan;
}

export default function Home() {
  const [task, setTask] = useState("Create the first version of the invoice dashboard");
  const [running, setRunning] = useState(false);
  const [states, setStates] = useState<Record<string, AgentState>>(Object.fromEntries(agents.map(a => [a.name, "idle"])));
  const [events, setEvents] = useState<Array<{agent:string;text:string}>>([]);
  const [workflow, setWorkflow] = useState<WorkStep[]>([]);
  const [complete, setComplete] = useState(false);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);
  useEffect(() => {
    if (!document.modelContext?.registerTool) return;
    const lifecycle = new AbortController();
    void Promise.resolve(document.modelContext.registerTool({
      name: "run_agent_task",
      title: "Run agent task",
      description: "Put a task into Axiom and start its simulated five-agent workflow.",
      inputSchema: { type: "object", properties: { task: { type: "string", minLength: 1 } }, required: ["task"], additionalProperties: false },
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      execute(input: unknown) {
        const value = input as { task?: unknown };
        if (typeof value?.task !== "string" || !value.task.trim()) throw new Error("A non-empty task is required.");
        setTask(value.task.trim());
        window.setTimeout(() => document.getElementById("run-task-button")?.click(), 0);
        return { status: "started", task: value.task.trim() };
      },
    }, { signal: lifecycle.signal })).catch(() => undefined);
    return () => lifecycle.abort();
  }, []);

  function runTask() {
    if (!task.trim() || running) return;
    timers.current.forEach(clearTimeout);
    const chosen = decideWorkflow(task);
    const chosenNames = new Set(chosen.map(step => step.agent));
    setWorkflow(chosen);
    setRunning(true); setComplete(false); setEvents([]);
    setStates(Object.fromEntries(agents.map(a => [a.name, chosenNames.has(a.name) ? "idle" : "skipped"])));
    chosen.forEach(({agent, text}, index) => timers.current.push(setTimeout(() => {
      setStates(current => {
        const next = {...current};
        if (index > 0) next[chosen[index - 1].agent] = "done";
        next[agent] = "working";
        return next;
      });
      setEvents(current => [{agent, text}, ...current]);
    }, index * 1150)));
    timers.current.push(setTimeout(() => {
      setStates(Object.fromEntries(agents.map(a => [a.name, chosenNames.has(a.name) ? "done" : "skipped"])));
      setRunning(false); setComplete(true);
    }, chosen.length * 1150));
  }

  return <main className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-mark"><Sparkles size={17}/></span><span>AXIOM</span></div>
      <div className="topbar-actions"><button className="icon-button" aria-label="Source control"><GitBranch size={18}/></button><button className="icon-button" aria-label="Settings"><Settings size={18}/></button><span className="avatar">RF</span></div>
    </header>

    <aside className="sidebar">
      <p className="eyebrow">Workspace</p>
      <button className="project-switcher"><span className="project-icon">I</span><span><strong>Invoice MVP</strong><small>5 agents</small></span><ChevronDown size={16}/></button>
      <nav aria-label="Project navigation"><a className="nav-item active" href="#task"><Bot size={17}/> Control room</a><a className="nav-item" href="#activity"><Activity size={17}/> Activity</a></nav>
      <div className="sidebar-bottom"><p className="eyebrow">Projects</p><button className="new-project"><CirclePlus size={16}/> New project</button><div className="project-row"><span className="project-dot violet"/> Rehab AI</div><div className="project-row"><span className="project-dot amber"/> Trend Engine</div></div>
    </aside>

    <section className="workspace">
      <div className="page-heading"><div><p className="eyebrow">Invoice MVP</p><h1>Control room</h1></div><div className="system-status"><span/> System ready</div></div>
      <section className="task-card" id="task">
        <div className="task-label"><Sparkles size={15}/> New task</div>
        <textarea value={task} onChange={e => setTask(e.target.value)} placeholder="What should the agents work on?" aria-label="Task description" rows={3}/>
        <div className="task-footer"><span>Orchestrator selects the agents automatically</span><button id="run-task-button" className="run-button" onClick={runTask} disabled={running || !task.trim()}>{running ? <><span className="spinner"/> Running</> : <><Play size={15} fill="currentColor"/> Run task</>}</button></div>
      </section>

      <div className="section-title"><div><p className="eyebrow">Team</p><h2>Agent workspace</h2></div><span>{running ? `${workflow.length} agents selected` : complete ? "Run complete" : "Standing by"}</span></div>
      <div className="agent-grid">{agents.map(({name, role, icon:Icon}) => {
        const state = states[name];
        return <article className={`agent-card ${state}`} key={name}><div className="agent-top"><span className="agent-icon"><Icon size={18}/></span><span className={`state-dot ${state}`}/></div><h3>{name}</h3><p>{role}</p><div className="agent-state">{state === "working" ? "Working now" : state === "done" ? <><Check size={13}/> Complete</> : state === "skipped" ? "Not needed" : "Waiting"}</div></article>
      })}</div>

      <div className="lower-grid" id="activity">
        <section className="panel"><div className="panel-heading"><div><p className="eyebrow">Live</p><h2>Activity</h2></div><span className="event-count">{events.length}</span></div><div className="event-list" aria-live="polite">
          {events.length === 0 ? <div className="empty-state"><Activity size={21}/><p>Activity appears here when you run a task.</p></div> : events.map((event,index) => <div className="event" key={`${event.agent}-${index}`}><span className="event-line"/><div><strong>{event.agent}</strong><p>{event.text}</p></div><time>now</time></div>)}
        </div></section>
        <section className="panel"><div className="panel-heading"><div><p className="eyebrow">Result</p><h2>Output</h2></div>{complete && <span className="success-pill"><Check size={13}/> Ready</span>}</div>
          {complete ? <div className="output-ready"><div className="output-icon"><Network size={20}/></div><h3>Workflow completed</h3><p>Orchestrator selected {workflow.length - 1} specialist{workflow.length === 2 ? "" : "s"} for this task.</p><div className="workflow-list">{workflow.map((step, index) => <div className="workflow-step" key={step.agent}><span>{index + 1}</span><div><strong>{step.agent}</strong><small>{step.assignment}</small></div></div>)}</div></div> : <div className="empty-state"><Bot size={22}/><p>The chosen workflow and final result will appear here.</p></div>}
        </section>
      </div>
    </section>
  </main>;
}
