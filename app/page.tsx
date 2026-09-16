"use client";
import { useEffect, useRef, useState } from "react";
import { Activity, Bot, Braces, Check, ChevronDown, FlaskConical, Network, Play, Settings, ShieldCheck, Sparkles, X } from "lucide-react";
type AgentState = "idle" | "working" | "done" | "failed" | "cancelled";
type WorkStep = { agent:string; assignment:string; result:string; model?:string };
type Task = { id:string; status:string; repair_round?:number; model_calls?:number; verification?:{mode:string;runtime_tested:boolean;visually_tested:boolean}; workers?:Array<{id:string;name:string;status:string;changed_files:string[]}>; workspace?:string; files?:string[]; summary:string; error?:string; agents:Array<{name:string;status:string;assignment:string;result:string;model?:string}>; events:Array<{agent:string;text:string;at:string}> };
const agents = [
 {name:"Planner",role:"Plans architecture and milestones",icon:Network},
 {name:"Coder",role:"Builds the solution",icon:Braces},
 {name:"Tester",role:"Validates the files",icon:FlaskConical},
 {name:"Reviewer",role:"Reviews the result",icon:ShieldCheck},
];
async function api<T>(path:string, options?:RequestInit):Promise<T>{
 const response=await fetch("/api/runtime/"+path,{...options,cache:"no-store",headers:{"Content-Type":"application/json",...options?.headers},signal:AbortSignal.timeout(15000)});
 const data=await response.json() as {detail?:unknown;error?:string};
 if(!response.ok) throw new Error(typeof data.detail==="string"?data.detail:data.error||"Backend request failed ("+response.status+")");
 return data as T;
}
export default function Home(){
 const [task,setTask]=useState("Lag et spillbart sjakkspill med et enkelt og mørkt design");
 const [projectInstructions,setProjectInstructions]=useState("");
 const [repairRound,setRepairRound]=useState(0);
 const [modelCalls,setModelCalls]=useState(0);
 const [verification,setVerification]=useState<Task["verification"]>();
 const [workers,setWorkers]=useState<NonNullable<Task["workers"]>>([]);
 const [running,setRunning]=useState(false);
 const [states,setStates]=useState<Record<string,AgentState>>(Object.fromEntries(agents.map(a=>[a.name,"idle"])));
 const [events,setEvents]=useState<Task["events"]>([]);
 const [workflow,setWorkflow]=useState<WorkStep[]>([]);
 const [summary,setSummary]=useState("");
 const [complete,setComplete]=useState(false);
 const [settingsOpen,setSettingsOpen]=useState(false);
 const [connected,setConnected]=useState(false);
 const [model,setModel]=useState("");
 const [agentModels,setAgentModels]=useState<Record<string,string>>({});
 const [models,setModels]=useState<Array<{id:string;name:string}>>([]);
 const [apiKey,setApiKey]=useState("");
 const [settingsError,setSettingsError]=useState("");
 const [savingSettings,setSavingSettings]=useState(false);
 const [error,setError]=useState("");
 const [taskId,setTaskId]=useState<string|null>(null);
 const [history,setHistory]=useState<Task[]>([]);
 const activeId=useRef<string|null>(null);
 const [workspace,setWorkspace]=useState("");
 const [files,setFiles]=useState<string[]>([]);
 function applyTask(data:Task){
  setRepairRound(data.repair_round||0);setModelCalls(data.model_calls||0);setVerification(data.verification);
  setWorkers(data.workers||[]);
  setWorkspace(data.workspace||"");setFiles(data.files||[]);setTaskId(data.id); setRunning(["queued","running"].includes(data.status));setComplete(data.status==="completed");
  setStates(Object.fromEntries(data.agents.map(a=>[a.name,({pending:"idle",running:"working",completed:"done",failed:"failed",cancelled:"cancelled"} as Record<string,AgentState>)[a.status]||"idle"])));
  setWorkflow(data.agents.map(a=>({agent:a.name,assignment:a.assignment,result:a.result,model:a.model})));
  setEvents([...data.events].reverse());setSummary(data.summary||"");setError(data.error||"");
 }
 useEffect(()=>{if(settingsOpen&&connected)void api<{models:Array<{id:string;name:string}>}>("models").then(data=>{setModels(data.models);setSettingsError("");}).catch(e=>setSettingsError((e instanceof Error?e.message:"Could not load models")+" The key is still saved; check the key if the list stays empty."));},[settingsOpen,connected]);
 async function refreshHealth(){
  try {const health=await api<{configured:boolean;workspace:string;model:string}>("health");setConnected(health.configured);setWorkspace(health.workspace);setModel(current=>current||localStorage.getItem("axiom-model")||health.model);}
  catch(e){setConnected(false);setError(e instanceof Error?e.message:"Backend unavailable");}
 }
 useEffect(()=>{
  // Remove credentials left by the previous browser-based integration.
  sessionStorage.removeItem("axiom-openrouter-key");
  void api<{configured:boolean;workspace:string;model:string}>("health").then(health=>{setConnected(health.configured);setWorkspace(health.workspace);setModel(current=>current||localStorage.getItem("axiom-model")||health.model);}).catch(e=>{setConnected(false);setError(e instanceof Error?e.message:"Backend unavailable");}).finally(()=>{try{const saved=localStorage.getItem("axiom-agent-models");if(saved)setAgentModels(JSON.parse(saved) as Record<string,string>);}catch{setAgentModels({});}});
  let alive=true;
  void api<{tasks:Task[]}>("tasks").then(data=>{if(!alive||activeId.current)return;setHistory(data.tasks);const saved=localStorage.getItem("axiom-task-id");const current=data.tasks.find(t=>t.id===saved)||data.tasks.find(t=>["queued","running"].includes(t.status));if(current){activeId.current=current.id;applyTask(current);}}).catch(()=>{});
  return()=>{alive=false;};
 },[]);
 useEffect(()=>{
  if(!taskId||!running)return;
  let stopped=false;let timer:ReturnType<typeof setTimeout>;
  const poll=async()=>{try{const data=await api<Task>("tasks/"+taskId);if(stopped||activeId.current!==taskId)return;applyTask(data);if(!["queued","running"].includes(data.status)){void api<{tasks:Task[]}>("tasks").then(d=>setHistory(d.tasks)).catch(()=>{});return;}}catch(e){if(!stopped)setError((e instanceof Error?e.message:"Connection interrupted")+" — retrying status check.");}if(!stopped)timer=setTimeout(poll,1200);};
  void poll();return()=>{stopped=true;clearTimeout(timer);};
 },[taskId,running]);
 async function saveConnection(){
  setSavingSettings(true);setSettingsError("");
  try{
   if(apiKey.trim()){await api("connection",{method:"POST",body:JSON.stringify({api_key:apiKey.trim()})});setApiKey("");setConnected(true);}
   else if(!connected)throw new Error("Enter your OpenRouter API key to connect.");
   // The key is already saved at this point. A blocked localStorage or a slow health
   // check must never make a successful save look like a failure.
   try{localStorage.setItem("axiom-model",model.trim());localStorage.setItem("axiom-agent-models",JSON.stringify(agentModels));}catch{}
   setSettingsOpen(false);setError("");
   void refreshHealth();
  }catch(e){setSettingsError(e instanceof Error?e.message:"Could not save connection");}
  finally{setSavingSettings(false);}
 }
 async function runTask(){
  if(!task.trim()||running)return;
  if(!connected){setError("Add an OpenRouter API key in LLM settings before running this task.");setSettingsError("");setSettingsOpen(true);return;}
  activeId.current="creating";setTaskId(null);setFiles([]);setRunning(true);setComplete(false);setError("");setEvents([]);setWorkflow([]);setWorkers([]);setSummary("");setRepairRound(0);setModelCalls(0);setVerification(undefined);
  const perAgent=Object.fromEntries(Object.entries(agentModels).filter(([,value])=>value));
  try{const data=await api<Task>("tasks",{method:"POST",body:JSON.stringify({task,project_instructions:projectInstructions,model:model.trim()||undefined,models:Object.keys(perAgent).length?perAgent:undefined})});activeId.current=data.id;localStorage.setItem("axiom-task-id",data.id);applyTask(data);}
  catch(e){setRunning(false);setError(e instanceof Error?e.message:"Could not start task");}
 }
 async function cancelTask(){if(!taskId)return;try{applyTask(await api<Task>("tasks/"+taskId+"/cancel",{method:"POST"}));}catch(e){setError(e instanceof Error?e.message:"Could not cancel task");}}
 async function selectTask(id:string){if(running)return;activeId.current=id;try{const data=await api<Task>("tasks/"+id);if(activeId.current!==id)return;localStorage.setItem("axiom-task-id",id);applyTask(data);}catch(e){setError(e instanceof Error?e.message:"Could not load task");}}
  return <main className="app-shell">
    <header className="topbar"><div className="brand"><span className="brand-mark"><Sparkles size={17}/></span><span>AXIOM</span></div><div className="topbar-actions"><span className={`llm-status ${connected?"connected":""}`}><span/>{connected?"LLM connected":"LLM offline"}</span><button className="icon-button" aria-label="LLM settings" onClick={()=>setSettingsOpen(true)}><Settings size={18}/></button><span className="avatar">RF</span></div></header>
    <aside className="sidebar"><p className="eyebrow">Workspace</p><button className="project-switcher"><span className="project-icon">I</span><span><strong>Axiom</strong><small>4 agents</small></span><ChevronDown size={16}/></button><nav aria-label="Project navigation"><a className="nav-item active" href="#task"><Bot size={17}/> Control room</a><a className="nav-item" href="#activity"><Activity size={17}/> Activity</a></nav><div className="sidebar-bottom"><p className="eyebrow">Recent tasks</p>{history.slice(0,8).map(item=><button key={item.id} className="history-task" disabled={running} onClick={()=>void selectTask(item.id)}><strong>{item.summary||item.id.slice(0,8)}</strong><small>{item.status}</small></button>)}</div></aside>
    <section className="workspace">
      <div className="page-heading"><div><p className="eyebrow">Axiom workspace</p><h1>Control room</h1></div><div className="system-status"><span/> {running?"Agents working":connected?"Backend ready":"Setup required"}</div></div>
      <section className="task-card" id="task"><div className="task-label"><Sparkles size={15}/> New task</div><textarea value={task} onChange={e=>setTask(e.target.value)} placeholder="What should the agents work on?" aria-label="Task description" rows={3}/><details><summary>Project instructions (AGENTS.md)</summary><textarea value={projectInstructions} onChange={e=>setProjectInstructions(e.target.value)} placeholder="Optional: architecture constraints, style, controls, performance targets and project rules shared by every agent." aria-label="Project instructions" maxLength={16000} rows={4} disabled={running}/></details>{error&&<div className="error-message">{error}</div>}<div className="task-footer"><span>Plan → Build → Check → Review → Repair if needed</span><button className="run-button" onClick={runTask} disabled={running||!task.trim()}>{running?<><span className="spinner"/> Running</>:<><Play size={15} fill="currentColor"/> Run task</>}</button>{running&&<button className="cancel-button" onClick={()=>void cancelTask()}>Cancel</button>}</div></section>
      <div className="section-title"><div><p className="eyebrow">Team</p><h2>Agent workspace</h2></div><span>{running?(workflow.length?`${workflow.length} agents selected`:"Starting task"):complete?"Task complete":"Standing by"}</span></div>
      <div className="agent-grid">{agents.map(({name,role,icon:Icon})=>{const state=states[name];const used=workflow.find(step=>step.agent===name)?.model;return <article className={`agent-card ${state}`} key={name}><div className="agent-top"><span className="agent-icon"><Icon size={18}/></span><span className={`state-dot ${state}`}/></div><h3>{name}</h3><p>{role}</p><small className="agent-model" title={used||""}>{used||""}</small><div className="agent-state">{state==="working"?"Working now":state==="done"?<><Check size={13}/> Complete</>:state==="failed"?"Failed":state==="cancelled"?"Cancelled":"Waiting"}</div></article>})}</div>
      <div className="lower-grid" id="activity">
        <section className="panel"><div className="panel-heading"><div><p className="eyebrow">Live</p><h2>Activity</h2></div><span className="event-count">{events.length}</span></div><div className="event-list" aria-live="polite">{events.length===0?<div className="empty-state"><Activity size={21}/><p>Activity appears here when you run a task.</p></div>:events.map((event,index)=><div className="event" key={event.agent+index}><span className="event-line"/><div><strong>{event.agent}</strong><p>{event.text}</p></div><time>{event.at?new Date(event.at).toLocaleTimeString():""}</time></div>)}</div></section>
        <section className="panel"><div className="panel-heading"><div><p className="eyebrow CC">Results</p><h2>Agent output</h2></div>{complete&&<span className="success-pill"><Check size={13}/> Review passed</span>}</div>{workflow.length?<div className="output-ready"><div className="output-icon"><Network size={20}/></div><h3>{summary||"Task in progress"}</h3><p className="workspace-path">{workspace}</p><p>{modelCalls} model calls · {repairRound} repair rounds{workers.length>0?` · ${workers.length} subagent proposals`:""}</p>{verification&&<p>Static checks only. Runtime behavior and visuals have not been tested.</p>}{files.length>0&&<ul className="generated-files">{files.map(file=><li key={file}>{file}</li>)}</ul>}<div className="workflow-list">{workflow.map((step,index)=><div className="workflow-step" key={step.agent}><span>{index+1}</span><div><strong>{step.agent}</strong><small>{step.assignment}</small>{step.result&&<pre className="agent-result">{step.result}</pre>}</div></div>)}</div>{workers.length>0&&<div className="workflow-list"><p className="eyebrow">Coder subagents</p>{workers.map(worker=><div className="workflow-step" key={worker.id}><span>↳</span><div><strong>{worker.name}</strong><small>{worker.status} · {worker.changed_files.length?worker.changed_files.join(", "):"no project changes"}</small></div></div>)}</div>}</div>:<div className="empty-state"><Bot size={22}/><p>Agent results and verification reports will appear here.</p></div>}</section>
      </div>
    </section>
    {settingsOpen&&<div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)setSettingsOpen(false)}}><section className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title"><button className="modal-close" aria-label="Close" onClick={()=>setSettingsOpen(false)}><X size={18}/></button><p className="eyebrow">Backend connection</p><h2 id="settings-title">OpenRouter</h2><p className="modal-copy">Enter your API key to connect the local backend. It is saved in the backend&apos;s own credentials file (.axiom/credentials.env), never in this browser and never in a file Next.js watches while developing.</p><label>OpenRouter API key<input type="password" value={apiKey} onChange={e=>setApiKey(e.target.value)} placeholder={connected?"Key configured — enter a new key to replace it":"Paste your API key"} autoComplete="off"/></label><p className="modal-copy">{connected?"Backend key configured":"Backend key not configured"}</p><label>Available models<select value={models.some(option=>option.id===model)?model:""} onChange={e=>setModel(e.target.value)} disabled={!connected}><option value="">{connected?"Custom or backend default":"Connect a key to load models"}</option>{models.map(option=><option value={option.id} key={option.id}>{option.name}</option>)}</select></label><label>OpenRouter model ID<input value={model} onChange={e=>setModel(e.target.value)} placeholder="Use backend default" autoComplete="off"/></label><p className="modal-copy">Use models that support tool calling. The default runs every agent unless you override it below.</p><div className="agent-models"><p className="eyebrow">Model per agent</p>{agents.map(({name})=><label key={name}>{name}<select value={agentModels[name]||""} onChange={e=>setAgentModels(current=>({...current,[name]:e.target.value}))} disabled={!connected}><option value="">Use default model</option>{models.map(option=><option value={option.id} key={option.id}>{option.name}</option>)}</select></label>)}</div>{settingsError&&<p className="error-message" role="alert">{settingsError}</p>}<button className="connect-button" onClick={()=>void saveConnection()} disabled={savingSettings}>{savingSettings?"Saving connection…":"Save connection"}</button></section></div>}
  </main>;
}
