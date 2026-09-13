"use client";
import { useEffect, useRef, useState } from "react";
import { Activity, Bot, Braces, Check, ChevronDown, CirclePlus, FlaskConical, GitBranch, KeyRound, Network, Play, Search, Settings, ShieldCheck, Sparkles, X } from "lucide-react";

type AgentState = "idle" | "working" | "done" | "skipped";
type WorkStep = { agent: string; text: string; assignment: string; reason?: string };
const agents = [
  { name: "Orchestrator", role: "Plans and delegates", icon: Network },
  { name: "Researcher", role: "Finds context", icon: Search },
  { name: "Coder", role: "Builds the solution", icon: Braces },
  { name: "Tester", role: "Checks the work", icon: FlaskConical },
  { name: "Reviewer", role: "Final quality pass", icon: ShieldCheck },
];
const eventText: Record<string,string> = {
  Orchestrator: "Understood the goal and assembled the team",
  Researcher: "Received the research assignment",
  Coder: "Received the implementation assignment",
  Tester: "Received the verification assignment",
  Reviewer: "Received the final review assignment",
};
const modelOptions = [
  { id: "~openai/gpt-sol-latest", name: "OpenAI GPT Sol — latest", note: "Balanced default" },
  { id: "google/gemini-3.1-pro-preview", name: "Gemini 3.1 Pro Preview", note: "Strong reasoning" },
  { id: "google/gemini-3.6-flash", name: "Gemini 3.6 Flash", note: "Fast agent workflows" },
  { id: "google/gemini-3.1-flash-lite-20260507", name: "Gemini 3.1 Flash Lite", note: "Lowest latency and cost" },
  { id: "google/gemini-2.5-flash", name: "Gemini 2.5 Flash", note: "Reliable workhorse" },
  { id: "~google/gemini-pro-latest", name: "Gemini Pro — latest", note: "Automatic latest Pro" },
];

export default function Home() {
  const [task,setTask]=useState("Lag et spillbart sjakkspill med et enkelt og mørkt design");
  const [running,setRunning]=useState(false);
  const [states,setStates]=useState<Record<string,AgentState>>(Object.fromEntries(agents.map(a=>[a.name,"idle"])));
  const [events,setEvents]=useState<Array<{agent:string;text:string}>>([]);
  const [workflow,setWorkflow]=useState<WorkStep[]>([]);
  const [summary,setSummary]=useState("");
  const [complete,setComplete]=useState(false);
  const [settingsOpen,setSettingsOpen]=useState(false);
  const [key,setKey]=useState("");
  const [connected,setConnected]=useState(false);
  const [editingKey,setEditingKey]=useState(false);
  const [model,setModel]=useState("~openai/gpt-sol-latest");
  const [error,setError]=useState("");
  const timers=useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(()=>{setConnected(Boolean(sessionStorage.getItem("axiom-openrouter-key")));setModel(sessionStorage.getItem("axiom-model")||"~openai/gpt-sol-latest");return()=>timers.current.forEach(clearTimeout)},[]);
  function saveConnection(){if((!connected||editingKey)&&!key.trim().startsWith("sk-or-")){setError("OpenRouter-nøkkelen må starte med sk-or-");return}if(key.trim())sessionStorage.setItem("axiom-openrouter-key",key.trim());sessionStorage.setItem("axiom-model",model);setConnected(true);setEditingKey(false);setKey("");setError("");setSettingsOpen(false)}
  function removeConnection(){sessionStorage.removeItem("axiom-openrouter-key");setConnected(false);setEditingKey(false);setKey("");setError("")}
  function animate(chosen:WorkStep[]){
    const names=new Set(chosen.map(s=>s.agent));
    setStates(Object.fromEntries(agents.map(a=>[a.name,names.has(a.name)?"idle":"skipped"])));
    chosen.forEach((step,index)=>timers.current.push(setTimeout(()=>{
      setStates(current=>{const next={...current};if(index>0)next[chosen[index-1].agent]="done";next[step.agent]="working";return next});
      setEvents(current=>[{agent:step.agent,text:eventText[step.agent]||"Received assignment"},...current]);
    },index*1050)));
    timers.current.push(setTimeout(()=>{setStates(Object.fromEntries(agents.map(a=>[a.name,names.has(a.name)?"done":"skipped"])));setRunning(false);setComplete(true)},chosen.length*1050));
  }
  async function runTask(){
    if(!task.trim()||running)return;
    const apiKey=sessionStorage.getItem("axiom-openrouter-key");
    if(!apiKey){setSettingsOpen(true);setError("Koble til OpenRouter før du kjører oppgaven.");return}
    timers.current.forEach(clearTimeout);setRunning(true);setComplete(false);setEvents([]);setWorkflow([]);setSummary("");setError("");
    setStates(Object.fromEntries(agents.map(a=>[a.name,a.name==="Orchestrator"?"working":"idle"])));
    try{
      const response=await fetch("/api/orchestrate",{method:"POST",headers:{"Content-Type":"application/json","x-openrouter-key":apiKey},body:JSON.stringify({task,model:sessionStorage.getItem("axiom-model")||model})});
      const data=await response.json() as {error?:string;summary?:string;agents?:Array<{name:string;assignment:string;reason:string}>};
      if(!response.ok||!data.agents)throw new Error(data.error||"Orchestrator svarte ikke.");
      const chosen=data.agents.map(agent=>({agent:agent.name,assignment:agent.assignment,reason:agent.reason,text:eventText[agent.name]||"Received assignment"}));
      setSummary(data.summary||task);setWorkflow(chosen);animate(chosen);
    }catch(reason){setRunning(false);setStates(Object.fromEntries(agents.map(a=>[a.name,"idle"])));setError(reason instanceof Error?reason.message:"Noe gikk galt.")}
  }

  return <main className="app-shell">
    <header className="topbar"><div className="brand"><span className="brand-mark"><Sparkles size={17}/></span><span>AXIOM</span></div><div className="topbar-actions"><span className={`llm-status ${connected?"connected":""}`}><span/>{connected?"LLM connected":"LLM offline"}</span><button className="icon-button" aria-label="Source control"><GitBranch size={18}/></button><button className="icon-button" aria-label="LLM settings" onClick={()=>setSettingsOpen(true)}><Settings size={18}/></button><span className="avatar">RF</span></div></header>
    <aside className="sidebar"><p className="eyebrow">Workspace</p><button className="project-switcher"><span className="project-icon">I</span><span><strong>Invoice MVP</strong><small>5 agents</small></span><ChevronDown size={16}/></button><nav aria-label="Project navigation"><a className="nav-item active" href="#task"><Bot size={17}/> Control room</a><a className="nav-item" href="#activity"><Activity size={17}/> Activity</a></nav><div className="sidebar-bottom"><p className="eyebrow">Projects</p><button className="new-project"><CirclePlus size={16}/> New project</button><div className="project-row"><span className="project-dot violet"/> Rehab AI</div><div className="project-row"><span className="project-dot amber"/> Trend Engine</div></div></aside>
    <section className="workspace">
      <div className="page-heading"><div><p className="eyebrow">Invoice MVP</p><h1>Control room</h1></div><div className="system-status"><span/> System ready</div></div>
      <section className="task-card" id="task"><div className="task-label"><Sparkles size={15}/> New task</div><textarea value={task} onChange={e=>setTask(e.target.value)} placeholder="What should the agents work on?" aria-label="Task description" rows={3}/>{error&&<div className="error-message">{error}</div>}<div className="task-footer"><span>AI Orchestrator selects the agents automatically</span><button className="run-button" onClick={runTask} disabled={running||!task.trim()}>{running?<><span className="spinner"/> Orchestrating</>:<><Play size={15} fill="currentColor"/> Run task</>}</button></div></section>
      <div className="section-title"><div><p className="eyebrow">Team</p><h2>Agent workspace</h2></div><span>{running?(workflow.length?`${workflow.length} agents selected`:"Orchestrator is planning"):complete?"Plan ready":"Standing by"}</span></div>
      <div className="agent-grid">{agents.map(({name,role,icon:Icon})=>{const state=states[name];return <article className={`agent-card ${state}`} key={name}><div className="agent-top"><span className="agent-icon"><Icon size={18}/></span><span className={`state-dot ${state}`}/></div><h3>{name}</h3><p>{role}</p><div className="agent-state">{state==="working"?"Working now":state==="done"?<><Check size={13}/> Complete</>:state==="skipped"?"Not needed":"Waiting"}</div></article>})}</div>
      <div className="lower-grid" id="activity">
        <section className="panel"><div className="panel-heading"><div><p className="eyebrow">Live</p><h2>Activity</h2></div><span className="event-count">{events.length}</span></div><div className="event-list" aria-live="polite">{events.length===0?<div className="empty-state"><Activity size={21}/><p>Activity appears here when you run a task.</p></div>:events.map((event,index)=><div className="event" key={event.agent+index}><span className="event-line"/><div><strong>{event.agent}</strong><p>{event.text}</p></div><time>now</time></div>)}</div></section>
        <section className="panel"><div className="panel-heading"><div><p className="eyebrow CC">AI plan</p><h2>Orchestrator output</h2></div>{complete&&<span className="success-pill"><Check size={13}/> Ready</span>}</div>{workflow.length?<div className="output-ready"><div className="output-icon"><Network size={20}/></div><h3>{summary}</h3><p>{workflow.length-1} specialists selected by the LLM.</p><div className="workflow-list">{workflow.map((step,index)=><div className="workflow-step" key={step.agent}><span>{index+1}</span><div><strong>{step.agent}</strong><small>{step.assignment}</small></div></div>)}</div></div>:<div className="empty-state"><Bot size={22}/><p>The LLM-generated workflow will appear here.</p></div>}</section>
      </div>
    </section>
    {settingsOpen&&<div className="modal-backdrop" role="presentation" onMouseDown={e=>{if(e.target===e.currentTarget)setSettingsOpen(false)}}><section className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title"><button className="modal-close" aria-label="Close" onClick={()=>setSettingsOpen(false)}><X size={18}/></button><div className="connection-icon"><KeyRound size={20}/></div><p className="eyebrow">Connection</p><h2 id="settings-title">OpenRouter</h2><p className="modal-copy">One OpenRouter key works across every model below. It stays in this browser session and is never displayed after saving.</p>{connected&&!editingKey?<div className="saved-key"><div><span>API key</span><strong>••••••••••••••••</strong></div><span className="saved-badge"><Check size={12}/> Saved</span><button onClick={()=>setEditingKey(true)}>Replace</button><button className="remove-key" onClick={removeConnection}>Remove</button></div>:<label>OpenRouter API key<input type="password" value={key} onChange={e=>setKey(e.target.value)} placeholder="sk-or-v1-…" autoComplete="new-password"/></label>}<label>Orchestrator model<select value={model} onChange={e=>setModel(e.target.value)}>{modelOptions.map(option=><option key={option.id} value={option.id}>{option.name} — {option.note}</option>)}</select></label><p className="model-id">{model}</p>{error&&<div className="error-message">{error}</div>}<button className="connect-button" onClick={saveConnection}>{connected&&!editingKey?"Save model":"Save connection"}</button></section></div>}
  </main>;
}
