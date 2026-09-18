"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, Bot, Braces, Check, ChevronDown, ChevronRight, FlaskConical, Menu, Network, Play, Plus, Settings, ShieldCheck, Sparkles, X } from "lucide-react";
type AgentState = "idle" | "working" | "done" | "failed" | "cancelled";
type WorkStep = { agent:string; assignment:string; result:string; model?:string; rounds?:number };
type Usage = { totals?:{calls?:number;unknown_calls?:number;prompt_tokens?:number;completion_tokens?:number;reasoning_tokens?:number;cached_tokens?:number;cost?:number;latency_ms?:number} };
type TestResult = { state:string; intent?:string; command?:string; exit_code?:number|null; duration_ms?:number; output?:string; reason?:string };
type Task = { id:string; status:string; project?:string; created_at?:string; updated_at?:string; repair_round?:number; model_calls?:number; limits?:{tool_rounds?:number;model_calls?:number}; usage?:Usage; verification?:{mode:string;runtime_tested:boolean;visually_tested:boolean;execution?:{state?:string;command?:string;exit_code?:number}}; workers?:Array<{id:string;name:string;status:string;changed_files:string[];assignment?:string}>; workspace?:string; files?:string[]; summary:string; error?:string; agents:Array<{name:string;status:string;assignment:string;result:string;model?:string;work_rounds?:number}>; events:Array<{agent:string;text:string;at:string}> };
// Only what the provider reported is shown. A missing figure stays absent rather
// than becoming a zero the operator would read as free.
function usageLine(usage:Usage|undefined){
 const totals=usage?.totals;
 if(!totals?.calls) return "";
 const tokens=(totals.prompt_tokens||0)+(totals.completion_tokens||0);
 const parts=[`${totals.calls} recorded calls`];
 if(tokens)parts.push(`${tokens.toLocaleString()} tokens`);
 if(totals.cost)parts.push(`$${totals.cost.toFixed(4)}`);
 if(totals.unknown_calls)parts.push(`${totals.unknown_calls} with no usage reported`);
 return parts.join(" · ");
}
// The label follows the record. A task whose declared check ran must never keep the
// "static checks only" sentence, and one whose check failed must say so.
function verificationLabel(verification:Task["verification"]){
 if(!verification) return "";
 const execution=verification.execution;
 if(execution?.state==="passed") return `The declared check ran and passed (${execution.command}). Visual behavior is still unverified.`;
 if(execution?.state==="failed") return `The declared check ran and failed with exit code ${execution.exit_code} (${execution.command}). Read the record before trusting this result.`;
 return "Static checks only. Runtime behavior and visuals have not been tested.";
}
// A thread is titled by what it was asked to do, the way a chat is. The reviewer's
// summary only appears once a run has produced one.
function taskTitle(item:{id:string;task?:string;summary?:string}){
 const source=(item.task||item.summary||item.id).trim();
 return source.split("\n")[0].slice(0,90)||item.id.slice(0,8);
}
// A run's day, in the operator's own timezone: the record stores UTC.
function dayKey(date:Date){return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")}`;}
function taskDay(item:{created_at?:string}){
 if(!item.created_at)return null;
 const date=new Date(item.created_at);
 return Number.isNaN(date.getTime())?null:dayKey(date);
}
function monthLabel(date:Date){return date.toLocaleDateString(undefined,{month:"long",year:"numeric"});}
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
 const [task,setTask]=useState("");
 // A thread is filed under a project, and the composer leaves the screen once the
 // prompt has been sent: the running task is what the operator is reading then.
 const [project,setProject]=useState("Axiom");
 const [composerOpen,setComposerOpen]=useState(true);
 // Narrow panes keep the project list in a drawer instead of dropping it entirely.
 const [navOpen,setNavOpen]=useState(false);
 const [activityMonth,setActivityMonth]=useState(()=>new Date());
 const [activityDay,setActivityDay]=useState<string|null>(null);
 const [previewUrl,setPreviewUrl]=useState("");
 const [testResult,setTestResult]=useState<TestResult|null>(null);
 const [testing,setTesting]=useState(false);
 const [limits,setLimits]=useState<{tool_rounds?:number;model_calls?:number}>();
 // An instruction written while a run is in flight waits here and goes out the moment
 // the thread is free, which is what makes the prompt window usable during work.
 const [pending,setPending]=useState("");
 const pendingRef=useRef("");
 const [outputOpen,setOutputOpen]=useState(true);
 const [extraProjects,setExtraProjects]=useState<string[]>([]);
 const [newProject,setNewProject]=useState<string|null>(null);
 const [openProjects,setOpenProjects]=useState<Record<string,boolean>>({});
 const [activeTitle,setActiveTitle]=useState("");
 const [repairRound,setRepairRound]=useState(0);
 const [modelCalls,setModelCalls]=useState(0);
 const [usage,setUsage]=useState<Usage>();
 const [verification,setVerification]=useState<Task["verification"]>();
 const [workers,setWorkers]=useState<NonNullable<Task["workers"]>>([]);
 const [continueFrom,setContinueFrom]=useState<string|null>(null);
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
  setUsage(data.usage);
  setWorkers(data.workers||[]);
  setWorkspace(data.workspace||"");setFiles(data.files||[]);setTaskId(data.id); setRunning(["queued","running"].includes(data.status));setComplete(data.status==="completed");
  setStates(Object.fromEntries(data.agents.map(a=>[a.name,({pending:"idle",running:"working",completed:"done",failed:"failed",cancelled:"cancelled"} as Record<string,AgentState>)[a.status]||"idle"])));
  setWorkflow(data.agents.map(a=>({agent:a.name,assignment:a.assignment,result:a.result,model:a.model,rounds:a.work_rounds})));
  setEvents([...data.events].reverse());setSummary(data.summary||"");setError(data.error||"");
  setLimits(data.limits);
 }
 // One submit path for both cases: a free thread starts the run, a busy one holds the
 // instruction and sends it when the thread is free.
 const sendPrompt=useCallback(async(text:string)=>{
  const prompt=text.trim();
  if(!prompt)return;
  if(running){
   pendingRef.current=prompt;setPending(prompt);setTask("");
   return;
  }
  if(!connected){setError("Add an OpenRouter API key in LLM settings before running this task.");setSettingsError("");setSettingsOpen(true);return;}
  activeId.current="creating";setFiles([]);setRunning(true);setComplete(false);setError("");setEvents([]);setWorkflow([]);setWorkers([]);setSummary("");setRepairRound(0);setModelCalls(0);setVerification(undefined);
  const perAgent=Object.fromEntries(Object.entries(agentModels).filter(([,value])=>value));
  // A running thread has nothing to continue yet, so an instruction sent while it works
  // is held and replayed here once it is free.
  const target=running?null:(continueFrom||taskId);
  if(!target)setTaskId(null);
  try{const data=await api<Task>("tasks",{method:"POST",body:JSON.stringify({task:prompt,project:project.trim()||undefined,model:model.trim()||undefined,models:Object.keys(perAgent).length?perAgent:undefined,continue_from:target||undefined})});activeId.current=data.id;localStorage.setItem("axiom-task-id",data.id);try{localStorage.setItem("axiom-project",project.trim()||"Axiom");}catch{}setActiveTitle(taskTitle({id:data.id,task:prompt}));setTask("");setComposerOpen(false);setContinueFrom(null);applyTask(data);}
  catch(e){setRunning(false);setError(e instanceof Error?e.message:"Could not start task");}
 },[running,continueFrom,taskId,connected,agentModels,project,model]);
 // What was typed while the thread was busy goes out as soon as it is free.
 useEffect(()=>{
  if(running||!pendingRef.current)return;
  const next=pendingRef.current;
  pendingRef.current="";setPending("");
  void sendPrompt(next);
 },[running,sendPrompt]);
 useEffect(()=>{if(settingsOpen&&connected)void api<{models:Array<{id:string;name:string}>}>("models").then(data=>{setModels(data.models);setSettingsError("");}).catch(e=>setSettingsError((e instanceof Error?e.message:"Could not load models")+" The key is still saved; check the key if the list stays empty."));},[settingsOpen,connected]);
 async function refreshHealth(){
  try {const health=await api<{configured:boolean;workspace:string;model:string}>("health");setConnected(health.configured);setWorkspace(health.workspace);setModel(current=>current||localStorage.getItem("axiom-model")||health.model);}
  catch(e){setConnected(false);setError(e instanceof Error?e.message:"Backend unavailable");}
 }
 useEffect(()=>{
  // Remove credentials left by the previous browser-based integration.
  sessionStorage.removeItem("axiom-openrouter-key");
  void api<{configured:boolean;workspace:string;model:string}>("health").then(health=>{setConnected(health.configured);setWorkspace(health.workspace);setModel(current=>current||localStorage.getItem("axiom-model")||health.model);}).catch(e=>{setConnected(false);setError(e instanceof Error?e.message:"Backend unavailable");}).finally(()=>{try{const saved=localStorage.getItem("axiom-agent-models");if(saved)setAgentModels(JSON.parse(saved) as Record<string,string>);const savedProject=localStorage.getItem("axiom-project");if(savedProject)setProject(savedProject);const savedProjects=localStorage.getItem("axiom-projects");if(savedProjects)setExtraProjects(JSON.parse(savedProjects) as string[]);}catch{setAgentModels({});}});
  let alive=true;
  void api<{tasks:Task[]}>("tasks").then(data=>{if(!alive||activeId.current)return;setHistory(data.tasks);const saved=localStorage.getItem("axiom-task-id");const current=data.tasks.find(t=>t.id===saved)||data.tasks.find(t=>["queued","running"].includes(t.status));if(current){activeId.current=current.id;setActiveTitle(taskTitle(current));setProject(current.project||"Axiom");setComposerOpen(false);applyTask(current);}}).catch(()=>{});
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
 async function cancelTask(){if(!taskId)return;try{applyTask(await api<Task>("tasks/"+taskId+"/cancel",{method:"POST"}));}catch(e){setError(e instanceof Error?e.message:"Could not cancel task");}}
async function selectTask(id:string){activeId.current=id;try{const data=await api<Task>("tasks/"+id);if(activeId.current!==id)return;localStorage.setItem("axiom-task-id",id);setActiveTitle(taskTitle(data));setProject(data.project||"Axiom");setTask("");setNavOpen(false);applyTask(data);}catch(e){setError(e instanceof Error?e.message:"Could not load task");}}
// Resume asks the backend to continue this task's workspace on its own, so an
// interrupted run costs one click instead of a retyped prompt.
 async function resumeTask(){if(!taskId||running)return;setError("");try{const data=await api<Task>("tasks/"+taskId+"/resume",{method:"POST"});activeId.current=data.id;localStorage.setItem("axiom-task-id",data.id);setActiveTitle(taskTitle(data));setComposerOpen(false);applyTask(data);}catch(e){setError(e instanceof Error?e.message:"Could not resume task");}}
 const [view,setView]=useState<"task"|"activity">("task");
 // Switching views is state, not navigation: the hash used to be rewritten on every
 // click, which fought the listener below and jumped the page around.
 const goTo=(id:"task"|"activity")=>{setView(id);setNavOpen(false);window.setTimeout(()=>document.getElementById(id)?.scrollIntoView({block:"start"}),0);};
 useEffect(()=>{const sync=()=>setView(window.location.hash==="#activity"?"activity":"task");sync();window.addEventListener("hashchange",sync);return()=>window.removeEventListener("hashchange",sync);},[]);
 // Threads are grouped under a project in the sidebar. A thread filed nowhere reads
 // as part of the default project rather than disappearing from the list.
 // Projects exist in the sidebar even before their first thread, so a project made
 // with the + is remembered rather than appearing only once something ran in it.
 const projectNames=Array.from(new Set([project.trim()||"Axiom",...extraProjects,...history.map(item=>item.project||"Axiom")]));
 const groups=projectNames.map(name=>({name,tasks:history.filter(item=>(item.project||"Axiom")===name)}));
 const startNewTask=()=>{
  activeId.current=null;setTaskId(null);setFiles([]);setEvents([]);setWorkflow([]);setWorkers([]);setSummary("");setComplete(false);setError("");setRunning(false);setVerification(undefined);setRepairRound(0);setModelCalls(0);setUsage(undefined);setTask("");setContinueFrom(null);setActiveTitle("");setComposerOpen(true);setStates(Object.fromEntries(agents.map(a=>[a.name,"idle"])));setNavOpen(false);
  try{localStorage.removeItem("axiom-task-id");}catch{}
  window.setTimeout(()=>document.getElementById("prompt")?.focus(),0);
 };
 // Deleting a thread also deletes the workspace it wrote, so it asks first and names
 // what goes. The backend refuses while the task is still running.
 async function deleteTask(id:string){
  const item=history.find(task=>task.id===id);
  if(!window.confirm(`Delete "${item?taskTitle(item):id.slice(0,8)}" and the workspace it wrote? This cannot be undone.`))return;
  setError("");
  try{
   await api("tasks/"+id,{method:"DELETE"});
   const data=await api<{tasks:Task[]}>("tasks");
   setHistory(data.tasks);
   if(id===taskId)startNewTask();
  }catch(e){setError(e instanceof Error?e.message:"Could not delete the thread");}
 }
 // Deleting a project means deleting its threads: the name lives in localStorage, the
 // threads live in the store, and both have to go or the project comes back empty.
 async function deleteProject(name:string){
  const threads=history.filter(task=>(task.project||"Axiom")===name);
  const question=threads.length
   ? `Delete the project "${name}" and its ${threads.length} thread${threads.length===1?"":"s"}? Their workspaces are removed too.`
   : `Delete the empty project "${name}"?`;
  if(!window.confirm(question))return;
  setError("");
  let failed=0;
  for(const thread of threads){
   try{await api("tasks/"+thread.id,{method:"DELETE"});}
   catch{failed+=1;}
  }
  const next=extraProjects.filter(item=>item!==name);
  setExtraProjects(next);
  try{localStorage.setItem("axiom-projects",JSON.stringify(next));}catch{}
  try{const data=await api<{tasks:Task[]}>("tasks");setHistory(data.tasks);}catch{}
  if(project.trim()===name)setProject("Axiom");
  if(threads.some(thread=>thread.id===taskId))startNewTask();
  if(failed)setError(`${failed} thread${failed===1?"":"s"} could not be deleted; try again.`);
 }
 // The page a run wrote is served from this machine on its own port, so this hands the
 // operator a real address to open rather than describing one.
 async function openPreview(id:string){
  setError("");
  try{
   const data=await api<{url:string}>("tasks/"+id+"/preview",{method:"POST"});
   setPreviewUrl(data.url);
   window.open(data.url,"_blank","noopener");
  }catch(e){setError(e instanceof Error?e.message:"Could not open the preview");}
 }
 // Testing a project means running the checks it declares, in its own workspace, and
 // showing what happened. Nothing is inferred from the source.
 async function runChecks(id:string){
  setError("");setTestResult(null);setTesting(true);
  try{setTestResult(await api<TestResult>("tasks/"+id+"/test",{method:"POST"}));}
  catch(e){setError(e instanceof Error?e.message:"Could not run the project's checks");}
  finally{setTesting(false);}
 }
 // The point of testing is the loop: when it fails, the failure becomes the next
 // instruction instead of something the operator copies by hand.
 const fixFromTest=()=>{
  if(!testResult)return;
  setTask(`The project's own checks failed.\n\n$ ${testResult.command||"(no command)"}\nexit ${testResult.exit_code ?? "?"}\n\n${(testResult.output||"").slice(-1500)}\n\nFind the cause and fix it, then run the checks again.`);
  window.setTimeout(()=>document.getElementById("prompt")?.focus(),0);
 };
 // A run with no declared check cannot be tested, and inventing one for it would be a
 // lie about what was verified. Asking for one is the honest way to close that gap.
 const askForCheck=()=>{
  setTask('Add a runnable check to this project: a package.json with a "test" script that exercises the real logic and exits non-zero when it breaks. Keep it dependency-free, make it pass, and leave the project working.');
  window.setTimeout(()=>document.getElementById("prompt")?.focus(),0);
 };
 // Continuing is a prompt that carries the earlier workspace in with it, so it has to
 // bring the composer back: with the composer hidden, a button that only set state
 // looked like it did nothing at all.
 const continueTask=()=>{
  if(!taskId)return;
  setContinueFrom(taskId);setTask("");setError("");setComposerOpen(true);
  window.setTimeout(()=>document.getElementById("prompt")?.focus(),0);
 };
 // The prompt window is the thread's next message. Inside a thread it belongs at the
 // bottom of that thread, the way a chat input does; a brand new thread still starts
 // with the window at the top, because there is nothing above it yet.
 const composer=<section className="task-card" id="task"><div className="task-label"><Sparkles size={15}/> Talk to Axiom while it&apos;s working<span className="task-context">{project.trim()||"Axiom"}</span></div><textarea id="prompt" value={task} onChange={e=>setTask(e.target.value)} onKeyDown={e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();void sendPrompt(task);}}} title="Enter sends · Shift+Enter makes a new line" placeholder={running?"Type now — it goes out when this run finishes":"What should change next?"} aria-label="Task description" rows={3}/>{pending&&<div className="queued-note">Queued: “{pending}” — sent when this run finishes.</div>}{error&&<div className="error-message">{error}</div>}<div className="task-footer"><span className="send-hint">Enter sends · Shift+Enter for a new line</span><button className="run-button" onClick={()=>void sendPrompt(task)} disabled={!task.trim()}>{running?<><Play size={15} fill="currentColor"/> Queue it</>:<><Play size={15} fill="currentColor"/> Send</>}</button>{running&&<button className="cancel-button" onClick={()=>void cancelTask()}>Cancel</button>}</div></section>;
 // Nothing in the runtime knows how far along a task is, so the only honest fraction is
 // how many of the four roles have finished — and, for delegated work, how many of the
 // Coder's packages are done. Everything else here is a count, not an estimate.
 const doneRoles=agents.filter(agent=>["done","failed","cancelled"].includes(states[agent.name])).length;
 // An estimate, and it says so: finished roles are real, and the running role adds the
 // share of its own round budget it has used. That is what makes the bar move while the
 // Coder works instead of only when a role hands over.
 const runningAgent=agents.find(agent=>states[agent.name]==="working");
 const runningStep=runningAgent?workflow.find(step=>step.agent===runningAgent.name):undefined;
 const roundBudget=limits?.tool_rounds||0;
 const roundFraction=runningAgent&&roundBudget?Math.min(0.85,(runningStep?.rounds||0)/roundBudget):0;
 const pipelinePercent=Math.round(((doneRoles+roundFraction)/agents.length)*100);
 const packagesDone=workers.filter(worker=>worker.status==="completed").length;
 const agentTools=(name:string)=>events.filter(event=>event.agent===name&&event.text.startsWith("Tool ")).length;
 const monthCells=(()=>{
  const year=activityMonth.getFullYear();
  const month=activityMonth.getMonth();
  const lead=(new Date(year,month,1).getDay()+6)%7;
  const days=new Date(year,month+1,0).getDate();
  return {year,month,prefix:`${year}-${String(month+1).padStart(2,"0")}`,cells:[...Array(lead).fill(0),...Array.from({length:days},(_,index)=>index+1)]};
 })();
 const runsByDay=new Map<string,Task[]>();
 for(const item of history){const key=taskDay(item);if(key)runsByDay.set(key,[...(runsByDay.get(key)||[]),item]);}
 const monthRuns=history.filter(item=>(taskDay(item)||"").startsWith(monthCells.prefix));
 const selectedRuns=activityDay?(runsByDay.get(activityDay)||[]):monthRuns;
 const shiftMonth=(delta:number)=>{setActivityDay(null);setActivityMonth(current=>new Date(current.getFullYear(),current.getMonth()+delta,1));};
 // What running the project's own checks reported, exactly as it reported it.
 const testPanel=<section className="panel"><div className="panel-heading"><div><p className="eyebrow">Checks</p><h2>{testResult?.state==="passed"?"The project's checks passed":testResult?.state==="failed"?"The project's checks failed":"Nothing was run"}</h2></div>{typeof testResult?.exit_code==="number"&&<span className="event-count">exit {testResult.exit_code}</span>}</div><div className="output-ready">{testResult?.reason&&<p>{testResult.reason}</p>}{testResult?.state==="not_run"&&<p className="workspace-path">A run can be tested when it declares a check: a <code>test</code> or <code>build</code> script in package.json, or a Python test project.</p>}{testResult?.command&&<p className="workspace-path">{testResult.command}{typeof testResult.duration_ms==="number"?` · ${testResult.duration_ms} ms`:""}</p>}{testResult?.output&&<pre className="agent-result">{testResult.output}</pre>}{testResult?.state==="failed"&&<button className="continue-button" onClick={fixFromTest}>Fix what failed</button>}{testResult?.state==="not_run"&&<button className="continue-button" onClick={askForCheck}>Ask for a check</button>}</div></section>;
 // The activity page answers "what have the agents been doing" by day, from the run
 // records themselves. A day is marked by the worst thing that happened in it.
 const calendarPage=<>
  <div className="calendar-nav"><button className="icon-button" aria-label="Previous month" onClick={()=>shiftMonth(-1)}><ChevronRight size={16}/></button><strong>{monthLabel(activityMonth)}</strong><button className="icon-button" aria-label="Next month" onClick={()=>shiftMonth(1)}><ChevronRight size={16}/></button>{activityDay&&<button className="link-button" onClick={()=>setActivityDay(null)}>Show the whole month</button>}</div>
  <div className="calendar">{["M","T","W","T","F","S","S"].map((label,index)=><span className="dow" key={index}>{label}</span>)}{monthCells.cells.map((day,index)=>{if(!day)return <span className="day empty" key={`empty-${index}`}/>;const key=`${monthCells.prefix}-${String(day).padStart(2,"0")}`;const runs=runsByDay.get(key)||[];const bad=runs.some(run=>["failed","cancelled"].includes(run.status));const good=runs.some(run=>run.status==="completed");return <button key={key} type="button" disabled={runs.length===0} className={`day${runs.length?" has":""}${bad?" bad":good?" good":""}${activityDay===key?" sel":""}`} title={runs.length?`${runs.length} run${runs.length===1?"":"s"}`:"No runs"} onClick={()=>setActivityDay(activityDay===key?null:key)}>{day}{runs.length>0&&<span className="mark"/>}</button>;})}</div>
  <section className="panel"><div className="panel-heading"><div><p className="eyebrow">Runs</p><h2>{selectedRuns.length} {activityDay?"that day":"this month"}</h2></div></div><ul className="run-list">{selectedRuns.length===0?<li className="run-empty">Nothing ran in this period.</li>:selectedRuns.map(item=>{const done=(item.workers||[]).filter(worker=>worker.status==="completed").length;return <li key={item.id}><button className="run" onClick={()=>{void selectTask(item.id);goTo("task");}}><span className={`thread-dot ${item.status}`}/><span className="run-main"><strong>{taskTitle(item)}</strong><small>{item.project||"Axiom"} · {item.model_calls||0} model calls{(item.workers||[]).length>0?` · delegated ${done}/${(item.workers||[]).length} packages`:""}{item.usage?.totals?.cost?` · $${item.usage.totals.cost.toFixed(4)}`:""}</small></span><time>{item.created_at?new Date(item.created_at).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"}):""}</time><span className="run-status">{item.status}</span></button></li>;})}</ul></section>
 </>;
 // Each panel is defined once and placed by the view, so the sidebar can never claim
 // one thing while the page shows another.
 const teamBoard=<><div className="section-title"><div><p className="eyebrow">Team</p><h2>Agent workspace</h2></div></div><div className="pipeline-row"><div className={`pipeline${running?" live":""}`}><span style={{width:`${pipelinePercent}%`}}/></div><span className="pipeline-label">{running?`~${pipelinePercent}% · ${doneRoles}/${agents.length} roles · ${runningAgent?.name} ${runningStep?.rounds||0}/${roundBudget} rounds`:complete?`${pipelinePercent}% · ${doneRoles}/${agents.length} roles reported`:doneRoles>0?`Stopped · ${doneRoles}/${agents.length} roles`:"Idle"}</span></div><div className="agent-grid">{agents.map(({name,role,icon:Icon})=>{const state=states[name];const used=workflow.find(step=>step.agent===name)?.model;const tools=agentTools(name);return <article className={`agent-card ${state}`} key={name}><div className="agent-top"><span className="agent-icon"><Icon size={18}/></span><span className={`state-dot ${state}`}/></div><h3>{name}</h3><p>{role}</p><small className="agent-model" title={used||""}>{used||""}</small><div className="agent-state">{state==="working"?"Working now":state==="done"?<><Check size={13}/> Complete</>:state==="failed"?"Failed":state==="cancelled"?"Cancelled":"Waiting"}</div>{state==="working"&&tools>0&&<small className="agent-progress">{tools} tool call{tools===1?"":"s"} so far</small>}{name==="Coder"&&workers.length>0&&<small className="agent-progress">delegated {packagesDone}/{workers.length} packages</small>}</article>})}</div></>;
 const activityPanel=<section className="panel"><div className="panel-heading"><div><p className="eyebrow">Live</p><h2>Activity</h2></div><span className="event-count">{events.length}</span></div><div className="event-list" aria-live="polite">{events.length===0?<div className="empty-state"><Activity size={21}/><p>Activity appears here when you run a task.</p></div>:events.map((event,index)=><div className="event" key={event.agent+index}><span className="event-line"/><div><strong>{event.agent}</strong><p>{event.text}</p></div><time>{event.at?new Date(event.at).toLocaleTimeString():""}</time></div>)}</div></section>;
 const resultsPanel=<section className="panel"><button className="panel-heading panel-toggle" aria-expanded={outputOpen} onClick={()=>setOutputOpen(open=>!open)}><div><p className="eyebrow CC">Results</p><h2>Agent output</h2></div><span className="panel-toggle-side">{complete&&<span className="success-pill"><Check size={13}/> Review passed</span>}{outputOpen?<ChevronDown size={16}/>:<ChevronRight size={16}/>}</span></button>{outputOpen&&(workflow.length?<div className="output-ready"><div className="output-icon"><Network size={20}/></div><h3 title={summary}>{summary||"Task in progress"}</h3><p className="workspace-path">{workspace}</p><p>{modelCalls} model calls · {repairRound} repair rounds{workers.length>0?` · ${workers.length} subagent proposals`:""}</p>{verification&&<p>{verificationLabel(verification)}</p>}{files.length>0&&<ul className="generated-files">{files.map(file=><li key={file}>{file}</li>)}</ul>}{previewUrl&&<p className="workspace-path">Preview: <a href={previewUrl} target="_blank" rel="noreferrer">{previewUrl}</a></p>}<div className="workflow-list">{workflow.map((step,index)=><div className="workflow-step" key={step.agent}><span>{index+1}</span><div><strong>{step.agent}</strong><small title={step.assignment}>{step.assignment}</small>{step.result&&<pre className="agent-result">{step.result}</pre>}</div></div>)}</div>{workers.length>0&&<div className="workflow-list"><p className="eyebrow">Coder subagents</p>{workers.map(worker=><div className="workflow-step" key={worker.id}><span>↳</span><div><strong>{worker.name}</strong><small>{worker.status} · {worker.changed_files.length?worker.changed_files.join(", "):"no project changes"}</small></div></div>)}</div>}{taskId&&!running&&<><button className="continue-button" onClick={()=>void resumeTask()}>Resume this task</button><button className="continue-button" onClick={continueTask}>Continue in the prompt window</button><button className="continue-button" onClick={()=>void openPreview(taskId)}>Open the page it wrote</button><button className="continue-button" onClick={()=>void runChecks(taskId)} disabled={testing}>{testing?"Running its checks…":"Test it"}</button></>}</div>:<div className="empty-state"><Bot size={22}/><p>Agent results and verification reports will appear here.</p></div>)}</section>;
  return <main className="app-shell">
    <header className="topbar"><button className="icon-button menu-button" aria-label="Projects and threads" aria-expanded={navOpen} onClick={()=>setNavOpen(open=>!open)}><Menu size={18}/></button><div className="brand"><span className="brand-mark"><Sparkles size={17}/></span><span>AXIOM</span></div><div className="topbar-actions"><span className={`llm-status ${connected?"connected":""}`}><span/>{connected?"LLM connected":"LLM offline"}</span><button className="icon-button" aria-label="LLM settings" onClick={()=>setSettingsOpen(true)}><Settings size={18}/></button><span className="avatar">RF</span></div></header>
    {navOpen&&<div className="nav-backdrop" onClick={()=>setNavOpen(false)}/>}
    <aside className={`sidebar ${navOpen?"open":""}`}><nav aria-label="Views"><a className={`nav-item ${view==="task"?"active":""}`} href="#task" onClick={(e)=>{e.preventDefault();goTo("task");}}><Bot size={17}/> Thread</a><a className={`nav-item ${view==="activity"?"active":""}`} href="#activity" onClick={(e)=>{e.preventDefault();goTo("activity");}}><Activity size={17}/> Agent activity</a></nav><div className="sidebar-bottom"><div className="projects-head"><button className="projects-toggle" onClick={()=>{const anyClosed=groups.some(group=>openProjects[group.name]===false);setOpenProjects(Object.fromEntries(projectNames.map(name=>[name,!anyClosed])));}}>{groups.some(group=>openProjects[group.name]===false)?<ChevronRight size={14}/>:<ChevronDown size={14}/>} Projects</button><button className="icon-button" aria-label="New project" title="New project" onClick={()=>setNewProject("")}><Plus size={15}/></button></div>{newProject!==null&&<form className="new-project-form" onSubmit={(event)=>{event.preventDefault();const name=newProject.trim();if(!name)return;const next=Array.from(new Set([...extraProjects,name]));setExtraProjects(next);try{localStorage.setItem("axiom-projects",JSON.stringify(next));}catch{}setProject(name);setNewProject(null);startNewTask();setProject(name);setNavOpen(false);}}><input autoFocus value={newProject} onChange={e=>setNewProject(e.target.value)} placeholder="Project name" maxLength={60} aria-label="Project name"/><button className="run-button" type="submit">Create</button></form>}{groups.map(group=>{const open=openProjects[group.name]!==false;return <div className="project-group" key={group.name}><div className="project-row-wrap"><button className="project-row" aria-expanded={open} onClick={()=>setOpenProjects(current=>({...current,[group.name]:!open}))}>{open?<ChevronDown size={14}/>:<ChevronRight size={14}/>}<span className="project-dot violet"/><strong>{group.name}</strong><small>{group.tasks.length}</small></button><button className="thread-add" aria-label={`New thread in ${group.name}`} title="New thread in this project" onClick={()=>{startNewTask();setProject(group.name);setNavOpen(false);}}><Plus size={13}/></button><button className="project-delete" aria-label={`Delete the project ${group.name}`} title="Delete this project" onClick={()=>void deleteProject(group.name)}><X size={13}/></button></div>{open&&<div className="thread-list">{group.tasks.map(item=><div className={`thread-row ${item.id===taskId?"active":""}`} key={item.id}><button title={taskTitle(item)} className="thread-item" onClick={()=>void selectTask(item.id)}><span className={`thread-dot ${item.status}`}/><span className="thread-title">{taskTitle(item)}</span></button><button className="thread-delete" aria-label={`Delete ${taskTitle(item)}`} title="Delete this thread" onClick={()=>void deleteTask(item.id)}><X size={13}/></button></div>)}</div>}</div>;})}</div></aside>
    <section className="workspace">
      <div className="page-heading"><div><p className="eyebrow">{view==="activity"?"Agent activity":project.trim()||"Axiom"}</p><h1 title={view==="activity"?"":activeTitle}>{view==="activity"?monthLabel(activityMonth):activeTitle||"New thread"}</h1>{usageLine(usage)&&<p className="heading-note">{usageLine(usage)}</p>}</div><div className="system-status"><span/> {running?"Agents working":connected?"Backend ready":"Setup required"}</div></div>
      {view==="task"&&(composerOpen||taskId)&&composer}
      {view==="activity"?<>{calendarPage}{activityPanel}</>:taskId?<>{teamBoard}{resultsPanel}{testResult&&testPanel}</>:<section className="panel"><div className="empty-state"><Bot size={24}/><p>No thread open. Pick one on the left, or write a prompt above to start a new one.</p></div></section>}
    </section>
    {settingsOpen&&<div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)setSettingsOpen(false)}}><section className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title"><button className="modal-close" aria-label="Close" onClick={()=>setSettingsOpen(false)}><X size={18}/></button><p className="eyebrow">Backend connection</p><h2 id="settings-title">OpenRouter</h2><p className="modal-copy">Enter your API key to connect the local backend. It is saved in the backend&apos;s own credentials file (.axiom/credentials.env), never in this browser and never in a file Next.js watches while developing.</p><label>OpenRouter API key<input type="password" value={apiKey} onChange={e=>setApiKey(e.target.value)} placeholder={connected?"Key configured — enter a new key to replace it":"Paste your API key"} autoComplete="off"/></label><p className="modal-copy">{connected?"Backend key configured":"Backend key not configured"}</p><label>Available models<select value={models.some(option=>option.id===model)?model:""} onChange={e=>setModel(e.target.value)} disabled={!connected}><option value="">{connected?"Custom or backend default":"Connect a key to load models"}</option>{models.map(option=><option value={option.id} key={option.id}>{option.name}</option>)}</select></label><label>OpenRouter model ID<input value={model} onChange={e=>setModel(e.target.value)} placeholder="Use backend default" autoComplete="off"/></label><p className="modal-copy">Use models that support tool calling. The default runs every agent unless you override it below.</p><div className="agent-models"><p className="eyebrow">Model per agent</p>{agents.map(({name})=><label key={name}>{name}<select value={agentModels[name]||""} onChange={e=>setAgentModels(current=>({...current,[name]:e.target.value}))} disabled={!connected}><option value="">Use default model</option>{models.map(option=><option value={option.id} key={option.id}>{option.name}</option>)}</select></label>)}</div>{settingsError&&<p className="error-message" role="alert">{settingsError}</p>}<button className="connect-button" onClick={()=>void saveConnection()} disabled={savingSettings}>{savingSettings?"Saving connection…":"Save connection"}</button></section></div>}
  </main>;
}
