'use strict';
// Exercise the real frontend event handlers without a browser or external packages.
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
for(const text of ['Oberflächenlabor','Bildübersicht','Zurückgestellt','Größte Abweichung zuerst']){
 assert.ok(html.includes(text),'Missing correctly encoded UI text: '+text);
}
assert.ok(!html.includes('\ufffd'),'UTF-8 replacement characters in HTML');
assert.ok(!/\p{L}\?\p{L}/u.test(html),'Question marks inside words in HTML');
assert.ok(!/>\s*\?\s*</.test(html),'Question mark used instead of an icon in HTML');
assert.equal(html.replace('Aufgabe?','Aufgabe').replace('Modellen?','Modellen').includes('?'),false,
 'Unexpected question mark in UI text');
class Element {
 constructor(id,tag='DIV'){this.id=id;this.tagName=tag.toUpperCase();this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.checked=false;this.open=false;this.dataset={};this.options=[];this.children=[];this.listeners={};this.classList={toggle(){}};this.style={};}
 set innerHTML(value){this._html=value;this.options=[...value.matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)].map(m=>({value:m[1],text:m[2]}));if(this.tagName==='SELECT')this.value=this.options[0]?.value||'';if(this.id==='scoreButtons')this.children=[...value.matchAll(/data-score="(\d+)"/g)].map(m=>{const b=new Element('score-'+m[1],'BUTTON');b.dataset.score=m[1];return b;});}
 get innerHTML(){return this._html||'';}
 setAttribute(key,value){this[key]=value;}
 addEventListener(type,fn){this.listeners[type]=fn;}
 focus(){document.activeElement=this;}
 closest(selector){if(selector==='[data-score]'&&this.dataset.score)return this;return null;}
 showModal(){this.open=true;}
 close(){this.open=false;}
 getContext(){return {drawImage(){},beginPath(){},rect(){},fill(){},strokeRect(){}};}
 getBoundingClientRect(){return {left:0,top:0,width:500,height:400};}
 setPointerCapture(){}
}
const nodes={};
for(const match of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)){
 const node=nodes[match[2]]=new Element(match[2],match[1]);
 node.type=match[0].match(/\btype="([^"]+)"/)?.[1]||'text';
}
for(const match of html.matchAll(/<select[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g))nodes[match[1]].innerHTML=match[2];
const navs=['workspace','library','models','settings'].map(name=>{const n=new Element(name+'-nav','BUTTON');n.dataset.tab=name;return n;});
const document={activeElement:nodes.main,listeners:{},getElementById:id=>nodes[id],querySelectorAll(selector){return selector==='.nav'?navs:selector==='.tab'?['workspace','library','models','settings'].map(id=>nodes[id]):selector==='[data-score]'?nodes.scoreButtons.children:[];},querySelector(selector){if(selector==='dialog[open]')return Object.values(nodes).find(n=>n.tagName==='DIALOG'&&n.open)||null;return null;},addEventListener(type,fn){this.listeners[type]=fn;}};
const fixture=[1,2,3,4].map(id=>({id,relative_name:'image-'+id+'.jpg',origin_folder:'Charge A',width:100,height:80,status:'pending',chosen_date:'2026-10-01T09:00:00',date_source:'manual',warning:'',dates:{import:'2026-10-01T09:00:00'},label:null,draft:null,history:[],model_results:[],predictions:[],relevance:null}));
fixture[0].draft={crop:[15,15,85,65],score:null};
fixture[3].label={score:55,crop:'[20,20,75,60]',alternatives:'[]',revision:4};
const calls=[];
const state=()=>({counts:{total:fixture.length,pending:fixture.filter(r=>r.status==='pending').length,rated:fixture.filter(r=>r.status==='rated').length,ignored:0,removed:0},models:[{id:'manual',name:'Textur',installed:true}],runs:[],folders:['Charge A'],criteria:'Beispielkriterien',output:'output',token:'test'});
const clone=x=>JSON.parse(JSON.stringify(x));
const fetch=async(url,options)=>{
 const body=options?.body?JSON.parse(options.body):null;calls.push({url,body});let result;
 if(url==='/api/state')result=state();
 else if(url.startsWith('/api/images?')){const q=new URLSearchParams(url.split('?')[1]),status=q.get('status');result=fixture.filter(r=>status==='all'||r.status===status);}
 else if(url.startsWith('/api/image?id='))result=fixture.find(r=>r.id===Number(url.split('=')[1]));
 else if(url==='/api/draft'){fixture.find(r=>r.id===body.image_id).draft=body.draft;result={ok:true};}
 else if(url==='/api/save'){const row=fixture.find(r=>r.id===body.image_id);row.status='rated';row.draft=null;row.label={...body,crop:JSON.stringify(body.crop),alternatives:JSON.stringify(body.alternatives),revision:1};result={saved:'example.jpg',revision:1};}
 else if(url==='/api/client-error')throw Error('Unexpected frontend error: '+body.message);
 else throw Error('Unexpected request '+url);
 return {ok:true,status:200,json:async()=>clone(result)};
};
const errors=[];
const context=vm.createContext({document,window:{addEventListener(){}},sessionStorage:{getItem(){return null;},setItem(){}},localStorage:{getItem(){return null;},setItem(){}},crypto:{randomUUID:()=> 'stable-seed'},Image:class{constructor(){this.naturalWidth=100;this.naturalHeight=80;}async decode(){}},fetch,URLSearchParams,console,setTimeout(){return 1;},clearTimeout(){}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8'),context);
const settled=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
const key=async(value,extra={})=>{const event={key:value,preventDefault(){this.prevented=true;},...extra};await document.listeners.keydown(event);await settled();return event;};
const pointer=(canvas,type,x,y)=>canvas['onpointer'+type]({clientX:x,clientY:y,pointerId:1,button:0});
(async()=>{
 await settled();assert.equal(nodes.imageName.textContent,'image-1.jpg');assert.equal(nodes.save.disabled,true);
 assert.equal(nodes.sourceCanvas.hidden,false,'An open draft must allow marking immediately, without Escape');
 assert.equal(nodes.cropCanvas.hidden,true);
 pointer(nodes.sourceCanvas,'down',50,50);pointer(nodes.sourceCanvas,'move',450,350);pointer(nodes.sourceCanvas,'up',450,350);
 assert.equal(nodes.cropCanvas.hidden,false,'Completing the rectangle must still enlarge the crop');
 const previewWidth=nodes.cropCanvas.width,previewHeight=nodes.cropCanvas.height;
 pointer(nodes.cropCanvas,'down',125,100);pointer(nodes.cropCanvas,'move',375,300);
 assert.equal(nodes.cropCanvas.width,previewWidth,'The enlarged image must stay fixed while drawing');
 assert.equal(nodes.cropCanvas.height,previewHeight);
 pointer(nodes.cropCanvas,'up',375,300);
 pointer(nodes.cropCanvas,'down',100,80);pointer(nodes.cropCanvas,'move',400,320);nodes.cropCanvas.onpointercancel();
 await key('3');await key('w');await key('w');await key('w');assert.equal(nodes.scoreValue.textContent,'33');
 await key('s');assert.equal(nodes.scoreValue.textContent,'32');await key('>');assert.equal(nodes.scoreValue.textContent,'33');
 assert.ok(nodes.exportPreview.textContent.includes('_[image-1]_33.jpg'));
 await key('d');assert.equal(nodes.imageName.textContent,'image-2.jpg');assert.equal(fixture[0].draft.score,33);
 assert.deepEqual(fixture[0].draft.crop,[30,25,70,55],'Direct preview refinement must use original-image coordinates; cancellation keeps the old crop');
 await key('a');assert.equal(nodes.imageName.textContent,'image-1.jpg');assert.equal(nodes.scoreValue.textContent,'33');
 assert.equal(nodes.sourceCanvas.hidden,false,'Returning to a draft must not require Escape before drawing');
 await key('ArrowRight');assert.equal(nodes.imageName.textContent,'image-1.jpg');
 await key('c');assert.equal(nodes.sourceCanvas.hidden,false);await key('d');await key('s',{shiftKey:true});
 assert.equal(nodes.imageName.textContent,'image-1.jpg');
 await key('F');assert.equal(nodes.cropCanvas.hidden,false);assert.equal(nodes.sourceCanvas.hidden,true);
 assert.equal(calls.filter(c=>c.url==='/api/save').length,0,'F during crop editing must only confirm the crop');
 await key('f',{repeat:true});assert.equal(calls.filter(c=>c.url==='/api/save').length,0);
 await key('Escape');assert.equal(nodes.sourceCanvas.hidden,false);await key('c');await key('f');await key('f');
 const saved=calls.find(c=>c.url==='/api/save');assert.equal(saved.body.score,33);assert.equal(saved.body.expected_revision,0);assert.equal(saved.body.crop.length,4);assert.equal(nodes.imageName.textContent,'image-2.jpg');
 await key('0');assert.equal(nodes.scoreValue.textContent,'0 · nicht bewertbar');await key('>');assert.equal(nodes.scoreValue.textContent,'0 · nicht bewertbar');
 await key('9');for(let i=0;i<12;i++)await key('w');assert.equal(nodes.scoreValue.textContent,'99');
 document.activeElement=nodes.alternatives;await key('3');await key('a');await key('w');await key('f');
 assert.equal(nodes.scoreValue.textContent,'99');assert.equal(nodes.imageName.textContent,'image-2.jpg');
 assert.equal(calls.filter(c=>c.url==='/api/save').length,1,'Typing f in a text field must not save');
 await key('Escape');assert.ok(['sourceCanvas','cropCanvas'].includes(document.activeElement.id));
 await key('F1');assert.equal(nodes.helpDialog.open,true);nodes.helpDialog.close();
 await key('2',{altKey:true});assert.equal(nodes.library.hidden,false);assert.equal(nodes.workspace.hidden,true);await key('1',{altKey:true});assert.equal(nodes.workspace.hidden,false);
 document.activeElement=nodes.fineSlider;await key('f');assert.equal(calls.filter(c=>c.url==='/api/save').length,2,'F saves with slider focused');
 await key('f',{repeat:true});assert.equal(calls.filter(c=>c.url==='/api/save').length,2,'Holding F must not save the next image');
 await key('0');document.activeElement=nodes.scoreButtons.children.find(b=>b.dataset.score==='0');await key('f');
 assert.equal(calls.filter(c=>c.url==='/api/save').length,3,'F saves with rating button focused');
 assert.equal(nodes.imageName.textContent,'image-4.jpg');
 assert.equal(nodes.sourceCanvas.hidden,false,'A reopened human label must also allow immediate marking');
 await key('4');await key('w');await key('w');document.activeElement=nodes.trainingEnabled;await key('F');
 assert.deepEqual(calls.filter(c=>c.url==='/api/save').map(c=>c.body.score),[33,99,0,42]);
 assert.equal(calls.filter(c=>c.url==='/api/client-error').length,0);
 console.log('PASS: immediate crop drawing on drafts and reopened labels, direct preview refinement/cancellation, UTF-8 labels, WASD/F controls and focus protection.');
})().catch(error=>{console.error(error);process.exitCode=1;});
