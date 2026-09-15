import { NextResponse } from "next/server";
export const dynamic = "force-dynamic";
async function proxy(request:Request, context:{params:Promise<{path:string[]}>}){
 const url=new URL(request.url);
 if(!["localhost","127.0.0.1","[::1]"].includes(url.hostname))return NextResponse.json({error:"The agent runtime is available in local Axiom only."},{status:403});
 const origin=request.headers.get("origin");
 if(origin&&origin!==url.origin)return NextResponse.json({error:"Cross-origin requests are not allowed."},{status:403});
 const {path}=await context.params;
 const route=path.join("/");
 if(!new RegExp("^(health|models|connection|tasks(?:/[a-zA-Z0-9-]+(?:/cancel)?)?)$").test(route))return NextResponse.json({error:"Unknown runtime route"},{status:404});
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
