// Generate from Python-exported schemas only; no hand-maintained wire models.
import {readFile,writeFile,mkdir,copyFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import openapiTS,{astToString} from 'openapi-typescript';
import {compile} from 'json-schema-to-typescript';
import ts from 'typescript';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const output=path.join(root,'contracts');
// The TypeScript compiler targets draft-07 tuple syntax. Preserve every
// prefixItems element while lowering 2020-12 schemas for generation only.
function compilerSchema(value){
  if(Array.isArray(value))return value.map(compilerSchema);
  if(value&&typeof value==='object'){
    const result=Object.fromEntries(Object.entries(value).map(([key,item])=>[key,compilerSchema(item)]));
    if(Array.isArray(result.prefixItems)){
      result.additionalItems=result.items??false;
      result.items=result.prefixItems;
      delete result.prefixItems;
    }
    return result;
  }
  return value;
}
const names={
  'ws-server':'WsServerMessage','ws-client':'WsClientMessage',
  'ipc-request':'RpcRequest','ipc-response':'RpcResponse','worker-event':'WorkerEvent',
  target:'TargetMessage','frame-catalog':'FrameCatalog','presented-frame':'PresentedFrame','replay-data':'ReplayData',
};
await mkdir(path.join(output,'generated'),{recursive:true});
const api=JSON.parse(await readFile(path.join(output,'openapi.json'),'utf8'));
// TS5 rejects recursive indexed interface properties (TS2502). Generate the
// recursive alias from its source schema, then reference that named alias.
const jsonValue=JSON.parse(JSON.stringify(api.components.schemas.JsonValue)
  .replaceAll('#/components/schemas/','#/$defs/'));
const recursive=await compile({$ref:'#/$defs/JsonValue',$defs:{JsonValue:jsonValue}},'JsonValue',{
  bannerComment:'/* Generated from Python contracts. Do not edit. */',enableConstEnums:false});
await writeFile(path.join(output,'generated/json-value.gen.ts'),recursive);
await writeFile(path.join(output,'types.gen.ts'),astToString(await openapiTS(api,{
  arrayLength:true,defaultNonNullable:false,inject:'import type { JsonValue } from "./generated/json-value.gen.js";',
  transform(_schema,options){
    if(options.path==='#/components/schemas/JsonValue')return ts.factory.createTypeReferenceNode('JsonValue');
  },
})));
const exports=[];
for(const [file,name] of Object.entries(names)){
  const schema=JSON.parse(await readFile(path.join(output,file+'.schema.json'),'utf8'));
  // A supplied root title avoids export-name drift as nested Pydantic models evolve.
  schema.title=name;
  const text=await compile(compilerSchema(schema),name,{bannerComment:'/* Generated from Python contracts. Do not edit. */',
    additionalProperties:false,enableConstEnums:false,maxItems:64,format:true});
  await writeFile(path.join(output,'generated',file+'.gen.ts'),text);
  exports.push(`export type { ${name} } from './generated/${file}.gen.js';`);
}
await writeFile(path.join(output,'messages.gen.ts'),exports.join('\n')+'\n');
// Optional frontend copy is explicit; a future frontend build does not become
// a prerequisite for validating backend schemas.
const destination=process.argv.indexOf('--frontend-dir');
if(destination!==-1){
  if(!process.argv[destination+1])throw new Error('--frontend-dir requires a path');
  // npm --prefix runs scripts from tools/contracts, so CLI destinations are
  // deliberately relative to backend/ rather than that implicit npm cwd.
  const frontend=path.resolve(root,process.argv[destination+1]);
  await mkdir(path.join(frontend,'generated'),{recursive:true});
  for(const name of ['types.gen.ts','messages.gen.ts','generated/json-value.gen.ts',...Object.keys(names).map(n=>'generated/'+n+'.gen.ts')])
    await copyFile(path.join(output,name),path.join(frontend,name));
  await mkdir(path.join(frontend,'schemas'),{recursive:true});
  for(const name of ['openapi.json',...Object.keys(names).map(n=>n+'.schema.json')])
    await copyFile(path.join(output,name),path.join(frontend,'schemas',name));
  await mkdir(path.join(frontend,'fixtures'),{recursive:true});
  await copyFile(path.join(output,'examples.json'),path.join(frontend,'fixtures/examples.json'));
  const examples=JSON.parse(await readFile(path.join(output,'examples.json'),'utf8'));
  await writeFile(path.join(frontend,'fixtures/joint-metadata.json'),
    JSON.stringify(examples['service/joint_metadata'].payload,null,2)+'\n');
}
console.log('Generated REST, WS, IPC and media TypeScript contracts');
