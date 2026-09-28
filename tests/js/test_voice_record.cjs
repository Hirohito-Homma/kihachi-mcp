const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync('src/kihachi_mcp/studio/static/index.html','utf8');
const source=html.slice(html.indexOf('    let voiceRecorder = null;'),html.indexOf('    const dialogueSession ='));
const ids=['dialogue-check-voice','dialogue-authorize-voice','dialogue-record','dialogue-send','dialogue-voice-status','dialogue-readiness','dialogue-utterance','dialogue-auto-send','dialogue-alternatives'];
const nodes=Object.fromEntries(ids.map(id=>[id,{disabled:false,value:'',textContent:'',checked:true}]));
nodes['dialogue-alternatives'].children=[];
nodes['dialogue-alternatives'].replaceChildren=()=>{nodes['dialogue-alternatives'].children=[];};
nodes['dialogue-alternatives'].append=child=>{nodes['dialogue-alternatives'].children.push(child);};
nodes['dialogue-auto-send'].checked=false;
let sends=0, trackStops=0, recorder;
let transcriptionResult={ok:true,transcript:'キックを短く'};
nodes['dialogue-send'].click=()=>{sends++;};
class Recorder{
  static isTypeSupported(type){return type.startsWith('audio/webm');}
  constructor(){this.state='inactive';recorder=this;}
  start(){this.state='recording';this.ondataavailable({data:new Blob([Buffer.alloc(150)])});}
  stop(){this.state='inactive';return this.onstop();}
}
const context=vm.createContext({
  $:id=>nodes[id], stopDialogueAudio(){},
  document:{createElement:()=>({textContent:'',onclick:null})},
  getJson:async()=>({asr_installed:true,ffmpeg_available:true,usage_description_present:true,on_device:true,available:true,authorization:'authorized',message:'ready'}),
  navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){trackStops++;}}]})}},
  window:{MediaRecorder:Recorder,addEventListener(){}},MediaRecorder:Recorder,
  Blob,Buffer,fetch:async()=>({ok:true,json:async()=>transcriptionResult}),
  setTimeout:()=>1,clearTimeout(){}
});
vm.runInContext(source,context);
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  await nodes['dialogue-record'].onclick();
  await recorder.stop();
  assert.equal(nodes['dialogue-utterance'].value,'キックを短く');
  assert.equal(sends,0,'recognized speech should wait for review by default');
  assert.equal(trackStops,1,'microphone must be released');
  nodes['dialogue-auto-send'].checked=true;
  await nodes['dialogue-record'].onclick();
  await recorder.stop();
  assert.equal(sends,1,'explicit auto-send toggle must be respected');
  transcriptionResult={ok:true,transcript:'Mutation Funkで',raw_transcript:'ミューテーションパンクで'};
  await nodes['dialogue-record'].onclick();
  await recorder.stop();
  assert.equal(nodes['dialogue-utterance'].value,'Mutation Funkで');
  assert.match(nodes['dialogue-voice-status'].textContent,/元の聞き取り/);
  assert.equal(sends,1,'a corrected transcript requires review even with auto-send enabled');
  transcriptionResult={ok:true,transcript:'Mutashon Funk',raw_transcript:'Mutashon Funk',alternatives:['Mutashon Funk','ミューテーションファンク']};
  await nodes['dialogue-record'].onclick();
  await recorder.stop();
  const alternative=nodes['dialogue-alternatives'].children[1];
  assert.equal(alternative.textContent,'ミューテーションファンク');
  alternative.onclick();
  assert.equal(nodes['dialogue-utterance'].value,'ミューテーションファンク');
  console.log('Voice record: transcript and auto-send checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
