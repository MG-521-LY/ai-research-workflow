// Read local packaged public CLI/API implementation; never modify the application archive.
import fs from 'node:fs';
const archive='E:/APP/deepseekHarness/resources/app.asar';
const fd=fs.openSync(archive,'r');const first=Buffer.alloc(16);fs.readSync(fd,first,0,16,0);
const headerSize=first.readUInt32LE(4), jsonSize=first.readUInt32LE(12);
const header=Buffer.alloc(jsonSize);fs.readSync(fd,header,0,jsonSize,16);const tree=JSON.parse(header.toString());
const files=[];
function walk(node,prefix=''){for(const [name,item] of Object.entries(node.files||{})){const path=prefix+name;if(item.files)walk(item,path+'/');else files.push({path,...item});}}
walk(tree);
function read(path){const item=files.find(x=>x.path===path);if(!item)throw Error('No such archive path');if(item.unpacked)return fs.readFileSync(archive+'.unpacked/'+path,'utf8');const b=Buffer.alloc(item.size);fs.readSync(fd,b,0,b.length,8+headerSize+Number(item.offset));return b.toString('utf8');}
if(process.argv[2]==='paths')console.log(JSON.stringify(files.filter(x=>new RegExp(process.argv[3]).test(x.path)).map(x=>({path:x.path,size:x.size})),null,2));
else if(process.argv[2]==='list')console.log(JSON.stringify(files.filter(x=>process.argv[3]?x.path.includes(process.argv[3]):!x.path.includes('node_modules')&&/mcp|cli|remote|rpc|headless|server|package.json|main|preload/i.test(x.path)).map(x=>({path:x.path,size:x.size})),null,2));
else if(process.argv[2]==='read')process.stdout.write(read(process.argv[3]));
else if(process.argv[2]==='search'){
 const rx=new RegExp(process.argv[3],'gi');const scopes=process.argv[4]||'';let n=0;
 for(const item of files){if((item.path.includes('node_modules')&&!scopes)||item.size>20000000||!item.path.includes(scopes)||!/\.(js|mjs|json|md)$/.test(item.path))continue;
 const text=read(item.path);let hit;while((hit=rx.exec(text))&&n<20){console.log(JSON.stringify({path:item.path,index:hit.index,text:text.slice(Math.max(0,hit.index-150),hit.index+300)}));n++;}rx.lastIndex=0;}
}
fs.closeSync(fd);
