const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function ui() {
  const elements = new Map();
  function element() {
    const classes = new Set(), listeners = new Map();
    return {textContent:'',className:'',value:'',children:[],style:{},classList:{toggle(name,on){if(on)classes.add(name);else classes.delete(name);},add(name){classes.add(name);},remove(name){classes.delete(name);},contains(name){return classes.has(name);}},
      listeners,replaceChildren(){this.children=[];},append(...items){this.children.push(...items);},addEventListener(name,handler){listeners.set(name,handler);},setAttribute(key,value){this[key]=value;},removeAttribute(key){delete this[key];},focus(){this.focused=true;},showModal(){this.open=true;},close(){this.open=false;}};
  }
  const document = {getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},createElement:element,querySelectorAll(){return [];}};
  const window = {location:{hash:'#overview'},addEventListener(){}};
  const context = vm.createContext({document,window,fetch:()=>new Promise(()=>{}),setInterval(){},setTimeout(){},clearTimeout(){},AbortSignal,Date,URL,TextEncoder});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'app.js'),'utf8'),context);
  return {elements,context,run(code){return vm.runInContext(code,context);},render(data){context.input=data;vm.runInContext('render(input)',context);}};
}
function fixture(live=true) {
  return {checking:false,detail_live:live,pi:{reachable:live},nas:{reachable:true,latency_ms:8},updated_at:'2026-10-07T19:00:00Z',history:[],events:[],
    remote:{health:{utc:'2026-10-07T19:00:00Z',temperature_c:52,memory_kib:{MemTotal:1024000,MemAvailable:512000},storage:{'/':{free_bytes:10737418240,total_bytes:21474836480}},uptime_seconds:7200,power:{undervoltage_now:true},services:{'pihole-FTL.service':{ActiveState:'active',NRestarts:'0'},'notification-bridge.service':{ActiveState:'active'},'renpho-sheets.service':{ActiveState:'active'}}},
      bridge:{announced:5,audio_submitted:2,queued:0,failed:0,alexa_configured:true,meme_mode:true,hue_configured:false},scale:{latest:{lb:169.5,utc:'2026-10-07T18:00:00Z',delivered:true},pending:0,raw_log_files:2},diagnostics_installed:false}};
}
test('Offline first load uses unavailable values, not invented readings',()=>{
  const app=ui();app.render({checking:false,pi:{reachable:false},nas:{reachable:true},history:[],events:[]});
  assert.equal(app.elements.get('temperature').textContent,'—');
  assert.equal(app.elements.get('pihole-badge').textContent,'Unavailable');
});
test('Live health renders data and highlights actual undervoltage',()=>{
  const app=ui();app.render(fixture());
  assert.equal(app.elements.get('temperature').textContent,'52 °C');
  assert.equal(app.elements.get('scale-weight').textContent,'169.5 lb');
  assert.equal(app.elements.get('pihole-badge').textContent,'Running');
  assert.match(app.elements.get('notice-title').textContent,/undervoltage/);
});
test('Connection loss relabels cached health and mode state',()=>{
  const app=ui();app.render(fixture());app.render(fixture(false));
  assert.equal(app.elements.get('pihole-badge').textContent,'Last seen');
  assert.match(app.elements.get('temperature-note').textContent,/Last seen/);
  assert.match(app.elements.get('modes-note').textContent,/last seen/);
});
test('Event content stays text instead of executable markup',()=>{
  const app=ui(), data=fixture();data.events=[{utc:'2026-10-07T19:00:00Z',kind:'warn',text:'<img src=x onerror=steal()>BAD'}];app.render(data);
  assert.equal(app.elements.get('events').children[0].children[1].textContent,data.events[0].text);
});
test('Hue connection follows observed pairing state and stale state',()=>{
  const app=ui(), data=fixture();data.remote.bridge.hue_configured=true;app.render(data);
  assert.equal(app.elements.get('hue-connection').textContent,'Hue bridge · linked locally');
  data.detail_live=false;app.render(data);
  assert.match(app.elements.get('hue-connection').textContent,/last seen/);
});
function cards(app) {
  const catalog={version:1,roast_threshold_lb:100,audio_enabled:true,sounds:{bruh:'https://example.com/bruh.mp3',boom:'https://example.com/boom.mp3'},special:{enabled:false,sound:'bruh',lights_target:'all lights'},good:[{sound:'bruh',before:'Good {weight}',after:''},{sound:'boom',before:'Nice {weight}',after:''}],bad:[{sound:'bruh',before:'Roast {weight}',after:'Finish',source:'credit'},{sound:'boom',before:'Other {weight}',after:''}]};
  app.context.catalogInput=catalog;app.elements.get('test-weight').value='150.5';
  app.run("liveRevision='a'.repeat(64);responseCatalog=readCardCatalog(catalogInput);liveCatalog=JSON.parse(JSON.stringify(catalogInput));draftParseValid=true;renderCards();");
  app.elements.get('catalog-json').value=JSON.stringify(catalog);
  return catalog;
}

test('Response edge-case matrix compiles audio positions, no-audio text, placeholders and Unicode safely',()=>{
  const cases=JSON.parse(fs.readFileSync(path.join(__dirname,'response-edge-cases.json'),'utf8'));
  for(const item of cases) {
    const app=ui();cards(app);app.context.item=item;
    app.run("editCardText('bad',0,item.text); if(item.audio === false) setCardAudio('bad',0,'');");
    assert.equal(app.run("cardViews.get('bad-0').card.classList.contains('invalid')"),!item.valid,item.name);
    if(item.valid) {
      assert.equal(app.run('responseCatalog.catalog.bad[0].before'),item.before,item.name);
      assert.equal(app.run('responseCatalog.catalog.bad[0].after'),item.after,item.name);
    }
  }
});

test('Weight logs show 12-hour times, unknown historical times and safe source text',()=>{
  const app=ui();app.context.weights={rows:[{id:'one',date:'2026-10-07',time:'00:04:06',lb:194.7,source:'<img onerror=steal()>',status:'pending'},{id:'two',date:'2025-08-01',time:'',lb:211,source:'tracker',status:'synced'}],timezone:'America/Vancouver',outbox_ok:true,cached:false,synced_at:'2026-10-07T10:00:00Z'};
  app.run('renderWeights(weights)');
  assert.equal(app.elements.get('weight-latest').textContent,'194.7 lb');
  assert.equal(app.elements.get('weight-waiting').textContent,'1');
  assert.equal(app.elements.get('weight-rows').children[0].children[1].textContent,'12:04 AM');
  assert.equal(app.elements.get('weight-rows').children[1].children[1].textContent,'—');
  assert.equal(app.elements.get('weight-rows').children[0].children[3].textContent,'<img onerror=steal()>');
  assert.equal(app.run("weightTime('14:59:02')"),'2:59 PM');
});

test('Weight search, pagination and tab switching preserve the card draft',()=>{
  const app=ui();cards(app);app.run("editCard('bad',0,'before','Keep my edit {weight}');");
  const draft=app.elements.get('catalog-json').value;
  app.context.weights={rows:Array.from({length:60},(_,i)=>({id:String(i),date:'2026-10-07',time:'12:00:00',lb:i===59?199:194.7,source:'Pi',status:'synced'})),outbox_ok:true};
  app.run('renderWeights(weights)');
  assert.equal(app.elements.get('weight-rows').children.length,50);
  app.run('weightPage=1;renderWeightRows();');
  assert.equal(app.elements.get('weight-rows').children.length,10);
  app.elements.get('weight-search').value='199';app.run('renderWeightRows();');
  assert.equal(app.elements.get('weight-rows').children.length,1);
  assert.equal(app.elements.get('weight-prev').disabled,true);
  app.run("window.location.hash='#weights';responseTab();");
  assert.equal(app.elements.get('weights').hidden,false);
  assert.equal(app.elements.get('responses').hidden,true);
  app.run("window.location.hash='#responses';responseTab();");
  assert.equal(app.elements.get('weights').hidden,true);
  assert.equal(app.elements.get('catalog-json').value,draft);
});

test('Cached weight history is clearly labelled and failed fetch retains rows',async()=>{
  const app=ui();app.context.weights={rows:[],outbox_ok:false,cached:true,error:'Pi unavailable. Showing saved history.',synced_at:'2026-10-07T10:00:00Z'};
  app.run('renderWeights(weights)');
  assert.match(app.elements.get('weight-status').textContent,/Pi unavailable/);
  assert.equal(app.elements.get('weight-waiting').textContent,'—');
  app.context.fetch=async()=>{throw Error('offline')};await app.run('loadWeights()');
  assert.match(app.elements.get('weight-status').textContent,/may be stale/);
  assert.equal(app.elements.get('weight-refresh').disabled,false);
});

test('Live weight events update logs and overview without losing edits or replaying a toast on delivery',()=>{
  const app=ui();cards(app);app.run("editCard('bad',0,'before','Keep this {weight}');");
  const draft=app.elements.get('catalog-json').value;
  function event(id,lb,status='pending') {
    return {data:JSON.stringify({rows:[{id:'renpho:'+id,date:'2026-10-08',time:'15:00:00',utc:'2026-10-08T22:00:00Z',lb,source:'Pi',status}],stream_live:true,outbox_ok:true,cached:false,timezone:'America/Vancouver'})};
  }
  app.context.message=event('first',193);app.run('receiveWeightEvent(message);');
  assert.equal(app.elements.get('scale-weight').textContent,'193 lb');
  app.context.initial={rows:[],outbox_ok:true};app.run('renderWeights(initial)');
  app.context.message=event('next',192);app.run('receiveWeightEvent(message);');
  assert.equal(app.elements.get('weight-latest').textContent,'192 lb');
  assert.equal(app.elements.get('weight-waiting').textContent,'1');
  assert.equal(app.elements.get('weight-new-notice').hidden,false);
  assert.match(app.elements.get('weight-new-text').textContent,/192 lb/);
  app.elements.get('weight-new-notice').hidden=true;
  app.context.message=event('next',192,'uploaded');app.run('receiveWeightEvent(message);');
  assert.equal(app.elements.get('weight-waiting').textContent,'0');
  assert.equal(app.elements.get('weight-new-notice').hidden,true);
  assert.equal(app.elements.get('catalog-json').value,draft);
  app.render(fixture());assert.equal(app.elements.get('scale-weight').textContent,'192 lb');
  app.run('weightStreamConnected=false;renderWeights(weightData);');
  assert.match(app.elements.get('weight-status').textContent,/reconnecting/);
});

test('A new live reading renders while the initial Google history fetch is still pending',()=>{
  const app=ui();app.run("window.location.hash='#weights';loadWeights();");
  app.context.message={data:JSON.stringify({rows:[{id:'renpho:new',date:'2026-10-08',time:'15:00:00',utc:'2026-10-08T22:00:00Z',lb:193,source:'Pi',status:'pending'}],stream_live:true,outbox_ok:true,cached:true,timezone:'America/Vancouver'})};
  app.run('receiveWeightEvent(message)');
  assert.equal(app.elements.get('weight-latest').textContent,'193 lb');
  assert.equal(app.elements.get('weight-rows').children.length,1);
  assert.equal(app.run('weightBusy'),true);
});
test('Cards preview weight as text and keep clip selectors on each card',()=>{
  const app=ui();cards(app);
  app.run(`editCard('bad',0,'before','Weight {weight} <img src=x onerror=steal()>');`);
  assert.equal(app.run("cardViews.get('bad-0').preview.textContent"),'At 150.5 lb: Weight 150.5 <img src=x onerror=steal()> Finish');
  assert.equal(app.run("cardViews.get('bad-0').preview.children.length"),0);
  assert.equal(app.run("cardViews.get('bad-0').sound.children.length"),3);
  assert.equal(app.elements.get('json-apply').disabled,false);
  app.elements.get('test-weight').value='';app.run('refreshCardPreviews();');
  assert.equal(app.run("cardViews.get('bad-0').play.disabled"),true);
});
test('Adding and editing cards syncs JSON and preserves settings and attribution',()=>{
  const app=ui();cards(app);
  app.run("editCard('bad',0,'before','Changed {weight}');editCard('bad',0,'sound','boom');addCard('good');");
  const draft=JSON.parse(app.elements.get('catalog-json').value);
  assert.equal(draft.bad[0].before,'Changed {weight}');assert.equal(draft.bad[0].sound,'boom');assert.equal(draft.bad[0].source,'credit');
  assert.equal(draft.good.length,3);assert.equal(draft.bad.length,2);assert.equal(draft.roast_threshold_lb,100);assert.equal(draft.special.enabled,false);
  assert.equal(app.run('liveRevision'),'a'.repeat(64));assert.equal(app.elements.get('json-apply').disabled,false);
});
test('Delete requires confirmation, cancel preserves card, confirm reindexes only draft',()=>{
  const app=ui();cards(app);
  app.run("requestDelete('bad',0);");assert.equal(app.elements.get('delete-dialog').open,true);
  assert.equal(app.run('responseCatalog.catalog.bad.length'),2);
  app.run('cancelDelete();');assert.equal(app.run('responseCatalog.catalog.bad.length'),2);assert.equal(app.elements.get('delete-dialog').open,false);
  app.run("requestDelete('bad',0);confirmDelete();");
  assert.equal(app.run('responseCatalog.catalog.bad.length'),1);
  assert.equal(app.run("cardViews.get('bad-0').text.value"),'Other {weight}');
  assert.equal(app.run('draftDirty'),true);assert.equal(app.elements.get('json-apply').disabled,false);
  assert.match(app.run("cardViews.get('bad-0').validation.textContent"),/2–100/);
  assert.equal(JSON.parse(app.elements.get('catalog-json').value).good.length,2);
});
test('Card playback sends exactly clicked card and uses draft route for edits',async()=>{
  const app=ui();cards(app);app.context.requests=[];
  app.context.fetch=async(url,options)=>{app.context.requests.push({url,body:JSON.parse(options.body)});return{ok:true,json:async()=>({message:'Queued'})};};
  await app.run("playResponse('bad',1);");
  assert.equal(app.context.requests[0].url,'/api/responses/play');assert.equal(app.context.requests[0].body.index,1);assert.equal(app.context.requests[0].body.pool,'bad');
  assert.equal(app.context.requests[0].body.weight,150.5);assert.equal('catalog' in app.context.requests[0].body,false);
  app.run("editCard('bad',1,'after','New ending');");await app.run("playResponse('bad',1);");
  assert.equal(app.context.requests[1].url,'/api/responses/play-draft');assert.equal(app.context.requests[1].body.catalog.bad[1].after,'New ending');
  assert.equal(app.run('draftDirty'),true);
});
test('Invalid JSON disables cards and valid JSON rebuilds both groups',()=>{
  const app=ui(),catalog=cards(app);
  app.elements.get('catalog-json').value='{broken';app.run('editorChanged();');
  assert.equal(app.run('cardViews.size'),0);assert.equal(app.elements.get('add-good').disabled,true);
  assert.match(app.elements.get('editor-result').textContent,/JSON needs fixing/);
  app.elements.get('catalog-json').value=JSON.stringify(catalog);app.run('editorChanged();');
  assert.equal(app.run('cardViews.size'),4);assert.equal(app.elements.get('add-good').disabled,false);
});
test('Apply automatically validates and saves the edited catalog with the live revision',async()=>{
  const app=ui();cards(app);app.run("editCard('good',0,'after','Test');");
  assert.equal(app.elements.get('json-apply').disabled,false);
  const requests=[];
  app.context.fetch=async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return{ok:true,json:async()=>app.run("({...responseCatalog,revision:'b'.repeat(64),message:'Applied'})")};};
  await app.run("editorOperation('apply');");
  assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/responses/apply');
  assert.equal(requests[0].body.catalog.good[0].after,'Test');assert.equal(requests[0].body.revision,'a'.repeat(64));
  assert.equal(app.run('draftDirty'),false);assert.equal(app.run('liveRevision'),'b'.repeat(64));assert.equal(app.elements.get('json-apply').disabled,true);
  app.run("editCard('good',0,'after','Changed again');");assert.equal(app.elements.get('json-apply').disabled,false);
});
test('Invalid placeholders turn only affected cards red, block requests, and clear when repaired',async()=>{
  const app=ui();cards(app);let requests=0;app.context.fetch=async()=>{requests++;};
  app.run("editCard('bad',0,'before','Wrong {weigth}');editCard('good',1,'after','Unmatched {');");
  assert.equal(app.run("cardViews.get('bad-0').card.classList.contains('invalid')"),true);
  assert.equal(app.run("cardViews.get('bad-1').card.classList.contains('invalid')"),false);
  assert.equal(app.run("cardViews.get('bad-0').text['aria-invalid']"),'true');
  assert.match(app.run("cardViews.get('bad-0').validation.textContent"),/Response text: use \{weight\}/);
  await app.run("editorOperation('apply');playResponse('bad',1);");assert.equal(requests,0);
  app.run("editCard('bad',0,'before','Fixed {weight}');editCard('good',1,'after','');");
  assert.equal(app.run("cardViews.get('bad-0').card.classList.contains('invalid')"),false);
  assert.equal(app.run("cardViews.get('bad-0').validation.textContent"),'');
  assert.equal(app.run("cardViews.get('bad-0').play.disabled"),false);
});
test('Semantic JSON errors keep cards visible with field reasons, including clip URL and text type',()=>{
  const app=ui(),catalog=cards(app);catalog.bad[0].before=42;catalog.bad[1].sound='missing';catalog.sounds.bruh='http://192.168.1.50/a.mp3';
  app.elements.get('catalog-json').value=JSON.stringify(catalog);app.run('editorChanged();');
  assert.equal(app.run('cardViews.size'),4);
  assert.match(app.run("cardViews.get('bad-0').validation.textContent"),/Text before audio: enter text/);
  assert.match(app.run("cardViews.get('bad-0').validation.textContent"),/public HTTPS/);
  assert.match(app.run("cardViews.get('bad-1').validation.textContent"),/existing clip/);
  assert.equal(app.run("cardViews.get('good-1').card.classList.contains('invalid')"),false);
});
test('Empty opening and closing are allowed with audio, but speech-only cards explain the missing line',()=>{
  const app=ui();cards(app);app.run("editCard('bad',0,'before','');editCard('bad',0,'after','');");
  assert.equal(app.run("cardViews.get('bad-0').validation.textContent"),'');
  app.run("responseCatalog.catalog.audio_enabled=false;markCatalogChanged();");
  assert.match(app.run("cardViews.get('bad-0').validation.textContent"),/Add response text/);
});
test('Backend validation rejection highlights its card and keeps unsaved text; conflicts do not mark cards invalid',async()=>{
  const app=ui();cards(app);app.run("editCard('bad',0,'after','Unsaved');");
  app.context.fetch=async()=>({ok:false,status:400,json:async()=>({error:'bad[0].after contains invalid speech.'})});
  await app.run("editorOperation('apply');");
  assert.equal(app.run("cardViews.get('bad-0').card.classList.contains('invalid')"),true);
  assert.match(app.run("cardViews.get('bad-0').validation.textContent"),/invalid speech/);
  assert.equal(app.run('draftDirty'),true);assert.equal(app.run('liveRevision'),'a'.repeat(64));
  app.run("editCard('bad',0,'after','Repaired');");
  app.context.fetch=async()=>({ok:false,status:409,json:async()=>({error:'Live JSON changed. Reload.'})});
  await app.run("editorOperation('apply');");
  assert.equal(app.run("cardViews.get('bad-0').card.classList.contains('invalid')"),false);
  assert.match(app.elements.get('editor-result').textContent,/Live JSON changed/);
});
test('Playback rejection restores editing and leaves the draft intact',async()=>{
  const app=ui();cards(app);app.run("editCard('good',1,'after','Unsaved');");
  app.context.fetch=async()=>({ok:false,json:async()=>({error:'Live JSON changed. Reload.'})});
  await app.run("playResponse('good',1);");
  assert.match(app.run("cardViews.get('good-1').result.textContent"),/Live JSON changed/);
  assert.equal(app.run("cardViews.get('good-1').text.disabled"),false);assert.equal(app.run('draftDirty'),true);
});
test('Meme Scale hides overview and keeps catalog on section navigation',()=>{
  const app=ui();cards(app);app.run("window.location.hash='#responses';responseTab();");
  assert.equal(app.elements.get('dashboard-view').hidden,true);assert.equal(app.elements.get('responses').hidden,false);
  app.run("window.location.hash='#overview';responseTab();");assert.equal(app.elements.get('dashboard-view').hidden,false);
});

test('Custom Audio is a subtab and switching preserves edited cards',()=>{
  const app=ui();cards(app);app.run("editCard('bad',0,'after','Keep me');audioLoaded=true;window.location.hash='#responses/audio';responseTab();");
  assert.equal(app.elements.get('dashboard-view').hidden,true);assert.equal(app.elements.get('audio-pane').hidden,false);assert.equal(app.elements.get('cards-pane').hidden,true);
  assert.equal(app.elements.get('audio-tab')['aria-current'],'page');assert.equal(app.elements.get('cards-tab')['aria-current'],undefined);
  app.run("window.location.hash='#responses';responseTab();");
  assert.equal(app.elements.get('cards-pane').hidden,false);assert.equal(app.run("responseCatalog.catalog.bad[0].after"),'Keep me');assert.equal(app.run('draftDirty'),true);
});
test('Choosing a file rejects non-MP3/oversized input and generates a unique clip name',()=>{
  const app=ui();cards(app);
  app.run("chooseAudioFile({name:'bad.exe',size:20});");assert.equal(app.run('selectedAudioFile'),null);
  app.run("chooseAudioFile({name:'bruh.mp3',size:25*1024*1024});");assert.equal(app.run('selectedAudioFile'),null);
  app.run("chooseAudioFile({name:'bruh.mp3',size:100});");assert.equal(app.elements.get('audio-name').value,'bruh_1');
  assert.equal(app.run('selectedAudioFile.name'),'bruh.mp3');assert.equal(app.elements.get('audio-url').value,'');
});
test('File processing is local only and does not publish or change the catalog',async()=>{
  const app=ui();cards(app);app.run("chooseAudioFile({name:'air_horn.mp3',size:100});");
  const requests=[], clip={id:'c'.repeat(64),name:'air_horn',duration:.5,bytes:100};
  app.context.fetch=async(url,options)=>{requests.push({url,options});return{ok:true,json:async()=>({clip})};};
  await app.run('processAudio();');
  assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/audio/file');assert.equal(requests[0].options.headers['Content-Type'],'audio/mpeg');
  assert.equal(requests[0].options.headers['X-Clip-Name'],'air_horn');assert.equal(app.run('audioClips.length'),1);
  assert.equal(app.run('draftDirty'),false);assert.equal(app.run('responseCatalog.catalog.sounds.air_horn'),undefined);
});
test('Link processing sends the chosen URL without publishing; errors leave cards unchanged',async()=>{
  const app=ui();cards(app);app.elements.get('audio-name').value='air_horn';app.elements.get('audio-url').value='https://example.com/horn.mp3';
  const requests=[];app.context.fetch=async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return{ok:false,json:async()=>({error:'This is not an MP3'})};};
  await app.run('processAudio();');
  assert.equal(requests[0].url,'/api/audio/link');assert.equal(requests[0].body.url,'https://example.com/horn.mp3');
  assert.match(app.elements.get('audio-result').textContent,/not an MP3/);assert.equal(app.run('draftDirty'),false);assert.equal(app.run('audioBusy'),false);
});
test('Explicit publication adds the hosted clip to the existing draft without applying it',async()=>{
  const app=ui();cards(app);app.run("editCard('bad',0,'after','Preserve me');");
  const clip={id:'c'.repeat(64),name:'air_horn',duration:.5,bytes:100}, hosted={...clip,url:'https://example.com/custom.mp3'},requests=[];
  app.context.clip=clip;app.context.fetch=async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return{ok:true,json:async()=>({clip:hosted})};};
  await app.run('publishAudio(clip);');
  assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/audio/publish');assert.equal(requests[0].body.publish_publicly,true);
  assert.equal(app.run('responseCatalog.catalog.sounds.air_horn'),hosted.url);assert.equal(app.run('responseCatalog.catalog.bad[0].after'),'Preserve me');
  assert.equal(app.run('draftDirty'),true);assert.equal(app.run('liveRevision'),'a'.repeat(64));
  assert.equal(app.run("cardViews.get('bad-0').sound.children.length"),4);
});
test('An existing sound name cannot be overwritten or published by adding a custom clip',async()=>{
  const app=ui();cards(app);app.context.clip={id:'c'.repeat(64),name:'bruh',duration:.5,bytes:100};let requests=0;
  app.context.fetch=async()=>{requests++;};await app.run('publishAudio(clip);');
  assert.equal(requests,0);assert.equal(app.run('responseCatalog.catalog.sounds.bruh'),'https://example.com/bruh.mp3');assert.equal(app.run('draftDirty'),false);
});
test('Single text field compiles clip position, validates placeholders, and keeps attribution',()=>{
  const app=ui();cards(app);
  app.run("editCardText('bad',0,'Damn, {weight}. {audio} Get out.');");
  let draft=JSON.parse(app.elements.get('catalog-json').value);
  assert.equal(draft.bad[0].before,'Damn, {weight}.');assert.equal(draft.bad[0].after,'Get out.');
  assert.equal(draft.bad[0].source,'credit');
  assert.equal(app.run("cardViews.get('bad-0').card.classList.contains('unsaved')"),true);
  app.run("editCardText('bad',0,'{audio} {audio}');");
  assert.equal(app.run("cardViews.get('bad-0').card.classList.contains('invalid')"),true);
  assert.match(app.run("cardViews.get('bad-0').validation.textContent"),/at most once/);
  app.run("editCardText('bad',0,'Weight {weigth}');");
  assert.match(app.run("cardViews.get('bad-0').validation.textContent"),/placeholder/);
  app.run("editCardText('bad',0,'Only speech before the sound.');");
  draft=JSON.parse(app.elements.get('catalog-json').value);
  assert.equal(draft.bad[0].before,'Only speech before the sound.');assert.equal(draft.bad[0].after,'');
  assert.equal(app.run("cardViews.get('bad-0').card.classList.contains('invalid')"),false);
  app.run("editCardText('bad',0,'{audio}');");
  assert.equal(app.run("cardViews.get('bad-0').validation.textContent"),'');
});

test('Group selector moves the same card, preserves text/sound/source, and leaves the live baseline untouched',()=>{
  const app=ui();cards(app);
  app.run("addCard('good');editCardText('good',2,'Start {audio} End {weight}');moveCard('good',2,'bad');");
  assert.equal(app.run('responseCatalog.catalog.good.length'),2);
  assert.equal(app.run('responseCatalog.catalog.bad.length'),3);
  assert.equal(app.run("cardViews.get('bad-2').text.value"),'Start {audio} End {weight}');
  assert.equal(app.run("cardViews.get('bad-2').group.value"),'bad');
  assert.equal(app.run("cardViews.get('bad-2').state.textContent"),'Unsaved');
  assert.equal(app.run('liveCatalog.bad.length'),2);
  app.run("moveCard('bad',0,'good');");
  assert.equal(app.run('responseCatalog.catalog.good[2].source'),'credit');
  assert.equal(app.run('responseCatalog.catalog.good[2].sound'),'bruh');
});

test('Card colors follow validity and live content, reverting turns green, Apply makes saved cards green',async()=>{
  const app=ui();cards(app);
  assert.equal(app.run("cardViews.get('good-0').state.textContent"),'Saved · ready');
  app.run("editCardText('good',0,'Changed {weight}');");
  assert.equal(app.run("cardViews.get('good-0').state.textContent"),'Unsaved');
  assert.equal(app.run("cardViews.get('good-1').state.textContent"),'Saved · ready');
  app.run("editCardText('good',0,'Bad {typo}');");
  assert.equal(app.run("cardViews.get('good-0').state.textContent"),'Invalid');
  app.run("editCardText('good',0,'Good {weight}');");
  assert.equal(app.run("cardViews.get('good-0').state.textContent"),'Saved · ready');
  assert.equal(app.run('draftDirty'),false);
  app.run("editCardText('good',0,'New {weight}');");
  app.context.fetch=async(url,options)=>{const catalog=JSON.parse(options.body).catalog;return {ok:true,json:async()=>({catalog,entries:[],revision:'b'.repeat(64),message:'Saved'})}};
  await app.run("editorOperation('apply')");
  assert.equal(app.run("cardViews.get('good-0').state.textContent"),'Saved · ready');
  assert.equal(app.run('draftDirty'),false);
});

test('Drag-and-drop prevents file navigation, selects one MP3, and rejects batches',()=>{
  const app=ui();cards(app);const drop=app.elements.get('audio-drop');let prevented=0;
  drop.listeners.get('dragover')({preventDefault(){prevented++;}});assert.equal(drop.classList.contains('drag-over'),true);
  drop.listeners.get('drop')({preventDefault(){prevented++;},dataTransfer:{files:[{name:'horn.mp3',size:100}]}});
  assert.equal(prevented,2);assert.equal(app.run('selectedAudioFile.name'),'horn.mp3');assert.equal(drop.classList.contains('drag-over'),false);
  drop.listeners.get('drop')({preventDefault(){},dataTransfer:{files:[{},{}]}});
  assert.match(app.elements.get('audio-result').textContent,/one MP3 at a time/);assert.equal(app.run('selectedAudioFile.name'),'horn.mp3');
});
