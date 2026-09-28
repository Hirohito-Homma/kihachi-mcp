const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('src/kihachi_mcp/studio/static/index.html', 'utf8');
const source = html.slice(html.indexOf('    let dialogueAudio = null;'), html.indexOf('    let voiceRecorder = null;'));
let requests=[], played=0, paused=0;
const nodes = {'dialogue-speak':{checked:true},'dialogue-stop':{},'dialogue-voice-status':{}};
const context = vm.createContext({
  $: id=>nodes[id], AbortController,
  fetch: (_url, options)=>new Promise(resolve=>requests.push({resolve,options})),
  URL:{createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{}},
  Audio: class {constructor(src){this.src=src;}async play(){played++;}pause(){paused++;}}
});
vm.runInContext(source, context);
const response = {ok:true, blob:async()=>({})};
(async()=>{
  const old = vm.runInContext("speakDialogue('old')",context);
  vm.runInContext('stopDialogueAudio()',context);
  assert.equal(requests[0].options.signal.aborted,true);
  requests[0].resolve(response); await old;
  assert.equal(played,0,'stopped pending audio must never play');
  const first=vm.runInContext("speakDialogue('first')",context);
  const second=vm.runInContext("speakDialogue('second')",context);
  requests[2].resolve(response);await second;
  requests[1].resolve(response);await first;
  assert.equal(played,1,'only newest speech may play');
  nodes['dialogue-speak'].checked=false;
  nodes['dialogue-speak'].onchange();
  assert.equal(paused,1);
  console.log('Voice lifecycle: 3 race/stop checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
