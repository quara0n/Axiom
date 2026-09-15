// Compatibility entrypoint: task execution now goes through the backend.
import { POST as proxy } from "../runtime/[...path]/route";
export async function POST(request:Request){return proxy(request,{params:Promise.resolve({path:["tasks"]})});}
