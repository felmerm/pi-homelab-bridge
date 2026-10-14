const $ = id => document.getElementById(id);
const set = (id, value) => { $(id).textContent = value; };
const number = (value, decimals = 0) => Number.isFinite(value) ? value.toLocaleString(undefined, {maximumFractionDigits: decimals}) : '—';
const date = value => value ? new Date(value).toLocaleString(undefined, {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}) : '—';
const clock = value => value ? new Date(value).toLocaleTimeString(undefined, {hour:'numeric', minute:'2-digit'}) : '—';
function badge(id, text, kind = '') { set(id, text); $(id).className = 'badge ' + kind; }
function stateLabel(value) { return value === true ? 'Reachable' : value === false ? 'Unreachable' : 'Checking…'; }
function serviceBadge(id, service, live) {
  if (!live) return badge(id, service ? 'Last seen' : 'Unavailable');
  const active = service?.ActiveState;
  badge(id, active === 'active' ? 'Running' : active || 'Unknown', active === 'active' ? 'ok' : 'warn');
}
function render(data) {
  const pi = data.pi?.reachable, nas = data.nas?.reachable, live = data.detail_live === true;
  $('pi-dot').className = 'dot ' + (pi === true ? 'blue' : pi === false ? 'gold' : '');
  $('nas-dot').className = 'dot ' + (nas === true ? 'green' : nas === false ? 'gold' : '');
  set('pi-status', stateLabel(pi)); set('nas-status', stateLabel(nas));
  set('updated', data.updated_at ? 'Checked ' + clock(data.updated_at) : 'First check running');
  $('notice').classList.toggle('ok', live);
  if (data.collection_error) {
    set('notice-title', 'Status check unavailable'); set('notice-text', data.collection_error);
  } else if (data.checking) {
    set('notice-title', 'Checking your lab'); set('notice-text', 'Connecting to the Pi and NAS. Status updates automatically.');
  } else if (!pi) {
    set('notice-title', 'The Pi isn’t answering');
    set('notice-text', (nas ? 'The NAS responds, so this PC can still reach the home network. ' : 'The NAS is also unreachable. ') + 'Live Pi readings are unavailable. A lost connection alone does not prove a crash.');
  } else if (!live) {
    set('notice-title', 'Pi SSH port is reachable; details unavailable');
    set('notice-text', (data.ssh_error || 'Checking authenticated SSH access') + '. Previously collected details are marked below.');
  } else {
    set('notice-title', 'Your Pi is connected');
    set('notice-text', 'Service state and health readings are live. Alexa counts indicate submitted requests, not confirmed audible playback.');
    if (data.remote?.health?.power?.undervoltage_now) {
      $('notice').classList.remove('ok');
      set('notice-title','Pi connected · undervoltage detected');
      set('notice-text','The current sample reports low voltage. The power supply or cable needs attention; this warning alone does not establish the cause of an outage.');
    } else if (nas === false) {
      $('notice').classList.remove('ok');
      set('notice-title','Pi connected · NAS unreachable');
      set('notice-text','Pi health data is live, but the NAS web interface isn’t responding.');
    }
  }
  const remote = data.remote, health = remote?.health, services = health?.services || {}, sampleNote = live ? 'Live · ' + clock(health?.utc) : health ? 'Last seen · ' + date(health.utc) : 'Waiting for a live sample';
  set('services-note', live ? 'Live from the Pi · ' + clock(health?.utc) : remote ? 'Previously seen data · ' + date(health?.utc) : 'Current Pi service state unavailable');
  set('temperature', health?.temperature_c == null ? '—' : number(health.temperature_c,1) + ' °C');
  set('temperature-note', sampleNote);
  const mem = health?.memory_kib, used = mem?.MemTotal != null && mem?.MemAvailable != null ? (mem.MemTotal - mem.MemAvailable) / 1024 : null;
  set('memory', used == null ? '—' : number(used) + ' MB');
  set('memory-note', mem ? number(mem.MemAvailable / 1024) + ' MB available · ' + (live ? 'live' : 'last seen') : 'Pi 3B+ · approximately 1 GB');
  const memoryPercent = mem?.MemTotal ? Math.min(100,Math.max(0,100*(mem.MemTotal-mem.MemAvailable)/mem.MemTotal)) : 0;
  // A fixed gradient string uses only a bounded number, never remote CSS.
  $('memory-meter').style.backgroundImage = `linear-gradient(90deg,var(--blue) ${memoryPercent}%,transparent ${memoryPercent}%)`;
  const disk = health?.storage?.['/'];
  set('storage', disk ? number(disk.free_bytes / 1073741824,1) + ' GB' : '—');
  set('storage-note', disk ? number(100*(disk.total_bytes-disk.free_bytes)/disk.total_bytes,1) + '% used · ' + (live ? 'live' : 'last seen') : sampleNote);
  const uptime = health?.uptime_seconds;
  set('uptime', uptime == null ? '—' : uptime >= 86400 ? number(uptime/86400,1) + ' days' : uptime >= 3600 ? number(uptime/3600,1) + ' hrs' : number(uptime/60) + ' min');
  set('uptime-note', health ? sampleNote : 'Boot ID unavailable');
  serviceBadge('pihole-badge',services['pihole-FTL.service'],live);
  serviceBadge('alexa-badge',services['notification-bridge.service'],live);
  serviceBadge('scale-badge',services['renpho-sheets.service'],live);
  badge('nas-badge', nas === true ? 'Reachable' : nas === false ? 'Offline' : 'Checking',nas === true ? 'ok' : nas === false ? 'warn' : '');
  set('blocklists',number(remote?.pihole_enabled_lists));
  const restart = services['pihole-FTL.service']?.NRestarts;
  set('pihole-restarts', restart == null ? '—' : String(restart));
  const bridge = remote?.bridge;
  set('hue-connection', bridge ? (bridge.hue_configured ? 'Hue bridge · linked locally' : 'Hue bridge · not linked') + (live ? '' : ' · last seen') : 'Hue bridge · status unavailable');
  set('alexa-count',bridge ? `${number(bridge.announced + (bridge.speak_submitted || 0))} / ${number(bridge.audio_submitted)}` : '—');
  set('alexa-queue',bridge ? `${number(bridge.queued)} / ${number(bridge.failed)}` : '—');
  set('alexa-note', bridge ? bridge.alexa_configured ? 'Amazon account linked · ' + (live ? 'live' : 'last seen') : 'Amazon account needs linking' : 'Bridge status requires Pi access');
  const observedScale = remote?.scale;
  const scale = latestLiveScale && weightStreamConnected && (!observedScale?.latest?.utc || latestLiveScale.utc >= observedScale.latest.utc)
    ? {...observedScale, latest:latestLiveScale, pending:livePending} : observedScale;
  set('scale-weight',scale?.latest ? number(scale.latest.lb,1) + ' lb' : '—');
  if (scale?.latest) $('scale-weight').title = date(scale.latest.utc) + (scale.latest.delivered ? ' · Uploaded to Sheets' : ' · Waiting for upload');
  set('scale-pending',number(scale?.pending));
  set('nas-latency', nas ? number(data.nas.latency_ms) + ' ms · TCP' : 'Unreachable');
  if (health?.power) {
    const power = health.power;
    set('power-note',(power.undervoltage_now ? 'Undervoltage detected at this sample.' : power.undervoltage_since_boot ? 'Undervoltage occurred earlier in this boot.' : 'No undervoltage flags in this sample.') + ' ' + (live ? 'Live' : 'Last seen') + ' · ' + date(health.utc));
  } else set('power-note','Last verified check, Oct 6: undervoltage and throttling. No current power sample available.');
  set('diagnostics-note', remote ? remote.diagnostics_installed ? 'Pi tools present. Sampler timer: ' + (services['pi-diagnostics.timer']?.ActiveState || 'unknown') + (live ? '.' : ' at last check.') : 'PC recorder active. Pi diagnostics are prepared but not installed.' : 'PC records connection checks. Pi-side logs and health sampler await installation.');
  if (bridge && typeof bridge.meme_mode === 'boolean') {
    set('modes-note','Meme mode ' + (bridge.meme_mode ? 'on' : 'off') + ' · Hue ' + (bridge.hue_configured ? 'linked' : 'not linked') + ' · ' + (live ? 'live' : 'last seen') + '. Above-230 scene ' + (scale?.reactions?.special_enabled ? 'enabled.' : 'paused.'));
  } else set('modes-note','Toggle and direct Hue support prepared; current mode state unavailable. Above-230 scene paused at last setup.');
  if (data.backup) set('backup-note', data.backup.present ? 'Encrypted backup · ' + date(data.backup.created) + (data.backup.includes_scale === false ? '. Predates scale setup; it needs refreshing.' : '. Backup coverage needs checking.') : 'Backup metadata found, archive missing.');
  else set('backup-note','No local encrypted backup found.');
  if (scale) set('raw-note', `${number(scale.raw_log_files)} raw log files on the Pi` + (scale.raw_last_write_utc ? ' · Last write ' + date(scale.raw_last_write_utc) : '') + '. Packet contents stay on the Pi. ' + (live ? '' : 'Last-seen status.'));
  else set('raw-note','Raw scale packets stay on the Pi. Current log-file status unavailable.');
  renderHistory(data.history || []); renderEvents(data.events || []);
}
function renderHistory(points) {
  const element = $('history'); element.replaceChildren(); element.classList.toggle('empty',!points.length);
  if (!points.length) element.textContent = 'No connection records yet.';
  for (const point of points) {
    const bar = document.createElement('span'); bar.className = point.pi === true ? 'ok' : point.pi === false ? 'warn' : '';
    bar.title = date(point.utc) + ' · Pi ' + stateLabel(point.pi) + ' · NAS ' + stateLabel(point.nas);
    element.append(bar);
  }
  set('history-start',points.length ? clock(points[0].utc) : 'No samples yet');
  set('history-end',points.length ? clock(points[points.length-1].utc) : '—');
  set('sample-count', points.length + ' checks');
}
function renderEvents(events) {
  $('events').replaceChildren();
  if (!events.length) { const li = document.createElement('li'); li.className = 'dim'; li.textContent='No connection changes recorded yet.'; $('events').append(li); }
  for (const event of events) {
    const li = document.createElement('li'), dot = document.createElement('span'), label = document.createElement('span'), time = document.createElement('time');
    dot.className = 'dot ' + (event.kind === 'ok' ? 'blue' : 'gold'); label.textContent = event.text; time.textContent = clock(event.utc); time.dateTime = event.utc;
    li.append(dot,label,time); $('events').append(li);
  }
}
let busy = false;
async function refresh(manual = false) {
  if (busy) return; busy = true; $('refresh').disabled=true;
  try {
    const response = await fetch('/api/status' + (manual ? '?refresh=1' : ''),{cache:'no-store',signal:AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error('Status unavailable');
    render(await response.json());
  } catch {
    $('notice').classList.remove('ok');
    set('notice-title','Dashboard connection lost'); set('notice-text','The local dashboard server isn’t answering. Shown values may be stale.');
    set('updated','Dashboard disconnected'); set('services-note','Previously displayed data · server disconnected');
    set('pi-status','Last shown · dashboard offline'); set('nas-status','Last shown · dashboard offline');
    for (const id of ['pihole-badge','alexa-badge','scale-badge','nas-badge']) badge(id,'Last shown');
    for (const id of ['temperature-note','memory-note','storage-note','uptime-note']) set(id,'Last shown · dashboard offline');
  } finally { busy = false; $('refresh').disabled=false; }
}
$('refresh').addEventListener('click',async () => { await refresh(true); setTimeout(() => refresh(),4000); });
set('clock',new Date().toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'}));
refresh(); setInterval(() => refresh(),30000);

let responseCatalog = null, responseBusy = false, catalogBusy = false;
let liveRevision = null, draftDirty = false, draftParseValid = false;
let liveCatalog = null;
const cardTextDrafts = new WeakMap();
let draftIssues = [], remoteIssue = null;
let pendingDelete = null;
let audioBusy = false, audioLoaded = false, audioClips = [], selectedAudioFile = null;
let weightData = null, weightBusy = false, weightPage = 0;
let weightStreamConnected = false, latestLiveScale = null, livePending = 0;
let weightEventSnapshot = null;
let lastLiveReading = null, weightNoticeTimer = null, liveBaselineReceived = false;
const cardViews = new Map();
const soundNames = {bruh:'Bruh',fah:'FAHHHH',oof:'Roblox oof',bell:'Undertaker bell',boom:'Vine boom',laugh:'Oh no no no laugh',wow:'Anime wow',rizz:'Rizz',damn_son:'Damn son'};
const poolNames = {good:'Good',bad:'Bad'};
function stableJSON(value) {
  if (Array.isArray(value)) return '[' + value.map(stableJSON).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + stableJSON(value[key])).join(',') + '}';
  return JSON.stringify(value);
}
function cardSignature(catalog,pool,entry) {
  return stableJSON({pool,entry,clip:catalog.sounds[entry?.sound],audio:catalog.audio_enabled,cutoff:catalog.roast_threshold_lb ?? 100});
}
function savedCardCounts() {
  const counts = new Map();
  if (liveCatalog) for (const pool of ['good','bad']) for (const entry of liveCatalog[pool]) {
    const key = cardSignature(liveCatalog,pool,entry); counts.set(key,(counts.get(key) || 0) + 1);
  }
  return counts;
}
function cardText(entry) {
  if (cardTextDrafts.has(entry)) return cardTextDrafts.get(entry);
  const before = typeof entry?.before === 'string' ? entry.before : '', after = typeof entry?.after === 'string' ? entry.after : '';
  return after ? before + (before ? ' ' : '') + '{audio} ' + after : before || '{audio}';
}
function sampleWeight() {
  const value = Number($('test-weight').value);
  return $('test-weight').value.trim() && Number.isFinite(value) && value >= 7 && value <= 396 ? Math.round(value * 10) / 10 : null;
}
function readCardCatalog(catalog) {
  if (!catalog || typeof catalog !== 'object' || !catalog.sounds || typeof catalog.sounds !== 'object' || Array.isArray(catalog.sounds))
    throw new Error('Include a sounds object and good/bad arrays.');
  const entries = [];
  for (const pool of ['good','bad']) {
    if (!Array.isArray(catalog[pool])) throw new Error('Include good and bad response arrays.');
    catalog[pool].forEach((entry,index) => {
      entries.push({pool,index,sound:typeof entry?.sound === 'string' ? entry.sound : '',
        before:typeof entry?.before === 'string' ? entry.before : '',after:typeof entry?.after === 'string' ? entry.after : ''});
    });
  }
  return {catalog,entries,revision:liveRevision,audio_enabled:catalog.audio_enabled === true};
}
function catalogIssues(catalog) {
  const issues = [], object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const add = (message,pool = null,index = null,field = null) => issues.push({message,pool,index,field});
  const allowed = ['version','roast_threshold_lb','audio_enabled','sounds','good','bad','special'];
  for (const key of Object.keys(catalog)) if (!allowed.includes(key)) add('Unknown setting: ' + key + '. Remove it in Advanced JSON.');
  if (catalog.version !== 1) add('version must be 1.');
  if (typeof catalog.audio_enabled !== 'boolean') add('audio_enabled must be true or false.');
  const cutoff = catalog.roast_threshold_lb ?? 100;
  if (typeof cutoff !== 'number' || !Number.isFinite(cutoff) || cutoff < 7 || cutoff > 396) add('roast_threshold_lb must be between 7 and 396.');
  const sounds = catalog.sounds, soundErrors = new Map();
  if (Object.keys(sounds).length < 1 || Object.keys(sounds).length > 40) add('Include 1–40 named clips in sounds.');
  for (const [name,url] of Object.entries(sounds)) {
    let reason = '';
    if (!/^[a-z0-9_]{1,40}$/.test(name)) reason = 'Clip names need 1–40 lowercase letters, numbers or underscores.';
    else {
      try {
        if (typeof url !== 'string' || !url || [...url].length > 2048 || /[\s\\\x00-\x1f]/u.test(url)) throw new Error();
        const parsed = new URL(url), host = parsed.hostname;
        if (parsed.protocol !== 'https:' || !host.includes('.') || parsed.port || parsed.username || parsed.password || parsed.hash ||
            !/^[a-zA-Z0-9.-]+$/.test(host) || /^\d+\.\d+\.\d+\.\d+$/.test(host) || /\.(local|localhost|internal|lan)$/i.test(host)) throw new Error();
      } catch { reason = 'Use a public HTTPS clip URL on port 443, without a login or fragment.'; }
    }
    if (reason) { soundErrors.set(name,reason); add('Clip “' + name + '”: ' + reason + ' Edit it in Advanced JSON.'); }
  }
  for (const pool of ['good','bad']) {
    const entries = catalog[pool];
    if (entries.length < 2 || entries.length > 100) {
      const message = poolNames[pool] + ' group needs 2–100 cards; it has ' + entries.length + '.';
      add(message);
      entries.forEach((entry,index) => add(message,pool,index));
    }
    entries.forEach((entry,index) => {
      if (!object(entry)) { add('This card must be an object in Advanced JSON.',pool,index); return; }
      for (const key of Object.keys(entry)) if (!['sound','before','after','source','audio_enabled'].includes(key)) add('Unknown card field: ' + key + '. Remove it in Advanced JSON.',pool,index);
      if (Object.hasOwn(entry,'audio_enabled') && typeof entry.audio_enabled !== 'boolean') add('Audio setting must be true or false.',pool,index,'sound');
      if (typeof entry.sound !== 'string' || !Object.hasOwn(sounds,entry.sound)) add('Meme audio: choose an existing clip.',pool,index,'sound');
      else if (soundErrors.has(entry.sound)) add('Meme audio: ' + soundErrors.get(entry.sound),pool,index,'sound');
      const multipleAudio = (cardTextDrafts.get(entry)?.match(/\{audio\}/g) || []).length > 1;
      if (multipleAudio) add('Response text: use {audio} at most once. A card plays one meme clip.',pool,index,'before');
      for (const field of ['before','after']) {
        const text = Object.hasOwn(entry,field) ? entry[field] : '', label = field === 'before' ? 'Text before audio' : 'Text after audio';
        if (typeof text !== 'string') { add(label + ': enter text, or leave it empty.',pool,index,field); continue; }
        if ([...text].length > 700) add(label + ': use at most 700 characters.',pool,index,field);
        if (!multipleAudio && /[{}]/.test(text.replaceAll('{weight}',''))) add('Response text: use {weight} for pounds and {audio} for the clip. Fix any other placeholder or unmatched brace.',pool,index,field);
        if ([...text].some(char => { const code = char.codePointAt(0); return !([9,10,13].includes(code) || code >= 32 && code <= 0xd7ff || code >= 0xe000 && code <= 0xfffd || code >= 0x10000 && code <= 0x10ffff); }))
          add(label + ': remove unsupported control characters.',pool,index,field);
      }
      if (Object.hasOwn(entry,'source') && (typeof entry.source !== 'string' || [...entry.source].length > 2048)) add('Source: use attribution text up to 2048 characters in Advanced JSON.',pool,index);
      if ((catalog.audio_enabled === false || entry.audio_enabled === false) && typeof (entry.before ?? '') === 'string' && typeof (entry.after ?? '') === 'string' && !((entry.before || '') + (entry.after || '')).trim())
        add('Add response text, or enable audio for a clip-only card. No-audio cards cannot be empty.',pool,index,'before');
    });
  }
  const special = catalog.special;
  if (!object(special) || Object.keys(special).sort().join(',') !== 'enabled,lights_target,sound' || typeof special.enabled !== 'boolean' ||
      typeof special.sound !== 'string' || !Object.hasOwn(sounds,special.sound) || special.lights_target !== 'all lights')
    add('special needs enabled (true/false), an existing sound, and lights_target: all lights.');
  if (new TextEncoder().encode(JSON.stringify(catalog)).length > 65536) add('Keep the catalog under 64 KB.');
  return issues;
}
function validateDraft() {
  draftIssues = draftParseValid && responseCatalog ? catalogIssues(responseCatalog.catalog) : [];
  if (remoteIssue) draftIssues.push(remoteIssue);
  const saved = savedCardCounts();
  for (const view of cardViews.values()) {
    const issues = draftIssues.filter(issue => issue.pool === view.pool && issue.index === view.index);
    const entry = responseCatalog.catalog[view.pool][view.index];
    const key = cardSignature(responseCatalog.catalog,view.pool,entry), count = saved.get(key) || 0;
    const isSaved = count > 0;
    if (isSaved) saved.set(key,count - 1);
    view.card.classList.toggle('invalid',issues.length > 0);
    view.card.classList.toggle('unsaved',!issues.length && !isSaved);
    view.card.classList.toggle('saved',!issues.length && isSaved);
    view.state.textContent = issues.length ? 'Invalid' : isSaved ? 'Saved · ready' : 'Unsaved';
    view.state.className = 'card-state ' + (issues.length ? 'invalid' : isSaved ? 'saved' : 'unsaved');
    view.validation.textContent = issues.map(issue => issue.message).join('\n');
    view.text.setAttribute('aria-invalid',String(issues.some(issue => ['before','after',null].includes(issue.field))));
    view.sound.setAttribute('aria-invalid',String(issues.some(issue => issue.field === 'sound' || issue.field === null)));
  }
  const global = draftIssues.filter(issue => issue.pool === null);
  $('catalog-json').setAttribute('aria-invalid',String(!draftParseValid || global.length > 0));
  $('editor-result').classList.toggle('validation-error',draftIssues.length > 0 || !draftParseValid);
  return draftParseValid && draftIssues.length === 0;
}
function showDraftIssues(focus = false) {
  const cardCount = new Set(draftIssues.filter(issue => issue.pool !== null).map(issue => issue.pool + '-' + issue.index)).size;
  const global = draftIssues.filter(issue => issue.pool === null).map(issue => issue.message);
  set('draft-status','Needs fixing');
  set('editor-result',[cardCount ? 'Fix ' + cardCount + ' red card' + (cardCount === 1 ? '' : 's') + ' before testing or applying.' : '',...global].filter(Boolean).join(' '));
  if (focus) {
    const first = draftIssues.find(issue => issue.pool !== null), view = first && cardViews.get(first.pool + '-' + first.index);
    if (view) (first.field === 'sound' ? view.sound : view.text).focus();
    else { $('catalog-editor').open = true; $('catalog-json').focus(); }
  }
}
function showServerValidation(message) {
  const match = /^(good|bad)\[(\d+)\](?:\.(before|after|source))?/.exec(message);
  remoteIssue = {message,pool:match ? match[1] : null,index:match ? Number(match[2]) : null,field:match?.[3] === 'source' ? null : match?.[3] || null};
  validateDraft(); showDraftIssues();
}
function node(tag,className,text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function makeCard(entry) {
  const key = entry.pool + '-' + entry.index, label = poolNames[entry.pool] + ' card ' + (entry.index + 1);
  const card = node('article','reaction-card ' + entry.pool); card.setAttribute('aria-label',label);
  const top = node('div','reaction-card-top'), title = node('h4','',label);
  const state = node('span','card-state','Checking');
  const topLeft = node('div','card-identity'); topLeft.append(title,state);
  const remove = node('button','card-delete','Delete'); remove.type = 'button';
  remove.setAttribute('aria-label','Delete ' + label.toLowerCase());
  remove.addEventListener('click',() => requestDelete(entry.pool,entry.index)); top.append(topLeft,remove);
  const textId = 'text-' + key, soundId = 'sound-' + key, groupId = 'group-' + key;
  const content = node('div','card-content'), script = node('div','card-script'), controls = node('div','card-controls');
  const textLabel = node('label','','Response text'); textLabel.htmlFor = textId;
  const text = node('textarea','card-line'); text.id = textId; text.rows = 3;
  text.value = cardText(responseCatalog.catalog[entry.pool][entry.index]);
  text.placeholder = 'Damn, {weight}. {audio} Your punchline goes here.';
  text.addEventListener('input',() => editCardText(entry.pool,entry.index,text.value));
  const hint = node('p','card-text-hint','{weight} = pounds · {audio} = selected clip. No marker plays it at the end; choose No audio for speech only.');
  const groupLabel = node('label','','Group'); groupLabel.htmlFor = groupId;
  const group = node('select','card-group'); group.id = groupId;
  for (const pool of ['good','bad']) { const option = node('option','',poolNames[pool]); option.value = pool; group.append(option); }
  group.value = entry.pool; group.addEventListener('change',() => moveCard(entry.pool,entry.index,group.value));
  const clipBody = node('div','card-clip-body');
  const clipLabel = node('label','','Meme audio'); clipLabel.htmlFor = soundId;
  const sound = node('select'); sound.id = soundId;
  const silent = node('option','','No audio · speech only'); silent.value = ''; sound.append(silent);
  for (const name of Object.keys(responseCatalog.catalog.sounds)) {
    const option = node('option','',soundNames[name] || name); option.value = name; sound.append(option);
  }
  if (!(entry.sound in responseCatalog.catalog.sounds)) {
    const missing = node('option','','Missing clip: ' + entry.sound); missing.value = entry.sound; sound.append(missing);
  }
  sound.value = responseCatalog.catalog[entry.pool][entry.index]?.audio_enabled === false ? '' : entry.sound;
  sound.addEventListener('change',() => setCardAudio(entry.pool,entry.index,sound.value));
  clipBody.append(clipLabel,sound);
  const preview = node('p','card-weight-preview'), play = node('button','card-play','▷ Play on Echo'); play.type = 'button';
  play.addEventListener('click',() => playResponse(entry.pool,entry.index));
  const result = node('p','card-result'); result.setAttribute('role','status'); result.setAttribute('aria-live','polite');
  const validation = node('p','card-errors'); validation.id = 'errors-' + key; validation.setAttribute('role','status'); validation.setAttribute('aria-live','polite');
  for (const field of [text,group,sound]) field.setAttribute('aria-describedby',validation.id);
  script.append(textLabel,text,hint,preview,validation,result);
  controls.append(groupLabel,group,clipBody,play); content.append(script,controls); card.append(top,content);
  cardViews.set(key,{card,text,group,state,sound,remove,play,preview,result,validation,pool:entry.pool,index:entry.index});
  return card;
}
function renderCards() {
  cardViews.clear();
  for (const pool of ['good','bad']) {
    const container = $(pool + '-cards'); container.replaceChildren();
    const entries = draftParseValid ? responseCatalog?.entries.filter(entry => entry.pool === pool) || [] : [];
    set(pool + '-count',String(entries.length));
    for (const entry of entries) container.append(makeCard(entry));
    if (!entries.length) container.append(node('p','empty-cards',draftParseValid ? 'No cards here yet. Add one to start.' : 'Cards appear when valid JSON is loaded.'));
  }
  const cutoff = responseCatalog?.catalog?.roast_threshold_lb ?? 100;
  set('good-rule','Below ' + cutoff + ' lb · ' + (responseCatalog?.audio_enabled ? 'speech + meme audio' : 'speech only'));
  set('bad-rule',cutoff + ' lb and above · randomly chosen by the scale');
  refreshCardPreviews(); updateEditorControls();
}
function refreshCardPreviews() {
  const weight = sampleWeight();
  for (const view of cardViews.values()) {
    const entry = responseCatalog.catalog[view.pool][view.index];
    const spoken = [entry?.before,entry?.after].filter(value => typeof value === 'string' && value).join(' ').replaceAll('{weight}',weight === null ? '…' : String(weight));
    view.preview.textContent = weight === null ? 'Enter a test weight from 7 to 396 lb.' : 'At ' + weight + ' lb: ' + (spoken || 'Clip only.');
  }
  updateEditorControls();
}
function updateEditorControls() {
  const valid = validateDraft();
  const busy = responseBusy || catalogBusy || audioBusy, loaded = Boolean(liveRevision), editable = loaded && draftParseValid && !busy;
  $('catalog-json').disabled = !loaded || busy;
  $('json-format').disabled = !loaded || busy;
  $('json-apply').disabled = !loaded || busy || !draftDirty;
  $('json-discard').disabled = !loaded || busy || !draftDirty;
  $('responses-reload').disabled = busy || draftDirty;
  $('test-weight').disabled = busy;
  for (const pool of ['good','bad']) $( 'add-' + pool).disabled = !editable || responseCatalog.catalog[pool].length >= 100;
  for (const view of cardViews.values()) {
    for (const field of [view.text,view.group,view.sound,view.remove]) field.disabled = !editable;
    view.play.disabled = !editable || !valid || sampleWeight() === null;
  }
  updateAudioControls();
}
function markCatalogChanged(rerender = false) {
  responseCatalog = readCardCatalog(responseCatalog.catalog);
  draftDirty = !liveCatalog || stableJSON(responseCatalog.catalog) !== stableJSON(liveCatalog); draftParseValid = true; remoteIssue = null;
  $('catalog-json').value = JSON.stringify(responseCatalog.catalog,null,2);
  set('draft-status',draftDirty ? 'Unsaved draft' : 'Live catalog'); set('response-count',responseCatalog.entries.length + (draftDirty ? ' draft cards' : ' cards'));
  set('response-result',draftDirty ? 'Draft preview · edits are not applied to the scale yet' : 'Matches saved catalog · selected Echo only');
  set('editor-result',draftDirty ? 'Edits stay in your draft. Apply checks them automatically before saving to the Pi.' : 'These cards match the saved catalog.');
  for (const view of cardViews.values()) view.result.textContent = '';
  if (rerender) renderCards(); else refreshCardPreviews();
  if (draftIssues.length) showDraftIssues();
}
function editCard(pool,index,field,value) {
  if (responseBusy || catalogBusy || !draftParseValid) return;
  const entry = responseCatalog.catalog[pool][index];
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) responseCatalog.catalog[pool][index] = {sound:Object.keys(responseCatalog.catalog.sounds)[0],before:'',after:''};
  responseCatalog.catalog[pool][index][field] = value;
  if (field === 'before' || field === 'after') cardTextDrafts.delete(responseCatalog.catalog[pool][index]);
  markCatalogChanged();
}
function editCardText(pool,index,value) {
  if (responseBusy || catalogBusy || !draftParseValid) return;
  let entry = responseCatalog.catalog[pool][index];
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
    entry = {sound:Object.keys(responseCatalog.catalog.sounds)[0],before:'',after:''};
    responseCatalog.catalog[pool][index] = entry;
  }
  cardTextDrafts.set(entry,value);
  const pieces = value.split('{audio}');
  if (pieces.length <= 2) { entry.before = pieces[0].trim(); entry.after = pieces.length === 2 ? pieces[1].trim() : ''; }
  else { entry.before = value; entry.after = ''; }
  markCatalogChanged();
}
function setCardAudio(pool,index,value) {
  if (responseBusy || catalogBusy || !draftParseValid) return;
  let entry = responseCatalog.catalog[pool][index];
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
    entry = {sound:Object.keys(responseCatalog.catalog.sounds)[0],before:'',after:''};
    responseCatalog.catalog[pool][index] = entry;
  }
  if (value === '') entry.audio_enabled = false;
  else { entry.sound = value; delete entry.audio_enabled; }
  markCatalogChanged();
}
function moveCard(pool,index,target) {
  if (!['good','bad'].includes(target) || pool === target || responseBusy || catalogBusy || !draftParseValid) return;
  const catalog = responseCatalog.catalog;
  if (catalog[target].length >= 100) { set('editor-result','The target group already has 100 cards.'); renderCards(); return; }
  const [entry] = catalog[pool].splice(index,1);
  catalog[target].push(entry); markCatalogChanged(true);
  cardViews.get(target + '-' + (catalog[target].length - 1))?.group.focus();
}
function addCard(pool) {
  if (!['good','bad'].includes(pool) || responseBusy || catalogBusy || !draftParseValid) return;
  const catalog = responseCatalog.catalog, sound = Object.keys(catalog.sounds)[0];
  if (!sound || catalog[pool].length >= 100) return;
  catalog[pool].push({sound,before:"You're {weight} pounds.",after:''});
  markCatalogChanged(true);
  cardViews.get(pool + '-' + (catalog[pool].length - 1))?.text.focus();
}
function requestDelete(pool,index) {
  if (responseBusy || catalogBusy || !draftParseValid || !responseCatalog.catalog[pool]?.[index]) return;
  pendingDelete = {pool,index};
  const entry = responseCatalog.catalog[pool][index];
  set('delete-preview',[entry.before || '', '[' + (soundNames[entry.sound] || entry.sound) + ']',entry.after || ''].filter(Boolean).join(' '));
  $('delete-dialog').showModal();
}
function cancelDelete() {
  const target = pendingDelete; pendingDelete = null; $('delete-dialog').close();
  if (target) cardViews.get(target.pool + '-' + target.index)?.remove.focus();
}
function confirmDelete() {
  const target = pendingDelete; pendingDelete = null; $('delete-dialog').close();
  if (!target || responseBusy || catalogBusy || !draftParseValid) return;
  responseCatalog.catalog[target.pool].splice(target.index,1);
  markCatalogChanged(true);
  if (!draftIssues.length) set('editor-result','Card removed from your draft. Apply saves the deletion; Discard restores live cards.');
  const next = cardViews.get(target.pool + '-' + Math.min(target.index,responseCatalog.catalog[target.pool].length - 1));
  (next?.remove || $('add-' + target.pool)).focus();
}
async function loadResponses(discard = false) {
  if (catalogBusy || responseBusy) return;
  if (draftDirty && !discard) { set('editor-result','Discard or apply your draft before reloading.'); return; }
  catalogBusy = true; updateEditorControls(); set('response-result','Loading cards from the Pi…');
  try {
    const response = await fetch('/api/responses',{cache:'no-store',signal:AbortSignal.timeout(25000)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not load cards.');
    liveRevision = data.revision; responseCatalog = readCardCatalog(data.catalog);
    liveCatalog = JSON.parse(JSON.stringify(data.catalog));
    draftDirty = false; draftParseValid = true; remoteIssue = null;
    $('catalog-json').value = JSON.stringify(data.catalog,null,2);
    set('draft-status','Live catalog'); set('response-count',data.entries.length + ' cards');
    set('editor-result','Edit any card. Changes stay in your draft until Apply to Pi.');
    set('response-result','Ready to test · selected Echo only'); renderCards();
  } catch (error) {
    set('response-result',error.message + ' Reload when the Pi is connected.');
    if (!responseCatalog) { set('response-count','Pi unavailable'); renderCards(); }
  } finally { catalogBusy = false; updateEditorControls(); }
}
async function playResponse(pool,index) {
  const weight = sampleWeight(), view = cardViews.get(pool + '-' + index);
  if (!view || weight === null || responseBusy || catalogBusy || !draftParseValid) return;
  if (!validateDraft()) { showDraftIssues(true); return; }
  responseBusy = true; updateEditorControls(); view.result.textContent = 'Sending to your Echo…';
  try {
    const body = {pool,index,weight,revision:liveRevision};
    if (draftDirty) body.catalog = responseCatalog.catalog;
    const response = await fetch('/api/responses/' + (draftDirty ? 'play-draft' : 'play'),{method:'POST',
      headers:{'Content-Type':'application/json','X-Lab-Action':'response-test'},body:JSON.stringify(body),signal:AbortSignal.timeout(25000)});
    const data = await response.json();
    if (!response.ok) { if (response.status === 400) showServerValidation(data.error || 'Invalid draft.'); throw new Error(data.error || 'Playback could not be confirmed.'); }
    view.result.textContent = (draftDirty ? 'Draft test · ' : '') + data.message;
    set('response-result','Queued for the selected Echo. Listen to confirm playback.');
  } catch (error) { view.result.textContent = error.name === 'TimeoutError' ? 'Playback not confirmed. Check your Echo before retrying.' : error.message; }
  finally { responseBusy = false; updateEditorControls(); }
}
function editorChanged() {
  draftDirty = true; remoteIssue = null;
  set('draft-status','Unsaved draft');
  set('response-result','Draft preview · edits are not applied to the scale yet');
  try {
    responseCatalog = readCardCatalog(JSON.parse($('catalog-json').value)); draftParseValid = true;
    draftDirty = !liveCatalog || stableJSON(responseCatalog.catalog) !== stableJSON(liveCatalog);
    set('draft-status',draftDirty ? 'Unsaved draft' : 'Live catalog');
    set('response-count',responseCatalog.entries.length + ' draft cards');
    set('editor-result','JSON and cards are synced. Apply checks them automatically before saving.');
  } catch (error) {
    draftParseValid = false; set('draft-status','Invalid draft');
    set('editor-result','JSON needs fixing: ' + error.message);
  }
  renderCards();
  if (draftIssues.length) showDraftIssues();
}
async function editorOperation(operation) {
  if (operation !== 'apply' || !liveRevision || responseBusy || catalogBusy || !draftDirty) return;
  let catalog;
  try { catalog = JSON.parse($('catalog-json').value); }
  catch (error) { set('editor-result','JSON needs fixing: ' + error.message); $('catalog-editor').open = true; $('catalog-json').focus(); return; }
  if (!validateDraft()) { if (draftParseValid) showDraftIssues(true); else { $('catalog-editor').open = true; $('catalog-json').focus(); } return; }
  responseBusy = true; updateEditorControls();
  set('editor-result','Checking and saving your cards to the Pi…');
  try {
    const response = await fetch('/api/responses/' + operation,{method:'POST',
      headers:{'Content-Type':'application/json','X-Lab-Action':'response-test'},
      body:JSON.stringify({catalog,revision:liveRevision}),signal:AbortSignal.timeout(operation === 'apply' ? 55000 : 25000)});
    const data = await response.json();
    if (!response.ok) { if (response.status === 400) showServerValidation(data.error || 'Invalid draft.'); throw new Error(data.error || 'JSON operation failed.'); }
    if (operation === 'apply') { liveRevision = data.revision; draftDirty = false; liveCatalog = JSON.parse(JSON.stringify(data.catalog)); }
    responseCatalog = readCardCatalog(data.catalog); draftParseValid = true; remoteIssue = null;
    $('catalog-json').value = JSON.stringify(data.catalog,null,2);
    set('draft-status',draftDirty ? 'Validated draft' : 'Live catalog');
    set('response-count',data.entries.length + (draftDirty ? ' draft cards' : ' cards'));
    set('response-result',operation === 'apply' ? 'Saved cards are active for the next scale reading.' : 'Draft valid · ready to test or apply');
    set('editor-result',data.message); renderCards();
  } catch (error) {
    set('editor-result',error.name === 'TimeoutError' ? 'Operation not confirmed. Check live JSON before retrying Apply.' : error.message);
  } finally { responseBusy = false; updateEditorControls(); }
}
function responseTab() {
  const audio = window.location.hash === '#responses/audio';
  const active = window.location.hash === '#responses' || audio;
  const weights = window.location.hash === '#weights';
  $('overview').classList.toggle('meme-active',active || weights);
  $('dashboard-view').hidden = active || weights; $('responses').hidden = !active;
  $('weights').hidden = !weights;
  $('cards-pane').hidden = audio; $('audio-pane').hidden = !audio;
  for (const [id,selected] of [['cards-tab',!audio],['audio-tab',audio]]) {
    $(id).classList.toggle('selected',selected);
    if (selected) $(id).setAttribute('aria-current','page'); else $(id).removeAttribute('aria-current');
  }
  for (const link of document.querySelectorAll('.rail nav a')) {
    const selected = link.getAttribute('href') === (active ? '#responses' : window.location.hash || '#overview');
    link.classList.toggle('selected',selected);
    if (selected) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  }
  if (active && !responseCatalog) loadResponses();
  if (audio && !audioLoaded) loadAudio();
  if (weights && !weightData) {
    if (weightEventSnapshot) renderWeights(weightEventSnapshot);
    loadWeights();
  }
}

function weightTime(value) {
  const match = /^(\d{1,2}):(\d{2})(?::\d{2})?$/.exec(value || '');
  if (!match || Number(match[1]) > 23 || Number(match[2]) > 59) return value || '—';
  const hour = Number(match[1]);
  return (hour % 12 || 12) + ':' + match[2] + (hour < 12 ? ' AM' : ' PM');
}
function weightDay(value) {
  return new Date(value + 'T12:00:00Z').toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'});
}
function renderWeights(data) {
  weightData = data;
  const rows = data.rows || [], latest = rows[0];
  set('weight-latest',latest ? number(latest.lb,1) + ' lb' : '—');
  set('weight-latest-time',latest ? weightDay(latest.date) + (latest.time ? ' · ' + weightTime(latest.time) : '') : 'No readings loaded');
  set('weight-total',number(rows.length));
  set('weight-waiting',data.outbox_ok ? number(rows.filter(row => row.status === 'pending').length) : '—');
  set('weight-waiting-note',data.cached ? 'Last known upload state' : data.outbox_ok ? 'Final readings waiting to upload' : 'Upload queue unavailable');
  const sync = data.synced_at ? 'Last synced ' + date(data.synced_at) + '. ' : '';
  const live = data.stream_live && weightStreamConnected;
  set('weight-status',(live ? 'Live from the Pi · new weigh-ins appear automatically. ' : 'Live updates reconnecting · minute refresh is available. ') +
      (data.error ? sync + data.error : sync));
  $('weight-status').classList.toggle('validation-error',Boolean(data.error));
  set('weight-footnote','Times use ' + (data.timezone || 'America/Vancouver') + ', with no seconds. Historical entries without a recorded time stay blank. The scale records whoever steps on it.' +
      (data.skipped ? ' ' + data.skipped + ' incomplete or invalid sheet rows were skipped.' : '') +
      (data.truncated ? ' Sheet history is limited to its first 10,000 rows.' : ''));
  renderWeightRows();
}
function renderWeightRows() {
  const query = $('weight-search').value.trim().toLowerCase(), period = $('weight-period').value;
  const days = period === '7' || period === '30' ? Number(period) : null;
  const today = new Date().toLocaleDateString('en-CA',{timeZone:weightData?.timezone || 'America/Vancouver'});
  const cutoff = days ? new Date(new Date(today + 'T12:00:00Z').getTime() - (days - 1) * 86400000).toISOString().slice(0,10) : null;
  const rows = (weightData?.rows || []).filter(row => (!cutoff || row.date >= cutoff) &&
    (!query || [row.date,weightDay(row.date),weightTime(row.time),row.lb,row.source,row.status].join(' ').toLowerCase().includes(query)));
  weightPage = Math.max(0,Math.min(weightPage,Math.ceil(rows.length / 50) - 1));
  const start = weightPage * 50, page = rows.slice(start,start + 50);
  $('weight-rows').replaceChildren();
  for (const row of page) {
    const tr = document.createElement('tr');
    for (const value of [weightDay(row.date),weightTime(row.time),number(row.lb,1) + ' lb',row.source || '—']) {
      const td = document.createElement('td'); td.textContent = value; tr.append(td);
    }
    const td = document.createElement('td'), state = document.createElement('span');
    state.className = 'badge ' + (row.status === 'pending' ? 'warn' : 'ok');
    state.textContent = row.status === 'pending' ? 'Pending' : row.status === 'synced' ? 'In Sheets' : 'Uploaded';
    td.append(state); tr.append(td); $('weight-rows').append(tr);
  }
  $('weight-empty').hidden = rows.length > 0;
  set('weight-range',rows.length ? `${start + 1}–${start + page.length} of ${rows.length} readings · newest first` : '0 readings');
  $('weight-prev').disabled = weightPage === 0;
  $('weight-next').disabled = start + 50 >= rows.length;
}
async function loadWeights() {
  if (weightBusy) return;
  weightBusy = true; $('weight-refresh').disabled = true;
  if (!weightData) set('weight-status','Loading sheet history and the Pi’s recent readings…');
  try {
    const response = await fetch('/api/weights',{cache:'no-store',signal:AbortSignal.timeout(50000)});
    if (!response.ok) throw new Error();
    renderWeights(await response.json());
  } catch {
    set('weight-status','Dashboard connection lost. Displayed readings may be stale.');
    $('weight-status').classList.add('validation-error');
    set('weight-waiting-note','Last shown upload state');
  } finally { weightBusy = false; $('weight-refresh').disabled = false; }
}

function receiveWeightEvent(event) {
  let data;
  try { data = JSON.parse(event.data); if (!Array.isArray(data.rows)) return; }
  catch { return; }
  weightEventSnapshot = data;
  weightStreamConnected = data.stream_live === true;
  const latest = data.rows.find(row => row.id.startsWith('renpho:'));
  if (latest && data.stream_live) {
    latestLiveScale = {lb:latest.lb,utc:latest.utc,delivered:latest.status !== 'pending'};
    livePending = data.rows.filter(row => row.status === 'pending').length;
    set('scale-weight',number(latest.lb,1) + ' lb');
    set('scale-pending',number(livePending));
    if (liveBaselineReceived && lastLiveReading !== latest.id) {
      set('weight-new-text','New weigh-in · ' + number(latest.lb,1) + ' lb');
      $('weight-new-notice').hidden = false;
      if (weightNoticeTimer) clearTimeout(weightNoticeTimer);
      weightNoticeTimer = setTimeout(() => { $('weight-new-notice').hidden = true; },7000);
    }
    lastLiveReading = latest.id;
  }
  if (data.stream_live) liveBaselineReceived = true;
  if (weightData || window.location.hash === '#weights') renderWeights(data);
}
function connectWeightEvents() {
  if (typeof EventSource === 'undefined') return;
  const events = new EventSource('/api/weight-events');
  events.addEventListener('weights',receiveWeightEvent);
  events.onerror = () => {
    weightStreamConnected = false;
    if (weightData) renderWeights(weightData);
  };
}
function updateAudioControls() {
  const busy = audioBusy || responseBusy || catalogBusy;
  for (const id of ['audio-name','audio-url','audio-file','audio-process','audio-reload']) $(id).disabled = busy;
  $('audio-drop').setAttribute('aria-disabled',String(busy));
  $('audio-drop').classList.toggle('disabled',busy);
  for (const button of document.querySelectorAll('.audio-add')) button.disabled = busy || !liveRevision || !draftParseValid;
}
function audioMessage(message,error = false) {
  set('audio-result',message); $('audio-result').classList.toggle('validation-error',error);
}
function chooseAudioFile(file) {
  if (audioBusy || responseBusy || catalogBusy) return;
  if (!file || !/\.mp3$/i.test(file.name) || !file.size || file.size > 20 * 1024 * 1024) {
    audioMessage('Choose one MP3 no larger than 20 MB.',true); return;
  }
  selectedAudioFile = file; $('audio-url').value = '';
  set('audio-file-label',file.name);
  $('audio-drop').classList.add('has-file');
  if (!$('audio-name').value.trim()) {
    const base = file.name.replace(/\.mp3$/i,'').toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_+|_+$/g,'').slice(0,40) || 'custom_clip';
    let name = base, suffix = 1;
    while (Object.hasOwn(responseCatalog?.catalog?.sounds || {},name)) name = base.slice(0,34) + '_' + suffix++;
    $('audio-name').value = name;
  }
  audioMessage('File selected. Process MP3 converts and checks it locally.');
}
function renderAudio() {
  const container = $('audio-library'); container.replaceChildren(); set('audio-count',String(audioClips.length));
  if (!audioClips.length) container.append(node('p','empty-cards','Drop your first MP3 above. Processed clips stay here, including after a dashboard restart.'));
  for (const clip of [...audioClips].reverse()) {
    const card = node('article','custom-clip'), top = node('div','custom-clip-top');
    top.append(node('h4','',clip.name.replaceAll('_',' ')),node('span','badge ' + (clip.url ? 'ok' : ''),clip.url ? 'Public' : 'Local preview'));
    const meta = node('p','clip-meta',clip.duration.toFixed(2) + ' sec · ' + Math.ceil(clip.bytes / 1024) + ' KB · Echo format');
    const audio = node('audio'); audio.controls = true; audio.preload = clip.id === audioClips.at(-1)?.id ? 'metadata' : 'none'; audio.src = '/audio/' + clip.id + '.mp3';
    audio.setAttribute('aria-label','Preview ' + clip.name.replaceAll('_',' '));
    const add = node('button','audio-add',clip.url ? 'Add to cards' : 'Publish & add to cards'); add.type = 'button';
    add.addEventListener('click',() => publishAudio(clip));
    const disclosure = node('p','clip-meta',clip.url ? 'Already hosted publicly. Adding updates your draft only.' : 'Publishing makes this processed MP3 public in your github.com/example-owner/meme-audio repository.');
    card.append(top,meta,audio,disclosure,add); container.append(card);
  }
  updateAudioControls();
}
async function loadAudio() {
  if (audioBusy) return;
  audioBusy = true; updateEditorControls();
  try {
    const response = await fetch('/api/audio',{cache:'no-store',signal:AbortSignal.timeout(10000)}), data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Audio library unavailable.');
    audioClips = data.clips; audioLoaded = true; renderAudio();
  } catch (error) { audioMessage(error.message,true); }
  finally { audioBusy = false; updateEditorControls(); }
}
function rememberClip(clip) {
  audioClips = audioClips.filter(item => item.id !== clip.id); audioClips.push(clip); audioLoaded = true; renderAudio();
}
async function processAudio(event) {
  event?.preventDefault();
  if (audioBusy || responseBusy || catalogBusy) return;
  const name = $('audio-name').value.trim(), url = $('audio-url').value.trim();
  const nameValid = /^[a-z0-9_]{1,40}$/.test(name);
  $('audio-name').setAttribute('aria-invalid',String(!nameValid));
  if (!nameValid) { audioMessage('Clip name needs 1–40 lowercase letters, numbers or underscores.',true); $('audio-name').focus(); return; }
  if (!url && !selectedAudioFile) { audioMessage('Drop an MP3 or paste its direct HTTPS link first.',true); return; }
  if (url) {
    try { const parsed = new URL(url); if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.hash) throw new Error(); }
    catch { $('audio-url').setAttribute('aria-invalid','true'); audioMessage('Use a direct HTTPS MP3 link without a login or fragment.',true); $('audio-url').focus(); return; }
  }
  $('audio-url').setAttribute('aria-invalid','false');
  audioBusy = true; updateEditorControls(); audioMessage(url ? 'Downloading and converting your MP3 locally…' : 'Uploading and converting your MP3 locally…');
  try {
    const response = await fetch('/api/audio/' + (url ? 'link' : 'file'),{method:'POST',
      headers:{'Content-Type':url ? 'application/json' : 'audio/mpeg','X-Lab-Action':'response-test',...(url ? {} : {'X-Clip-Name':name})},
      body:url ? JSON.stringify({name,url}) : selectedAudioFile,signal:AbortSignal.timeout(130000)});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || 'MP3 processing failed.');
    rememberClip(data.clip); audioMessage('Processed. Preview it below, then publish and add it when you’re ready.');
    selectedAudioFile = null; $('audio-file').value = ''; $('audio-url').value = ''; set('audio-file-label','Drop your MP3 here'); $('audio-drop').classList.remove('has-file');
  } catch (error) { audioMessage(error.name === 'TimeoutError' ? 'Processing was not confirmed. Refresh clips before trying again.' : error.message,true); }
  finally { audioBusy = false; updateEditorControls(); }
}
function addAudioToDraft(clip) {
  if (!liveRevision || !draftParseValid) throw new Error('Load or fix your card draft before adding a clip.');
  const sounds = responseCatalog.catalog.sounds;
  if (Object.hasOwn(sounds,clip.name) && sounds[clip.name] !== clip.url) throw new Error('That clip name is already used by different audio. Choose a unique name before processing.');
  if (!Object.hasOwn(sounds,clip.name) && Object.keys(sounds).length >= 40) throw new Error('Your draft already has 40 clips. Remove an unused clip in Advanced JSON first.');
  sounds[clip.name] = clip.url; markCatalogChanged(true);
}
async function publishAudio(clip) {
  if (audioBusy || responseBusy || catalogBusy) return;
  if (!liveRevision || !draftParseValid) { audioMessage('Load or fix your card draft before adding audio.',true); return; }
  const sounds = responseCatalog.catalog.sounds;
  if (Object.hasOwn(sounds,clip.name) && sounds[clip.name] !== clip.url) { audioMessage('That clip name already exists. Use a unique clip name before processing.',true); return; }
  if (!Object.hasOwn(sounds,clip.name) && Object.keys(sounds).length >= 40) { audioMessage('Your draft already has 40 clips. Remove an unused clip in Advanced JSON first.',true); return; }
  audioBusy = true; updateEditorControls(); audioMessage(clip.url ? 'Adding to your draft…' : 'Publishing this processed MP3 to your public meme-audio repository…');
  try {
    if (!clip.url) {
      const response = await fetch('/api/audio/publish',{method:'POST',headers:{'Content-Type':'application/json','X-Lab-Action':'response-test'},
        body:JSON.stringify({id:clip.id,publish_publicly:true}),signal:AbortSignal.timeout(130000)});
      const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Publishing was not confirmed.');
      clip = data.clip; rememberClip(clip);
    }
    addAudioToDraft(clip); audioMessage('“' + clip.name.replaceAll('_',' ') + '” added to your draft. Choose it on a response card, then Apply to Pi.');
  } catch (error) { audioMessage(error.name === 'TimeoutError' ? 'Publishing was not confirmed. Refresh clips before retrying.' : error.message,true); }
  finally { audioBusy = false; updateEditorControls(); }
}
$('audio-form').addEventListener('submit',processAudio);
$('audio-file').addEventListener('change',event => chooseAudioFile(event.target.files[0]));
$('audio-drop').addEventListener('click',() => { if (!audioBusy && !responseBusy && !catalogBusy) $('audio-file').click(); });
$('audio-drop').addEventListener('keydown',event => { if (['Enter',' '].includes(event.key)) { event.preventDefault(); if (!audioBusy && !responseBusy && !catalogBusy) $('audio-file').click(); } });
$('audio-drop').addEventListener('dragover',event => { event.preventDefault(); if (!audioBusy) $('audio-drop').classList.add('drag-over'); });
$('audio-drop').addEventListener('dragleave',() => $('audio-drop').classList.remove('drag-over'));
$('audio-drop').addEventListener('drop',event => { event.preventDefault(); $('audio-drop').classList.remove('drag-over'); if (event.dataTransfer.files.length !== 1) audioMessage('Drop one MP3 at a time.',true); else chooseAudioFile(event.dataTransfer.files[0]); });
$('audio-url').addEventListener('input',() => { $('audio-url').setAttribute('aria-invalid','false'); if ($('audio-url').value.trim()) { selectedAudioFile = null; $('audio-file').value = ''; set('audio-file-label','Drop your MP3 here'); $('audio-drop').classList.remove('has-file'); } });
$('audio-name').addEventListener('input',() => $('audio-name').setAttribute('aria-invalid','false'));
$('audio-reload').addEventListener('click',loadAudio);
$('test-weight').addEventListener('input',refreshCardPreviews);
$('add-good').addEventListener('click',() => addCard('good'));
$('add-bad').addEventListener('click',() => addCard('bad'));
$('delete-cancel').addEventListener('click',cancelDelete);
$('delete-confirm').addEventListener('click',confirmDelete);
$('delete-dialog').addEventListener('cancel',event => { event.preventDefault(); cancelDelete(); });
$('responses-reload').addEventListener('click',() => loadResponses());
$('catalog-json').addEventListener('input',editorChanged);
$('json-format').addEventListener('click',() => {
  try { $('catalog-json').value = JSON.stringify(JSON.parse($('catalog-json').value),null,2); editorChanged(); }
  catch (error) { set('editor-result','JSON needs fixing: ' + error.message); }
});
$('json-apply').addEventListener('click',() => editorOperation('apply'));
$('json-discard').addEventListener('click',() => loadResponses(true));
$('weight-refresh').addEventListener('click',loadWeights);
for (const [id,event] of [['weight-search','input'],['weight-period','change']]) $(id).addEventListener(event,() => { weightPage = 0; renderWeightRows(); });
$('weight-prev').addEventListener('click',() => { weightPage--; renderWeightRows(); });
$('weight-next').addEventListener('click',() => { weightPage++; renderWeightRows(); });
setInterval(() => { if (window.location.hash === '#weights') loadWeights(); },60000);
window.addEventListener('hashchange',responseTab); responseTab();
connectWeightEvents();
