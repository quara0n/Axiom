import { NextResponse } from "next/server";
export const dynamic = "force-dynamic";
function hostName(value:string){try{return new URL(value).hostname.replace(/^\[|\]$/g,"").toLowerCase();}catch{return "";}}
function isLoopback(value:string){const name=hostName(value);return name==="localhost"||name==="127.0.0.1"||name==="::1";}
function selfOrigin(request:Request){
 const host=request.headers.get("host");
 const proto=request.headers.get("x-forwarded-proto")||"http";
 if(host){try{return new URL(proto+"://"+host).origin;}catch{}}
 return new URL(request.url).origin;
}
function originBlocked(request:Request){
 const site=(request.headers.get("sec-fetch-site")||"").toLowerCase();
 if(site==="cross-site")return "Cross-site requests are not allowed.";
 const origin=request.headers.get("origin");
 if(!origin)return "";
 if(isLoopback(origin))return "";
 // Opaque origins (sandboxed frames, file://) are only accepted when the browser
 // confirms the request stayed on this site; a remote page reports cross-site here.
 if(origin==="null"&&(site==="same-origin"||site==="same-site"||site==="none"))return "";
 return "Cross-origin requests are not allowed.";
}
async function proxy(request:Request, context:{params:Promise<{path:string[]}>}){
 // Compare against the Host the client actually used: Next normalises request.url to
 // its own origin, which made every loopback page look cross-origin.
 if(!isLoopback(selfOrigin(request)))return NextResponse.json({error:"The agent runtime is available in local Axiom only."},{status:403});
 const blocked=originBlocked(request);
 if(blocked)return NextResponse.json({error:blocked},{status:403});
 const {path}=await context.params;
 const route=path.join("/");
 if(!new RegExp("^(health|models|connection|mcp(?:/probe)?|tasks(?:/[a-zA-Z0-9-]+(?:/(?:cancel|preview|test))?)?)$").test(route))return NextResponse.json({error:"Unknown runtime route"},{status:404});
 const target=new URL(process.env.AXIOM_BACKEND_URL||"http://127.0.0.1:8000");
 if(target.protocol!=="http:"||!["localhost","127.0.0.1","[::1]"].includes(target.hostname))return NextResponse.json({error:"AXIOM_BACKEND_URL must be a loopback HTTP address."},{status:503});
 try{
  const body=request.method==="POST"?await request.text():undefined;
  if(body&&body.length>30000)return NextResponse.json({error:"Request too large"},{status:413});
  const response=await fetch(new URL("/api/"+route,target),{method:request.method,headers:{"Content-Type":"application/json"},body,cache:"no-store",signal:AbortSignal.timeout(10000)});
  return new Response(await response.text(),{status:response.status,headers:{"Content-Type":"application/json","Cache-Control":"no-store"}});
 }catch{return NextResponse.json({error:"Start the Axiom Python backend on 127.0.0.1:8000."},{status:503});}
}
export const GET=proxy;
export const POST=proxy;
export const DELETE=proxy;
