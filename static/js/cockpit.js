/* ═══════════════════════════════════════════════════════════════════════════
   TutoBuilder Vision — Cockpit JS v3
   Couverture exhaustive de tous les cas utilisateur
   ═══════════════════════════════════════════════════════════════════════════ */

/* ═══════════════════════════════════════════════════════════
   SECTION 1 — CONFIG & ÉTAT GLOBAL
═══════════════════════════════════════════════════════════ */

const VOICES = [
  { id: "narrateur_pro",  name: "Narrateur Pro",  desc: "Professionnel, clair" },
  { id: "narratrice_pro", name: "Narratrice Pro", desc: "Douce, narrative" },
  { id: "expert",         name: "Expert",         desc: "Grave, autorité" },
  { id: "experte",        name: "Experte",        desc: "Confiante, expressive" },
  { id: "guide",          name: "Guide",          desc: "Chaleureux, accessible" },
  { id: "pedagogique",    name: "Pédagogique",    desc: "Claire, rassurante" },
];

// Filler words les plus courants en français
const FILLER_WORDS_FR = ["euh", "hm", "hmm", "bon", "enfin", "voilà", "donc", "alors", "ben", "bah"];
const FILLER_WORDS_EN = ["uh", "um", "hmm", "like", "you know", "so", "well", "basically", "actually", "right"];

const STATE = {
  jobId:         document.getElementById('current-job-id').value    || null,
  jobStatus:     document.getElementById('current-job-status').value || '',
  segments:      [],          // liste des segments {id, index, start_ms, end_ms, text, _deleted, _merged}
  waveform:      [],
  ws:            null,
  uploadedFile:  null,
  pollInterval:  null,
  isFinalVideo:  false,
  selectedVoice: document.getElementById('current-job-voice').value || 'narrateur_pro',
  scriptDirty:   false,
  ttsOkCount:    0,
  ttsTotal:      0,
  ttsEchecs:     [],
  ttsReady:      false,
  currentStep:   1,
  // Historique undo pour le script
  undoStack:     [],          // [{segments: [...], label: string}]
  redoStack:     [],
  MAX_UNDO:      50,
  // Recherche/remplacement
  searchActive:  false,
  searchMatches: [],
  searchIndex:   0,
  // Stats
  wordCount:     0,
  charCount:     0,
  estimatedDuration: 0,       // durée estimée du TTS en secondes
  // Contexte menu segment
  contextSegIdx: null,
  // Sélection multiple pour fusion
  selectedSegs:  new Set(),
};

const CSRF = document.getElementById('csrf-token').value;

// Paramétrage initial depuis URL
const _urlProject = new URLSearchParams(location.search).get('project');
if (_urlProject) document.getElementById('select-project').value = _urlProject;


/* ═══════════════════════════════════════════════════════════
   SECTION 2 — TOAST & NOTIFICATIONS
═══════════════════════════════════════════════════════════ */

function showError(title, msg) {
  if (typeof toast !== 'undefined') {
    toast.err(title, msg, 8000);
  } else {
    alert(`${title}\n\n${msg}`);
  }
}

function showSuccess(msg) {
  if (typeof toast !== 'undefined') {
    toast.ok(msg, '', 3000);
  }
}

function showWarn(id) {
  document.querySelectorAll('.step-warning').forEach(w => w.classList.remove('show'));
  const el = document.getElementById(id);
  if (el) {
    el.classList.add('show');
    setTimeout(() => el && el.classList.remove('show'), 5500);
  }
}

function hideAllWarns() {
  document.querySelectorAll('.step-warning').forEach(w => w.classList.remove('show'));
}


/* ═══════════════════════════════════════════════════════════
   SECTION 3 — VOIX
═══════════════════════════════════════════════════════════ */

function buildVoiceGrids() {
  ['el', 'ca'].forEach(suffix => {
    const grid = document.getElementById(`voice-grid-${suffix}`);
    if (!grid) return;
    grid.innerHTML = '';
    VOICES.forEach(v => {
      const card = document.createElement('div');
      card.className = 'voice-card' + (v.id === STATE.selectedVoice ? ' sel' : '');
      card.dataset.voice = v.id;
      card.innerHTML = `
        <div class="voice-card-name">${v.name}</div>
        <div class="voice-card-desc">${v.desc}</div>`;
      card.addEventListener('click', () => selectVoice(v.id));
      grid.appendChild(card);
    });
  });
}

function selectVoice(id) {
  STATE.selectedVoice = id;
  document.getElementById('select-voice').value = id;
  document.querySelectorAll('.voice-card').forEach(c =>
    c.classList.toggle('sel', c.dataset.voice === id));
}

function onTtsEngineChange() {
  const engine = document.getElementById('select-tts').value;
  document.getElementById('voices-elevenlabs').style.display = engine === 'elevenlabs' ? '' : 'none';
  document.getElementById('voices-cartesia').style.display   = engine === 'cartesia'   ? '' : 'none';
}


/* ═══════════════════════════════════════════════════════════
   SECTION 4 — SOUS-TITRES
═══════════════════════════════════════════════════════════ */

function toggleSubEditor() {
  const enabled = document.getElementById('subtitles-sw').classList.contains('on');
  document.getElementById('sub-editor-wrap').classList.toggle('disabled', !enabled);
}

const SUB_PRESETS = [
  { name:"Moderne",   size:48, color:"#ffffff", font:"calibri", ow:1,  oc:"#000000", bg:true,  bgc:"#000000", bga:75,  br:10, sh:true,  sha:60, pos:"bottom", mg:60 },
  { name:"Classique", size:48, color:"#ffffff", font:"arial",   ow:3,  oc:"#000000", bg:false, bgc:"#000000", bga:0,   br:0,  sh:false, sha:0,  pos:"bottom", mg:60 },
  { name:"Netflix",   size:52, color:"#ffff00", font:"arial",   ow:4,  oc:"#000000", bg:false, bgc:"#000000", bga:0,   br:0,  sh:false, sha:0,  pos:"bottom", mg:60 },
  { name:"Cinéma",    size:44, color:"#f5f5dc", font:"georgia", ow:2,  oc:"#2c1810", bg:false, bgc:"#000000", bga:0,   br:0,  sh:true,  sha:70, pos:"center", mg:60 },
  { name:"Social",    size:44, color:"#ffffff", font:"calibri", ow:0,  oc:"#000000", bg:true,  bgc:"#000000", bga:90,  br:20, sh:false, sha:0,  pos:"bottom", mg:60 },
  { name:"Neon",      size:44, color:"#00ffc8", font:"calibri", ow:2,  oc:"#00a080", bg:true,  bgc:"#000000", bga:80,  br:8,  sh:true,  sha:50, pos:"bottom", mg:60 },
];
let selPreset = 0;

function hexToRgb(hex, a) {
  const h = hex.replace('#','');
  return `rgba(${parseInt(h.slice(0,2),16)},${parseInt(h.slice(2,4),16)},${parseInt(h.slice(4,6),16)},${a})`;
}
function getSwState(id) {
  return document.getElementById(id).classList.contains('on');
}
function toggleSw(id) {
  document.getElementById(id).classList.toggle('on');
}
function syncVal(rangeId, valId, unit) {
  document.getElementById(valId).textContent = document.getElementById(rangeId).value + unit;
}

function getSubStyle() {
  return {
    enabled:        getSwState('subtitles-sw'),
    style_preset:   SUB_PRESETS[selPreset].name.toLowerCase().replace(/é/g,'e'),
    font_size:      parseInt(document.getElementById('s-size').value),
    font_family:    document.getElementById('s-font').value,
    text_color:     document.getElementById('s-color').value.replace('#','').toUpperCase(),
    outline_width:  parseInt(document.getElementById('s-ow').value),
    outline_color:  document.getElementById('s-oc').value.replace('#','').toUpperCase(),
    bg_enabled:     getSwState('s-bg-sw'),
    bg_color:       document.getElementById('s-bgc').value.replace('#','').toUpperCase(),
    bg_opacity:     parseInt(document.getElementById('s-bga').value),
    bg_radius:      parseInt(document.getElementById('s-br').value),
    shadow:         getSwState('s-sh-sw'),
    shadow_opacity: parseInt(document.getElementById('s-sha').value),
    position:       document.getElementById('s-pos').value,
    margin:         parseInt(document.getElementById('s-mg').value),
  };
}

function drawSubPreview() {
  const canvas = document.getElementById('sub-canvas');
  if (!canvas) return;
  const wrap = canvas.parentElement;
  const W = wrap.offsetWidth || 220;
  const H = Math.round(W * 9 / 16);
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext('2d');

  // Fond foncé simulant une vidéo
  ctx.fillStyle = '#0a0a12'; ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = 'rgba(255,255,255,.04)';
  ctx.fillRect(10, 12, W * 0.5, 5); ctx.fillRect(10, 22, W * 0.3, 4);
  ctx.fillStyle = 'rgba(26,107,240,.06)';
  ctx.fillRect(10, 35, W * 0.7, H * 0.3);

  const text  = document.getElementById('sub-preview-text').value || 'Aperçu sous-titre';
  const sz    = document.getElementById('s-size').value;
  const fsize = Math.max(10, Math.round(sz * W / 640));
  const font  = document.getElementById('s-font').value;
  const color = document.getElementById('s-color').value;
  const ow    = parseInt(document.getElementById('s-ow').value);
  const oc    = document.getElementById('s-oc').value;
  const bgOn  = getSwState('s-bg-sw');
  const bgc   = document.getElementById('s-bgc').value;
  const bga   = parseInt(document.getElementById('s-bga').value) / 100;
  const br    = Math.round(parseInt(document.getElementById('s-br').value) * W / 640);
  const shOn  = getSwState('s-sh-sw');
  const sha   = parseInt(document.getElementById('s-sha').value) / 100;
  const pos   = document.getElementById('s-pos').value;
  const mg    = Math.round(parseInt(document.getElementById('s-mg').value) * W / 640);

  ctx.font = `bold ${fsize}px ${font}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  const maxW = W * 0.88;
  const words = text.split(' ');
  const lines = []; let cur = '';
  for (const w of words) {
    const t = cur ? cur + ' ' + w : w;
    if (ctx.measureText(t).width <= maxW) { cur = t; }
    else { if (cur) lines.push(cur); cur = w; if (lines.length >= 3) break; }
  }
  if (cur && lines.length < 3) lines.push(cur);

  const lh = fsize * 1.35;
  const padX = Math.round(14 * W / 640);
  const padY = Math.round(8 * W / 640);
  let maxLw = 0;
  lines.forEach(l => { maxLw = Math.max(maxLw, ctx.measureText(l).width); });
  const bw = maxLw + padX * 2, bh = lh * lines.length + padY * 2;
  const cy = pos === 'top' ? mg + bh / 2 : pos === 'center' ? H / 2 : H - mg - bh / 2;
  const cx = W / 2, bx = cx - bw / 2, by = cy - bh / 2;

  if (bgOn && bga > 0) {
    ctx.fillStyle = hexToRgb(bgc, bga);
    const r = Math.min(br, bh / 2);
    ctx.beginPath();
    ctx.moveTo(bx+r,by); ctx.lineTo(bx+bw-r,by);
    ctx.quadraticCurveTo(bx+bw,by,bx+bw,by+r);
    ctx.lineTo(bx+bw,by+bh-r);
    ctx.quadraticCurveTo(bx+bw,by+bh,bx+bw-r,by+bh);
    ctx.lineTo(bx+r,by+bh);
    ctx.quadraticCurveTo(bx,by+bh,bx,by+bh-r);
    ctx.lineTo(bx,by+r);
    ctx.quadraticCurveTo(bx,by,bx+r,by);
    ctx.closePath(); ctx.fill();
  }

  lines.forEach((line, i) => {
    const ly = by + padY + i * lh + lh / 2;
    if (shOn && sha > 0) {
      const so = Math.round(2 * W / 640);
      ctx.fillStyle = hexToRgb('#000000', sha);
      ctx.fillText(line, cx + so, ly + so);
    }
    if (ow > 0) {
      ctx.strokeStyle = oc; ctx.lineWidth = ow * 1.5; ctx.lineJoin = 'round';
      ctx.strokeText(line, cx, ly);
    }
    ctx.fillStyle = color; ctx.fillText(line, cx, ly);
  });
}

function applyPreset(idx) {
  selPreset = idx;
  document.querySelectorAll('.sub-preset').forEach((p, i) => p.classList.toggle('sel', i === idx));
  const p = SUB_PRESETS[idx];
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
  const setTxt = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  set('s-size', p.size);   setTxt('s-size-v', p.size+'px');
  set('s-color', p.color);
  set('s-font',  p.font);
  set('s-ow', p.ow);       setTxt('s-ow-v', p.ow+'px');
  set('s-oc', p.oc);
  set('s-bgc', p.bgc);
  set('s-bga', p.bga);     setTxt('s-bga-v', p.bga+'%');
  set('s-br',  p.br);      setTxt('s-br-v', p.br+'px');
  set('s-sha', p.sha);     setTxt('s-sha-v', p.sha+'%');
  set('s-pos', p.pos);
  set('s-mg',  p.mg);      setTxt('s-mg-v', p.mg+'px');
  document.getElementById('s-bg-sw').classList.toggle('on', p.bg);
  document.getElementById('s-sh-sw').classList.toggle('on', p.sh);
  drawSubPreview();
}

function initPresets() {
  const grid = document.getElementById('sub-presets-grid');
  if (!grid) return;
  SUB_PRESETS.forEach((p, i) => {
    const mc = document.createElement('canvas');
    mc.width = 80; mc.height = 36;
    const ctx = mc.getContext('2d');
    ctx.fillStyle = '#111'; ctx.fillRect(0, 0, 80, 36);
    ctx.font = `bold 11px ${p.font}`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const txt = 'Aa';
    if (p.bg && p.bga > 0) {
      ctx.fillStyle = hexToRgb(p.bgc, p.bga/100);
      const tw = ctx.measureText(txt).width+10, r = Math.min(p.br, 8);
      const bx = 40-tw/2, by = 8;
      ctx.beginPath();
      ctx.moveTo(bx+r,by); ctx.lineTo(bx+tw-r,by); ctx.quadraticCurveTo(bx+tw,by,bx+tw,by+r);
      ctx.lineTo(bx+tw,18); ctx.quadraticCurveTo(bx+tw,by+20,bx+tw-r,by+20);
      ctx.lineTo(bx+r,by+20); ctx.quadraticCurveTo(bx,by+20,bx,by+r);
      ctx.lineTo(bx,by+r); ctx.quadraticCurveTo(bx,by,bx+r,by);
      ctx.closePath(); ctx.fill();
    }
    if (p.ow > 0) { ctx.strokeStyle = p.oc; ctx.lineWidth = p.ow; ctx.strokeText(txt, 40, 18); }
    ctx.fillStyle = p.color; ctx.fillText(txt, 40, 18);
    const el = document.createElement('div');
    el.className = 'sub-preset' + (i === 0 ? ' sel' : '');
    el.addEventListener('click', () => applyPreset(i));
    el.innerHTML = `
      <div class="sub-preset-prev"><img src="${mc.toDataURL()}" width="80" height="36"/></div>
      <div class="sub-preset-name">${p.name}</div>`;
    grid.appendChild(el);
  });
}

function switchSubTab(name, el) {
  document.querySelectorAll('.sub-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.sub-tab-panel').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('subtab-' + name).classList.add('active');
  if (name === 'preview') setTimeout(drawSubPreview, 50);
}


/* ═══════════════════════════════════════════════════════════
   SECTION 5 — WORKFLOW ÉTAPES
═══════════════════════════════════════════════════════════ */

function goToStep(n) {
  STATE.currentStep = n;
  document.querySelectorAll('.wsec').forEach((s, i) => {
    const num = i + 1;
    s.classList.remove('active', 'done', 'locked');
    const numEl = s.querySelector('.wsec-num');
    if (num < n)      { s.classList.add('done');   if (numEl) numEl.textContent = '✓'; }
    else if (num === n){ s.classList.add('active'); if (numEl) numEl.textContent = num; }
    else               { s.classList.add('locked'); if (numEl) numEl.textContent = num; }
  });
  for (let i = 1; i <= 4; i++) {
    const dot  = document.getElementById(`sd-${i}`);
    const wrap = document.getElementById(`sdw-${i}`);
    if (!dot || !wrap) continue;
    dot.className = 'step-dot';
    wrap.className = 'step-node';
    if (i < n)       { dot.classList.add('done');   wrap.classList.add('done');   dot.textContent = '✓'; }
    else if (i === n) { dot.classList.add('active'); wrap.classList.add('active'); dot.textContent = i; }
    else              { dot.textContent = i; }
    const line = document.getElementById(`sc-${i}-${i+1}`);
    if (line) line.classList.toggle('done', i < n);
  }
  hideAllWarns();
}


/* ═══════════════════════════════════════════════════════════
   SECTION 6 — LOGS & PROGRESSION
═══════════════════════════════════════════════════════════ */

function log(msg, type = '') {
  const el = document.getElementById('log-monitor');
  if (!el) return;
  const ts  = new Date().toLocaleTimeString('fr-FR');
  const cls = type === 'err' ? 'll-err' : type === 'warn' ? 'll-warn' : type === 'ok' ? 'll-ok' : 'll-info';
  el.innerHTML += `<span class="${cls}">[${ts}] ${msg}\n</span>`;
  el.scrollTop = el.scrollHeight;
}

function clearLog() {
  const el = document.getElementById('log-monitor');
  if (el) el.innerHTML = '';
}

function setProgress(label, pct, state = 'active') {
  const labelEl = document.getElementById('progress-label');
  const pctEl   = document.getElementById('progress-pct');
  const barEl   = document.getElementById('progress-bar');
  const dotEl   = document.getElementById('progress-dot');
  if (labelEl) labelEl.textContent = label;
  if (pctEl)   pctEl.textContent   = pct > 0 ? pct + '%' : '';
  if (barEl)   barEl.style.width   = pct + '%';
  if (dotEl) {
    dotEl.className = 'dot';
    if (state === 'active')    dotEl.classList.add('pulse');
    else if (state === 'done') dotEl.classList.add('done');
    else if (state === 'error')dotEl.classList.add('error');
  }
}

function msToTC(ms) {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2,'0')}:${String(s % 60).padStart(2,'0')}`;
}

async function apiPost(url, data) {
  const r = await fetch(url, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
    body:    JSON.stringify(data),
  });
  return r.json();
}


/* ═══════════════════════════════════════════════════════════
   SECTION 7 — UNDO / REDO
═══════════════════════════════════════════════════════════ */

function pushUndo(label = 'Modification') {
  // Deep copy des segments avant la modification
  const snapshot = JSON.parse(JSON.stringify(STATE.segments));
  STATE.undoStack.push({ segments: snapshot, label });
  if (STATE.undoStack.length > STATE.MAX_UNDO) STATE.undoStack.shift();
  STATE.redoStack = []; // Effacer redo dès qu'on modifie
  updateUndoRedoUI();
}

function undo() {
  if (!STATE.undoStack.length) return;
  const snap = STATE.undoStack.pop();
  // Sauvegarder l'état actuel dans redo
  STATE.redoStack.push({ segments: JSON.parse(JSON.stringify(STATE.segments)), label: snap.label });
  STATE.segments = snap.segments;
  renderScript(STATE.segments);
  updateScriptStats();
  updateUndoRedoUI();
  log(`↩ Annulé : ${snap.label}`, 'info');
  _markDirty();
}

function redo() {
  if (!STATE.redoStack.length) return;
  const snap = STATE.redoStack.pop();
  STATE.undoStack.push({ segments: JSON.parse(JSON.stringify(STATE.segments)), label: snap.label });
  STATE.segments = snap.segments;
  renderScript(STATE.segments);
  updateScriptStats();
  updateUndoRedoUI();
  log(`↪ Rétabli : ${snap.label}`, 'info');
  _markDirty();
}

function updateUndoRedoUI() {
  const btnUndo = document.getElementById('btn-undo');
  const btnRedo = document.getElementById('btn-redo');
  if (btnUndo) {
    btnUndo.disabled = STATE.undoStack.length === 0;
    btnUndo.title = STATE.undoStack.length
      ? `Annuler : ${STATE.undoStack[STATE.undoStack.length-1].label}`
      : 'Rien à annuler';
  }
  if (btnRedo) {
    btnRedo.disabled = STATE.redoStack.length === 0;
    btnRedo.title = STATE.redoStack.length
      ? `Rétablir : ${STATE.redoStack[STATE.redoStack.length-1].label}`
      : 'Rien à rétablir';
  }
}


/* ═══════════════════════════════════════════════════════════
   SECTION 8 — STATS SCRIPT
═══════════════════════════════════════════════════════════ */

/* ══════════════════════════════════════════════════════════
   SECTION 8 — STATS SCRIPT & VALIDATION
═══════════════════════════════════════════════════════════ */

// Debit TTS moyen : 130 mots/min en francais, 150 en anglais
const TTS_WPM = 130;
// Debordement max tolere avant blocage sauvegarde (ms) — meme valeur que backend
const FREEZE_MAX_MS = 5000;

/**
 * Calcule le debordement TTS pour un segment.
 * Retourne { ttsMs, paroleMs, overflowMs, ratio, status }
 * status : 'ok' | 'freeze_ok' | 'freeze_warn' | 'blocked'
 */
function segmentOverflow(seg) {
  const words    = (seg.text || '').trim().split(/\s+/).filter(Boolean).length;
  const ttsMs    = (words / TTS_WPM) * 60 * 1000;
  const paroleMs = (seg.end_ms || 0) - (seg.start_ms || 0);
  const overflow = Math.max(0, ttsMs - paroleMs);
  const ratio    = ttsMs / Math.max(paroleMs, 100);
  let status = 'ok';
  if (overflow > FREEZE_MAX_MS)     status = 'blocked';
  else if (overflow > FREEZE_MAX_MS * 0.8) status = 'freeze_warn';
  else if (overflow > 0)            status = 'freeze_ok';
  return { ttsMs, paroleMs, overflowMs: overflow, ratio, status, words };
}

function updateScriptStats() {
  const visible = STATE.segments.filter(s => !s._deleted && !s._silence);
  const allText = visible.map(s => s.text || '').join(' ');
  const words   = allText.trim() ? allText.trim().split(/\s+/).length : 0;
  const estSecs = Math.round(words / TTS_WPM * 60);

  STATE.wordCount         = words;
  STATE.estimatedDuration = estSecs;

  const statsEl = document.getElementById('script-stats');
  if (statsEl) {
    const m = Math.floor(estSecs / 60);
    const s = estSecs % 60;
    const durStr = m > 0 ? `${m}m${s > 0 ? s+'s' : ''}` : `${s}s`;
    statsEl.textContent = `${visible.length} segments · ${words} mots · ~${durStr}`;
  }

  // Mettre a jour chaque indicateur de segment
  STATE.segments.forEach((seg, i) => {
    const row = document.querySelector(`.seg-row[data-index="${i}"]`);
    if (!row || seg._silence) return;

    const ov  = segmentOverflow(seg);
    const ind = row.querySelector('.seg-length-indicator');
    const lbl = row.querySelector('.seg-budget-label');

    if (ind) {
      ind.className = 'seg-length-indicator';
      if      (ov.status === 'blocked')    ind.classList.add('blocked');
      else if (ov.status === 'freeze_warn')ind.classList.add('freeze-warn');
      else if (ov.status === 'freeze_ok')  ind.classList.add('freeze-ok');
      else if (ov.ratio > 0.8)             ind.classList.add('ok');
      else                                 ind.classList.add('short');

      const maxWords = Math.floor((ov.paroleMs / 1000) * TTS_WPM / 60);
      ind.title = ov.status === 'blocked'
        ? `Texte trop long ! Debordement ${(ov.overflowMs/1000).toFixed(1)}s > max 5s — sauvegarder bloque`
        : ov.status === 'freeze_ok' || ov.status === 'freeze_warn'
        ? `Image gelee ${(ov.overflowMs/1000).toFixed(1)}s apres la parole — OK`
        : `Budget OK · max ~${maxWords} mots`;
    }

    if (lbl) {
      const maxW = Math.floor(ov.paroleMs / 1000 * TTS_WPM / 60);
      if (ov.status === 'blocked') {
        const aEnlever = ov.words - maxW;
        lbl.textContent = `Trop long — retirez ${aEnlever} mot(s), vous en avez ${ov.words}, max ${maxW}`;
        lbl.style.color = 'var(--red)';
      } else if (ov.status === 'freeze_warn') {
        const aEnlever = ov.words - Math.floor(maxW * 0.8);
        lbl.textContent = `Presque ! Retirez ~${aEnlever} mot(s) pour un résultat impeccable`;
        lbl.style.color = 'var(--amber)';
      } else if (ov.status === 'freeze_ok') {
        lbl.textContent = `Super ! L'image sera gelée ${(ov.overflowMs/1000).toFixed(1)}s pour laisser votre voix finir`;
        lbl.style.color = 'var(--blue)';
      } else {
        const motsRestants = maxW - ov.words;
        lbl.textContent = `Parfait ! Vous pouvez encore ajouter ~${motsRestants} mot(s)`;
        lbl.style.color = 'var(--green)';
      }
    }
  });
}

/* Indicateurs de segment - couleurs enrichies */
const STATS_STYLE = document.createElement('style');
STATS_STYLE.textContent = `
.seg-length-indicator {
  width: 6px; height: 6px; border-radius: 50%;
  flex-shrink: 0; margin-top: 12px; margin-right: 4px;
  transition: background .2s, box-shadow .2s; cursor: help;
}
.seg-length-indicator.ok         { background: var(--green); }
.seg-length-indicator.short      { background: var(--text-3); }
.seg-length-indicator.freeze-ok  { background: var(--blue); box-shadow: 0 0 0 2px rgba(26,107,240,.2); }
.seg-length-indicator.freeze-warn{ background: var(--amber); animation: pulse 1.4s infinite; }
.seg-length-indicator.blocked    { background: var(--red); animation: pulse .8s infinite; }

/* Label budget sous le texte */
.seg-budget-label {
  font-size: 9.5px; color: var(--text-3); font-family: var(--font-mono);
  margin-top: 2px; display: block; transition: color .2s;
}

/* Segment silence — zone opportunite */
.seg-row.silence-zone {
  background: repeating-linear-gradient(-45deg,
    transparent, transparent 6px,
    rgba(26,107,240,.03) 6px, rgba(26,107,240,.03) 12px);
  border: 1px solid var(--border);
  border-style: dashed;
  cursor: default;
  opacity: .85;
}
.seg-row.silence-zone:hover { border-color: var(--blue-border); opacity: 1; }
.silence-info {
  flex: 1; padding: 8px 6px; display: flex; flex-direction: column; gap: 4px;
}
.silence-title {
  font-size: 10.5px; font-weight: 600; color: var(--text-3);
  display: flex; align-items: center; gap: 6px;
}
.silence-title svg { width: 11px; height: 11px; flex-shrink: 0; }
.silence-hint { font-size: 10px; color: var(--text-3); line-height: 1.4; }
.silence-add-btn {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 8px; border-radius: 4px; margin-top: 2px;
  background: var(--blue-bg); border: 1px solid var(--blue-border);
  color: var(--blue); font-size: 10px; font-weight: 600;
  cursor: pointer; width: fit-content; transition: all .12s;
}
.silence-add-btn:hover { background: rgba(26,107,240,.15); }
.silence-add-btn svg { width: 10px; height: 10px; }

/* Alerte debordement inline */
.seg-overflow-alert {
  display: none; margin: 4px 0 2px;
  padding: 6px 9px; border-radius: 5px;
  font-size: 10.5px; line-height: 1.45;
  border: 1px solid;
}
.seg-overflow-alert.show { display: block; }
.seg-overflow-alert.freeze {
  background: rgba(26,107,240,.07); border-color: var(--blue-border); color: var(--blue);
}
.seg-overflow-alert.blocked {
  background: var(--red-bg); border-color: var(--red-border); color: var(--red);
}

.seg-row .seg-actions {
  display: none; align-items: center; gap: 2px;
  padding: 6px 6px 6px 0; flex-shrink: 0;
}
.seg-row:hover .seg-actions { display: flex; }
.seg-row.active .seg-actions { display: flex; }
.seg-action-btn {
  width: 20px; height: 20px; border-radius: 4px;
  border: none; background: none; cursor: pointer;
  color: var(--text-3); display: flex; align-items: center;
  justify-content: center; transition: all .12s; font-size: 10px;
}
.seg-action-btn:hover { background: var(--surface-3); color: var(--text-1); }
.seg-action-btn.danger:hover { background: var(--red-bg); color: var(--red); }
.seg-action-btn svg { width: 11px; height: 11px; }
.seg-row._deleted {
  opacity: .35; pointer-events: none;
  background: repeating-linear-gradient(-45deg, transparent, transparent 4px, rgba(196,40,29,.04) 4px, rgba(196,40,29,.04) 8px);
}
.search-bar {
  display: none; padding: 7px 10px; gap: 6px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-2); align-items: center;
}
.search-bar.open { display: flex; }
.search-bar input {
  flex: 1; padding: 5px 8px; border-radius: var(--r);
  border: 1px solid var(--border-md); background: var(--surface);
  color: var(--text-1); font-size: 12px; outline: none;
}
.search-bar input:focus { border-color: var(--blue); box-shadow: 0 0 0 2px rgba(26,107,240,.1); }
.search-bar .replace-input { display: none; }
.search-bar.show-replace .replace-input { display: flex; }
.search-match-highlight { background: rgba(255,200,0,.4); border-radius: 2px; outline: 1px solid rgba(255,180,0,.6); }
.search-match-current { background: rgba(255,120,0,.5); outline: 1px solid rgba(255,100,0,.8); }
.search-count { font-size: 10.5px; color: var(--text-3); white-space: nowrap; }
.script-toolbar-ext {
  display: flex; gap: 4px; flex-wrap: wrap;
  padding: 6px 10px; border-bottom: 1px solid var(--border);
  background: var(--surface-2);
}
.script-topbar { flex-wrap: wrap; gap: 4px; }
.context-menu {
  position: fixed; z-index: 600;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r-lg); box-shadow: var(--sh-md);
  min-width: 200px; overflow: hidden; display: none;
}
.context-menu.open { display: block; }
.ctx-item {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 14px; cursor: pointer; font-size: 12px;
  color: var(--text-2); transition: background .1s;
}
.ctx-item:hover { background: var(--surface-2); color: var(--text-1); }
.ctx-item.danger { color: var(--red); }
.ctx-item.danger:hover { background: var(--red-bg); }
.ctx-sep { height: 1px; background: var(--border); margin: 3px 0; }
.ctx-kbd {
  margin-left: auto; font-family: var(--font-mono);
  font-size: 9.5px; color: var(--text-3); background: var(--surface-2);
  padding: 1px 5px; border-radius: 3px; border: 1px solid var(--border);
}
.seg-badge {
  display: inline-flex; align-items: center;
  padding: 1px 5px; border-radius: 4px; font-size: 9px;
  font-weight: 600; margin-right: 3px; white-space: nowrap;
}
.seg-badge.added   { background: var(--green-bg); color: var(--green); border: 1px solid var(--green-border); }
.seg-badge.merged  { background: rgba(99,76,200,.12); color: #6340C8; border: 1px solid rgba(99,76,200,.2); }
.seg-row.selected-for-merge {
  background: rgba(99,76,200,.07);
  border-color: rgba(99,76,200,.3);
  box-shadow: inset 2px 0 0 #6340C8;
}
.merge-toolbar {
  display: none; position: sticky; bottom: 0;
  padding: 8px 12px; background: var(--surface);
  border-top: 1px solid var(--border);
  align-items: center; gap: 8px;
  box-shadow: 0 -4px 12px rgba(0,0,0,.08);
}
.merge-toolbar.open { display: flex; }
#script-stats {
  font-size: 10px; color: var(--text-3);
  padding: 0 4px; white-space: nowrap;
}
`;
document.head.appendChild(STATS_STYLE);


/* ═══════════════════════════════════════════════════════════
   SECTION 9 — RENDU DU SCRIPT (ÉDITEUR PRINCIPAL)
═══════════════════════════════════════════════════════════ */

/**
 * Insère des entrées _silence entre les vrais segments quand le gap > 1.5s.
 * Ces entrées sont affichées comme zones éditables dans l'UI.
 * Elles ne sont PAS envoyées au backend sauf si l'user y a écrit du texte.
 */
function _buildEnrichedSegments(segs) {
  const SILENCE_MIN_MS = 1500; // gap minimum pour afficher une zone silence
  const result = [];
  const real = segs.filter(s => !s._silence); // on repart des vrais segments

  real.forEach((seg, i) => {
    result.push(seg);
    if (i + 1 < real.length) {
      const next   = real[i + 1];
      const gap    = (next.start_ms || 0) - (seg.end_ms || 0);
      if (gap >= SILENCE_MIN_MS) {
        // Zone silence entre ce segment et le suivant
        result.push({
          id:       `silence_${seg.end_ms}_${next.start_ms}`,
          index:    -1,
          start_ms: seg.end_ms,
          end_ms:   next.start_ms,
          text:     '',
          _silence: true,
          _deleted: false,
          _added:   false,
        });
      }
    }
  });
  return result;
}

function renderScript(segs, focusIdx = -1) {
  const ed = document.getElementById('script-editor');
  if (!ed) return;

  // Construire une liste enrichie avec les silences inter-segments
  // On insère des entrées _silence entre les vrais segments
  const enriched = _buildEnrichedSegments(segs);

  document.getElementById('seg-count').textContent =
    segs.length ? `— ${segs.filter(s=>!s._deleted && !s._silence).length} segments` : '';

  if (!segs.length) {
    ed.innerHTML = `
      <div class="script-empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        <p>Le script apparaît ici après la transcription.</p>
        <p class="hint">Modifiez le texte librement.<br>Les timecodes sont conservés automatiquement.</p>
      </div>`;
    return;
  }

  const frag = document.createDocumentFragment();
  enriched.forEach((seg, i) => {
    const row = buildSegmentRow(seg, i);
    frag.appendChild(row);
  });

  // Toolbar de fusion (sticky)
  const mergeBar = document.createElement('div');
  mergeBar.className = 'merge-toolbar';
  mergeBar.id = 'merge-toolbar';
  mergeBar.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="color:var(--text-3)"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>
    <span style="font-size:11.5px;color:var(--text-2);flex:1" id="merge-count"></span>
    <button class="script-action-btn primary" onclick="mergeSelectedSegments()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>
      Fusionner
    </button>
    <button class="script-action-btn" onclick="clearMergeSelection()">Annuler</button>`;
  frag.appendChild(mergeBar);

  // Mettre à jour STATE.segments avec la liste enrichie
  // (les silences sont injectés, les indices reflètent la liste affichée)
  STATE.segments = enriched;

  ed.innerHTML = '';
  ed.appendChild(frag);

  if (focusIdx >= 0) {
    const focusRow = ed.querySelector(`.seg-row[data-index="${focusIdx}"]`);
    if (focusRow) {
      const txt = focusRow.querySelector('.seg-txt');
      if (txt) { txt.focus(); placeCaretAtEnd(txt); }
    }
  }

  // Réappliquer la sélection multiple
  STATE.selectedSegs.forEach(idx => {
    const row = ed.querySelector(`.seg-row[data-index="${idx}"]`);
    if (row) row.classList.add('selected-for-merge');
  });
  updateMergeToolbar();
  updateScriptStats();
}

function buildSegmentRow(seg, i) {
  const row = document.createElement('div');
  row.dataset.index = i;

  // ── CAS SPÉCIAL : segment silence ────────────────────────────────────────
  if (seg._silence) {
    const durS     = ((seg.end_ms - seg.start_ms) / 1000).toFixed(1);
    const silSpeed = seg.end_ms - seg.start_ms > 15000 ? 'x8' :
                     seg.end_ms - seg.start_ms > 5000  ? 'x4' :
                     seg.end_ms - seg.start_ms > 2000  ? 'x2' : 'x1';
    row.className = 'seg-row silence-zone';
    row.innerHTML = `
      <span class="seg-num" style="color:var(--text-3)">${i + 1}</span>
      <span class="seg-tc">${msToTC(seg.start_ms||0)}</span>
      <div class="silence-info">
        <div class="silence-title">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>
          Silence de ${durS}s — accéléré automatiquement (${silSpeed})
        </div>
        <div class="silence-hint">
          Vous pouvez ajouter un commentaire ou une explication sur cette zone si vous le souhaitez.
          La vidéo sera quand même accélérée, mais votre texte sera lu en voix off.
        </div>
        <button class="silence-add-btn" onclick="convertSilenceToSpeech(${i})">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          Ajouter un texte ici (budget : ${durS}s)
        </button>
      </div>`;
    // Clic sur le timecode → aller à ce moment
    const tcEl = row.querySelector('.seg-tc');
    if (tcEl) tcEl.addEventListener('click', () => {
      const vid = document.getElementById('video-player');
      if (vid) vid.currentTime = (seg.start_ms || 0) / 1000;
    });
    return row;
  }

  // ── SEGMENT PAROLE NORMAL ─────────────────────────────────────────────────
  row.className = 'seg-row' + (seg._deleted ? ' _deleted' : '');
  if (seg._added)  row.classList.add('_added');
  if (seg._merged) row.classList.add('_merged');

  const ov      = segmentOverflow(seg);
  const maxWords = Math.floor((ov.paroleMs / 1000) * TTS_WPM / 60);
  const durS     = (ov.paroleMs / 1000).toFixed(1);

  // Message budget persistant
  let budgetText  = '';
  let budgetColor = 'var(--green)';
  if (ov.status === 'blocked') {
    const aEnlever = ov.words - maxWords;
    budgetText  = `Trop long — retirez ${aEnlever} mot(s), vous en avez ${ov.words}, max ${maxWords}`;
    budgetColor = 'var(--red)';
  } else if (ov.status === 'freeze_warn') {
    const aEnlever = ov.words - Math.floor(maxWords * 0.8);
    budgetText  = `Presque ! Retirez ~${aEnlever} mot(s) pour un résultat impeccable`;
    budgetColor = 'var(--amber)';
  } else if (ov.status === 'freeze_ok') {
    budgetText  = `Super ! L'image sera gelée ${(ov.overflowMs/1000).toFixed(1)}s pour laisser votre voix finir`;
    budgetColor = 'var(--blue)';
  } else {
    const motsRestants = maxWords - ov.words;
    budgetText  = `Parfait ! Vous pouvez encore ajouter ~${motsRestants} mot(s)`;
    budgetColor = 'var(--green)';
  }

  let badges = '';
  if (seg._added)  badges += `<span class="seg-badge added">Nouveau</span>`;
  if (seg._merged) badges += `<span class="seg-badge merged">Fusionné</span>`;

  // Alerte debordement
  let alertHtml = '';
  if (ov.status === 'blocked') {
    alertHtml = `<div class="seg-overflow-alert blocked show">
      Ce texte déborde de <strong>${(ov.overflowMs/1000).toFixed(1)}s</strong> — la limite est 5s.
      Raccourcissez le texte (max ${maxWords} mots) ou scindez ce segment (Ctrl+Entrée).
    </div>`;
  } else if (ov.status === 'freeze_ok' || ov.status === 'freeze_warn') {
    alertHtml = `<div class="seg-overflow-alert freeze show">
      La vidéo sera gelée <strong>${(ov.overflowMs/1000).toFixed(1)}s</strong> à la fin
      pour laisser votre voix finir naturellement.
      ${ov.status === 'freeze_warn' ? ' Approche de la limite !' : ''}
    </div>`;
  }

  row.innerHTML = `
    <div class="seg-length-indicator ${ov.status === 'blocked' ? 'blocked' : ov.status === 'freeze_warn' ? 'freeze-warn' : ov.status === 'freeze_ok' ? 'freeze-ok' : 'ok'}"
         title="${ov.status === 'blocked' ? 'Trop long — sauvegarde bloquée' : 'Budget OK'}"></div>
    <span class="seg-num">${i + 1}</span>
    <span class="seg-tc" title="Aller à ${msToTC(seg.start_ms||0)}">${msToTC(seg.start_ms||0)}</span>
    <div class="seg-content" style="flex:1;min-width:0;padding:2px 0">
      ${badges ? `<div style="margin-bottom:2px">${badges}</div>` : ''}
      <div class="seg-txt" contenteditable="${seg._deleted ? 'false' : 'true'}"
        data-index="${i}" spellcheck="true">${escapeHtml(seg.text || '')}</div>
      ${alertHtml}
      <span class="seg-budget-label" style="color:${budgetColor}">${budgetText}</span>
    </div>
    <div class="seg-actions">
      <button class="seg-action-btn" title="Ajouter un segment après (Alt+Enter)" onclick="addSegmentAfter(${i})">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
      </button>
      <button class="seg-action-btn" title="Scinder ici (Ctrl+Enter)" onclick="splitSegmentAt(${i})">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5"/></svg>
      </button>
      <button class="seg-action-btn danger" title="Supprimer (Delete)" onclick="deleteSegment(${i})">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
      </button>
    </div>`;

  const tcEl  = row.querySelector('.seg-tc');
  const txtEl = row.querySelector('.seg-txt');

  if (tcEl) tcEl.addEventListener('click', e => {
    e.stopPropagation();
    const vid = document.getElementById('video-player');
    if (vid) vid.currentTime = (seg.start_ms || 0) / 1000;
    hlSeg(i);
  });

  row.addEventListener('click', () => hlSeg(i));
  row.addEventListener('click', e => { if (e.shiftKey) { e.preventDefault(); toggleMergeSelect(i); } });

  if (txtEl) {
    txtEl.addEventListener('input', () => onSegInput(txtEl, i));
    txtEl.addEventListener('keydown', e => onSegKeydown(e, i));
    txtEl.addEventListener('focus', () => hlSeg(i));
    txtEl.addEventListener('contextmenu', e => showContextMenu(e, i));
  }

  return row;
}

function escapeHtml(str) {
  return (str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function placeCaretAtEnd(el) {
  const range = document.createRange();
  const sel   = window.getSelection();
  range.selectNodeContents(el);
  range.collapse(false);
  sel.removeAllRanges();
  sel.addRange(range);
}

function hlSeg(idx) {
  document.querySelectorAll('.seg-row').forEach(r => r.classList.remove('active'));
  const row = document.querySelector(`.seg-row[data-index="${idx}"]`);
  if (row) {
    row.classList.add('active');
    row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

function onSegInput(el, idx) {
  const seg = STATE.segments[idx];
  if (!seg) return;
  seg.text = el.textContent.trim();
  _markDirty();

  // Avertissement script modifié si on est à l'étape 3+
  if (STATE.currentStep >= 3) {
    const w = document.getElementById('warn-step3');
    if (w) w.classList.add('show');
  }

  // Mise à jour en temps réel de l'alerte de debordement
  const row = el.closest('.seg-row');
  if (row) {
    const ov       = segmentOverflow(seg);
    const alertEl  = row.querySelector('.seg-overflow-alert');
    const indEl    = row.querySelector('.seg-length-indicator');
    const budgetEl = row.querySelector('.seg-budget-label');
    const maxWords = Math.floor((ov.paroleMs / 1000) * TTS_WPM / 60);
    const durS     = (ov.paroleMs / 1000).toFixed(1);

    if (alertEl) {
      alertEl.className = 'seg-overflow-alert';
      if (ov.status === 'blocked') {
        alertEl.className = 'seg-overflow-alert blocked show';
        alertEl.innerHTML = `Texte trop long ! Débordement <strong>${(ov.overflowMs/1000).toFixed(1)}s</strong> — max 5s.
          Raccourcissez (max ~${maxWords} mots) ou scindez ce segment en deux.`;
      } else if (ov.status === 'freeze_ok' || ov.status === 'freeze_warn') {
        alertEl.className = 'seg-overflow-alert freeze show';
        alertEl.innerHTML = `La vidéo sera gelée <strong>${(ov.overflowMs/1000).toFixed(1)}s</strong>
          après la parole pour laisser votre voix finir.
          ${ov.status === 'freeze_warn' ? ' Approche de la limite !' : ''}`;
      }
    }
    if (indEl) {
      indEl.className = `seg-length-indicator ${
        ov.status === 'blocked' ? 'blocked' :
        ov.status === 'freeze_warn' ? 'freeze-warn' :
        ov.status === 'freeze_ok' ? 'freeze-ok' : 'ok'}`;
    }
    if (budgetEl) {
      const maxW = Math.floor((ov.paroleMs / 1000) * TTS_WPM / 60);
      if (ov.status === 'blocked') {
        const aEnlever = ov.words - maxW;
        budgetEl.textContent = `Trop long — retirez ${aEnlever} mot(s), vous en avez ${ov.words}, max ${maxW}`;
        budgetEl.style.color = 'var(--red)';
      } else if (ov.status === 'freeze_warn') {
        const aEnlever = ov.words - Math.floor(maxW * 0.8);
        budgetEl.textContent = `Presque ! Retirez ~${aEnlever} mot(s) pour un résultat impeccable`;
        budgetEl.style.color = 'var(--amber)';
      } else if (ov.status === 'freeze_ok') {
        budgetEl.textContent = `Super ! L'image sera gelée ${(ov.overflowMs/1000).toFixed(1)}s pour laisser votre voix finir`;
        budgetEl.style.color = 'var(--blue)';
      } else {
        const motsRestants = maxW - ov.words;
        budgetEl.textContent = `Parfait ! Vous pouvez encore ajouter ~${motsRestants} mot(s)`;
        budgetEl.style.color = 'var(--green)';
      }
    }
  }

  // Stats globales (debounced)
  clearTimeout(el._statsTimer);
  el._statsTimer = setTimeout(() => updateScriptStats(), 400);
}

/**
 * Convertit un segment silence en segment parole editable.
 * L'user clique sur "Ajouter un texte ici" dans la zone silence.
 */
function convertSilenceToSpeech(idx) {
  const seg = STATE.segments[idx];
  if (!seg || !seg._silence) return;
  const durS = ((seg.end_ms - seg.start_ms) / 1000).toFixed(1);

  pushUndo(`Convertir silence ${idx + 1} en parole`);
  seg._silence = false;
  seg.text     = '';

  renderScript(STATE.segments, idx);
  _markDirty();

  // Focus sur le champ texte du segment converti
  setTimeout(() => {
    const row = document.querySelector(`.seg-row[data-index="${idx}"]`);
    const txt = row && row.querySelector('.seg-txt');
    if (txt) txt.focus();
  }, 50);

  log(`Zone silence de ${durS}s convertie en segment — budget disponible : ${durS}s`, 'ok');
}


/* ═══════════════════════════════════════════════════════════
   SECTION 10 — RACCOURCIS CLAVIER DANS LE SCRIPT
═══════════════════════════════════════════════════════════ */

function onSegKeydown(e, idx) {
  // Alt+Enter → Ajouter un segment après
  if (e.altKey && e.key === 'Enter') {
    e.preventDefault();
    addSegmentAfter(idx);
    return;
  }
  // Ctrl+Enter → Scinder le segment à la position du curseur
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault();
    splitSegmentAtCursor(idx);
    return;
  }
  // Delete sur champ vide → Supprimer le segment
  if (e.key === 'Delete' && !e.ctrlKey && !e.metaKey) {
    const txt = STATE.segments[idx]?.text || '';
    if (!txt.trim()) {
      e.preventDefault();
      deleteSegment(idx);
      return;
    }
  }
  // Backspace en début de segment → Fusionner avec le précédent
  if (e.key === 'Backspace') {
    const sel = window.getSelection();
    if (sel.anchorOffset === 0 && sel.toString() === '') {
      const prevIdx = findPrevVisible(idx);
      if (prevIdx >= 0) {
        e.preventDefault();
        mergeWithPrev(idx);
        return;
      }
    }
  }
  // Tab → passer au segment suivant
  if (e.key === 'Tab' && !e.shiftKey) {
    e.preventDefault();
    focusSegment(idx + 1);
    return;
  }
  // Shift+Tab → segment précédent
  if (e.key === 'Tab' && e.shiftKey) {
    e.preventDefault();
    focusSegment(idx - 1);
    return;
  }
}

// Raccourcis globaux
document.addEventListener('keydown', e => {
  // Ctrl+Z → Undo
  if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
    if (!isTypingInScript(e)) { e.preventDefault(); undo(); }
    return;
  }
  // Ctrl+Shift+Z ou Ctrl+Y → Redo
  if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) {
    if (!isTypingInScript(e)) { e.preventDefault(); redo(); }
    return;
  }
  // Ctrl+S → Sauvegarder
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault(); saveScript(); return;
  }
  // Ctrl+F → Rechercher
  if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
    e.preventDefault(); toggleSearch(); return;
  }
  // Escape → Fermer modale / recherche
  if (e.key === 'Escape') {
    closeContextMenu();
    const modal = document.getElementById('full-editor-modal');
    if (modal && modal.classList.contains('open')) { closeFullEditor(); return; }
    if (STATE.searchActive) { closeSearch(); return; }
  }
  // Espace → Play/Pause si on n'est pas dans un champ texte
  if (e.key === ' ' && !isTypingAnywhere(e)) {
    e.preventDefault(); togglePlay(); return;
  }
  // F2 → Focus sur le premier segment éditable
  if (e.key === 'F2') {
    e.preventDefault();
    focusSegment(0); return;
  }
});

function isTypingInScript(e) {
  const t = e.target;
  return t && t.classList.contains('seg-txt');
}

function isTypingAnywhere(e) {
  const t = e.target;
  return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||
               t.isContentEditable || t.classList.contains('seg-txt'));
}

function focusSegment(idx) {
  if (idx < 0 || idx >= STATE.segments.length) return;
  const row = document.querySelector(`.seg-row[data-index="${idx}"]`);
  if (!row) return;
  const txt = row.querySelector('.seg-txt');
  if (txt) { txt.focus(); hlSeg(idx); }
}

function findPrevVisible(idx) {
  for (let i = idx - 1; i >= 0; i--) {
    if (!STATE.segments[i]?._deleted) return i;
  }
  return -1;
}

function findNextVisible(idx) {
  for (let i = idx + 1; i < STATE.segments.length; i++) {
    if (!STATE.segments[i]?._deleted) return i;
  }
  return -1;
}


/* ═══════════════════════════════════════════════════════════
   SECTION 11 — OPÉRATIONS SUR LES SEGMENTS
═══════════════════════════════════════════════════════════ */

/* ── Supprimer un segment ─────────────────────────────────── */
async function deleteSegment(idx) {
  const seg = STATE.segments[idx];
  if (!seg) return;

  // Si le segment a du TTS généré, avertir
  if (STATE.ttsReady && !seg._added) {
    const ok = await tbConfirm(
      'Supprimer ce segment',
      `Le segment "${(seg.text||'').slice(0,60)}…" sera marqué comme supprimé.\n\nIl sera silencieux dans la vidéo finale. La séquence vidéo correspondante sera traitée comme un silence.`,
      { confirm: 'Supprimer', type: 'warn' }
    );
    if (!ok) return;
  }

  pushUndo(`Supprimer segment ${idx + 1}`);
  seg._deleted = true;
  seg.text = '';
  renderScript(STATE.segments);
  _markDirty();
  log(`Segment ${idx + 1} supprimé (traité comme silence à l'export).`, 'warn');
  updateScriptStats();
}

/* ── Ajouter un segment après ─────────────────────────────── */
function addSegmentAfter(idx) {
  const current = STATE.segments[idx];
  if (!current) return;

  // Calculer les timecodes du nouveau segment
  // On prend la moitié du silence suivant s'il y en a,
  // sinon on crée un segment de 2s à la fin du segment courant.
  let newStart = current.end_ms;
  let newEnd   = current.end_ms + 2000;

  const nextVisible = STATE.segments.slice(idx + 1).find(s => !s._deleted);
  if (nextVisible && nextVisible.start_ms > current.end_ms + 200) {
    // Il y a un silence — on le coupe en deux
    const mid = Math.round((current.end_ms + nextVisible.start_ms) / 2);
    newStart   = current.end_ms;
    newEnd     = mid;
  }

  pushUndo(`Ajouter segment après ${idx + 1}`);

  const newSeg = {
    id:       `new_${Date.now()}`,
    index:    -1,           // sera attribué par le backend à la sauvegarde
    start_ms: newStart,
    end_ms:   newEnd,
    text:     '',
    _added:   true,
    _deleted: false,
  };

  STATE.segments.splice(idx + 1, 0, newSeg);
  // Recalculer les index d'affichage
  renderScript(STATE.segments, idx + 1);
  _markDirty();
  log(`Nouveau segment ajouté après le segment ${idx + 1} [${msToTC(newStart)}→${msToTC(newEnd)}]`, 'ok');
}

/* ── Scinder un segment à la position du curseur ─────────── */
function splitSegmentAtCursor(idx) {
  const seg = STATE.segments[idx];
  if (!seg) return;

  const el  = document.querySelector(`.seg-row[data-index="${idx}"] .seg-txt`);
  if (!el) { splitSegmentAt(idx); return; }

  const sel = window.getSelection();
  if (!sel.rangeCount) { splitSegmentAt(idx); return; }

  const range    = sel.getRangeAt(0);
  const preRange = document.createRange();
  preRange.setStart(el, 0);
  preRange.setEnd(range.startContainer, range.startOffset);
  const textBefore = preRange.toString().trim();
  const textAfter  = seg.text.slice(textBefore.length).trim();

  if (!textBefore || !textAfter) {
    log('Scinder impossible : le curseur doit être au milieu du texte.', 'warn');
    return;
  }

  pushUndo(`Scinder segment ${idx + 1}`);

  // Calculer la répartition temporelle proportionnellement à la longueur du texte
  const totalLen    = (textBefore + textAfter).length || 1;
  const splitPoint  = seg.start_ms + Math.round((seg.end_ms - seg.start_ms) * textBefore.length / totalLen);

  const seg1 = { ...seg, end_ms: splitPoint, text: textBefore };
  const seg2 = {
    id:       `split_${Date.now()}`,
    index:    -1,
    start_ms: splitPoint,
    end_ms:   seg.end_ms,
    text:     textAfter,
    _added:   true,
    _deleted: false,
  };

  STATE.segments.splice(idx, 1, seg1, seg2);
  renderScript(STATE.segments, idx + 1);
  _markDirty();
  log(`Segment ${idx + 1} scindé en deux : "${textBefore.slice(0,30)}…" / "${textAfter.slice(0,30)}…"`, 'ok');
}

/* ── Scinder un segment en deux moitiés égales ─────────────── */
function splitSegmentAt(idx) {
  const seg = STATE.segments[idx];
  if (!seg || !seg.text.trim()) {
    log('Scinder : ce segment est vide.', 'warn');
    return;
  }

  const words     = seg.text.trim().split(/\s+/);
  if (words.length < 2) {
    log('Scinder : le segment ne contient qu\'un seul mot.', 'warn');
    return;
  }

  pushUndo(`Scinder segment ${idx + 1}`);

  const half       = Math.floor(words.length / 2);
  const text1      = words.slice(0, half).join(' ');
  const text2      = words.slice(half).join(' ');
  const splitPoint = seg.start_ms + Math.round((seg.end_ms - seg.start_ms) * half / words.length);

  const seg1 = { ...seg, end_ms: splitPoint, text: text1 };
  const seg2 = {
    id:       `split_${Date.now()}`,
    index:    -1,
    start_ms: splitPoint,
    end_ms:   seg.end_ms,
    text:     text2,
    _added:   true,
    _deleted: false,
  };

  STATE.segments.splice(idx, 1, seg1, seg2);
  renderScript(STATE.segments, idx + 1);
  _markDirty();
  log(`Segment ${idx + 1} scindé : "${text1.slice(0,30)}…" / "${text2.slice(0,30)}…"`, 'ok');
}

/* ── Fusionner avec le segment précédent ────────────────────── */
function mergeWithPrev(idx) {
  const prevIdx = findPrevVisible(idx);
  if (prevIdx < 0) return;
  const prev = STATE.segments[prevIdx];
  const curr = STATE.segments[idx];
  if (!prev || !curr) return;

  pushUndo(`Fusionner segments ${prevIdx + 1} et ${idx + 1}`);

  const merged = {
    ...prev,
    end_ms:  curr.end_ms,
    text:    [prev.text, curr.text].filter(Boolean).join(' '),
    _merged: true,
  };

  // Supprimer les deux et insérer le fusionné à la place du premier
  STATE.segments[prevIdx] = merged;
  STATE.segments[idx]._deleted = true;
  STATE.segments[idx].text     = '';

  renderScript(STATE.segments, prevIdx);
  _markDirty();
  log(`Segments ${prevIdx + 1} et ${idx + 1} fusionnés.`, 'ok');
}

/* ── Sélection multiple pour fusion ────────────────────────── */
function toggleMergeSelect(idx) {
  const seg = STATE.segments[idx];
  if (!seg || seg._deleted) return;
  if (STATE.selectedSegs.has(idx)) {
    STATE.selectedSegs.delete(idx);
  } else {
    STATE.selectedSegs.add(idx);
  }
  const row = document.querySelector(`.seg-row[data-index="${idx}"]`);
  if (row) row.classList.toggle('selected-for-merge', STATE.selectedSegs.has(idx));
  updateMergeToolbar();
}

function clearMergeSelection() {
  STATE.selectedSegs.clear();
  document.querySelectorAll('.seg-row.selected-for-merge')
    .forEach(r => r.classList.remove('selected-for-merge'));
  updateMergeToolbar();
}

function updateMergeToolbar() {
  const bar   = document.getElementById('merge-toolbar');
  const count = document.getElementById('merge-count');
  if (!bar) return;
  if (STATE.selectedSegs.size >= 2) {
    bar.classList.add('open');
    if (count) count.textContent = `${STATE.selectedSegs.size} segments sélectionnés (Maj+Clic)`;
  } else {
    bar.classList.remove('open');
  }
}

function mergeSelectedSegments() {
  if (STATE.selectedSegs.size < 2) return;
  const indices = Array.from(STATE.selectedSegs).sort((a, b) => a - b);
  // Vérifier que les segments sont consécutifs (pas obligatoire, mais avertir si non)
  const segs    = indices.map(i => STATE.segments[i]).filter(s => s && !s._deleted);
  if (segs.length < 2) { clearMergeSelection(); return; }

  pushUndo(`Fusionner ${segs.length} segments`);

  const merged = {
    ...segs[0],
    end_ms:  segs[segs.length - 1].end_ms,
    text:    segs.map(s => s.text).filter(Boolean).join(' '),
    _merged: true,
  };

  STATE.segments[indices[0]] = merged;
  for (let i = 1; i < indices.length; i++) {
    STATE.segments[indices[i]]._deleted = true;
    STATE.segments[indices[i]].text     = '';
  }

  clearMergeSelection();
  renderScript(STATE.segments, indices[0]);
  _markDirty();
  log(`${segs.length} segments fusionnés en un seul.`, 'ok');
}

/* ── Déplacer un segment (haut/bas) ─────────────────────────── */
function moveSegment(idx, direction) {
  const targetIdx = direction === 'up' ? findPrevVisible(idx) : findNextVisible(idx);
  if (targetIdx < 0) return;

  pushUndo(`Déplacer segment ${idx + 1} ${direction === 'up' ? 'vers le haut' : 'vers le bas'}`);

  // Échanger les textes uniquement (les timecodes restent sacrés)
  const tempText   = STATE.segments[idx].text;
  STATE.segments[idx].text       = STATE.segments[targetIdx].text;
  STATE.segments[targetIdx].text = tempText;

  renderScript(STATE.segments, direction === 'up' ? targetIdx : idx);
  _markDirty();
  log(`Texte du segment ${idx + 1} échangé avec le segment ${targetIdx + 1}.`, 'info');
}

/* ── Dupliquer un segment ────────────────────────────────────── */
function duplicateSegment(idx) {
  const seg = STATE.segments[idx];
  if (!seg) return;

  pushUndo(`Dupliquer segment ${idx + 1}`);

  const dup = {
    id:       `dup_${Date.now()}`,
    index:    -1,
    start_ms: seg.end_ms,
    end_ms:   seg.end_ms + (seg.end_ms - seg.start_ms),
    text:     seg.text,
    _added:   true,
    _deleted: false,
  };

  STATE.segments.splice(idx + 1, 0, dup);
  renderScript(STATE.segments, idx + 1);
  _markDirty();
  log(`Segment ${idx + 1} dupliqué.`, 'ok');
}

/* ── Vider le texte d'un segment ────────────────────────────── */
function clearSegmentText(idx) {
  const seg = STATE.segments[idx];
  if (!seg) return;
  pushUndo(`Vider segment ${idx + 1}`);
  seg.text = '';
  renderScript(STATE.segments);
  _markDirty();
}

/* ── Restaurer un segment supprimé ──────────────────────────── */
function restoreSegment(idx) {
  const seg = STATE.segments[idx];
  if (!seg) return;
  pushUndo(`Restaurer segment ${idx + 1}`);
  seg._deleted = false;
  renderScript(STATE.segments);
  _markDirty();
  log(`Segment ${idx + 1} restauré.`, 'ok');
}


/* ═══════════════════════════════════════════════════════════
   SECTION 12 — MENU CONTEXTUEL
═══════════════════════════════════════════════════════════ */

function showContextMenu(e, idx) {
  e.preventDefault();
  STATE.contextSegIdx = idx;
  const seg = STATE.segments[idx];
  if (!seg) return;

  let menu = document.getElementById('seg-context-menu');
  if (!menu) {
    menu = document.createElement('div');
    menu.id = 'seg-context-menu';
    menu.className = 'context-menu';
    document.body.appendChild(menu);
    document.addEventListener('click', () => closeContextMenu(), { once: true });
  }

  menu.innerHTML = `
    <div class="ctx-item" onclick="splitSegmentAtCursor(${idx});closeContextMenu()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5"/></svg>
      Scinder ici
      <span class="ctx-kbd">Ctrl+↵</span>
    </div>
    <div class="ctx-item" onclick="addSegmentAfter(${idx});closeContextMenu()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
      Insérer segment après
      <span class="ctx-kbd">Alt+↵</span>
    </div>
    <div class="ctx-item" onclick="duplicateSegment(${idx});closeContextMenu()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
      Dupliquer
    </div>
    ${idx > 0 && !STATE.segments[idx-1]?._deleted ? `
    <div class="ctx-item" onclick="mergeWithPrev(${idx});closeContextMenu()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>
      Fusionner avec le précédent
      <span class="ctx-kbd">⌫</span>
    </div>` : ''}
    <div class="ctx-sep"></div>
    <div class="ctx-item" onclick="moveSegment(${idx},'up');closeContextMenu()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>
      Déplacer le texte vers le haut
    </div>
    <div class="ctx-item" onclick="moveSegment(${idx},'down');closeContextMenu()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>
      Déplacer le texte vers le bas
    </div>
    <div class="ctx-sep"></div>
    <div class="ctx-item" onclick="clearSegmentText(${idx});closeContextMenu()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      Vider le texte
    </div>
    ${seg._deleted ? `
    <div class="ctx-item" onclick="restoreSegment(${idx});closeContextMenu()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.85"/></svg>
      Restaurer ce segment
    </div>` : `
    <div class="ctx-item danger" onclick="deleteSegment(${idx}).then(()=>closeContextMenu())">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
      Supprimer ce segment
      <span class="ctx-kbd">Del</span>
    </div>`}
    <div class="ctx-sep"></div>
    <div class="ctx-item" onclick="removeFillersFromSegment(${idx});closeContextMenu()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
      Nettoyer les mots parasites
    </div>
  `;

  menu.classList.add('open');
  const rect = { x: e.clientX, y: e.clientY };
  menu.style.left = Math.min(rect.x, window.innerWidth - 220) + 'px';
  menu.style.top  = Math.min(rect.y, window.innerHeight - menu.offsetHeight - 20) + 'px';
}

function closeContextMenu() {
  const menu = document.getElementById('seg-context-menu');
  if (menu) menu.classList.remove('open');
}


/* ═══════════════════════════════════════════════════════════
   SECTION 13 — RECHERCHE / REMPLACEMENT
═══════════════════════════════════════════════════════════ */

function toggleSearch() {
  STATE.searchActive = !STATE.searchActive;
  const bar = document.getElementById('search-bar');
  if (!bar) return;
  bar.classList.toggle('open', STATE.searchActive);
  if (STATE.searchActive) {
    const input = document.getElementById('search-input');
    if (input) { input.focus(); input.select(); }
  } else {
    closeSearch();
  }
}

function closeSearch() {
  STATE.searchActive = false;
  STATE.searchMatches = [];
  STATE.searchIndex   = 0;
  const bar = document.getElementById('search-bar');
  if (bar) bar.classList.remove('open');
  clearSearchHighlights();
  updateSearchCount();
}

function onSearchInput() {
  const query = (document.getElementById('search-input')?.value || '').trim();
  clearSearchHighlights();
  if (!query) { updateSearchCount(); return; }
  doSearch(query);
}

function doSearch(query) {
  STATE.searchMatches = [];
  STATE.searchIndex   = 0;
  const lq = query.toLowerCase();
  document.querySelectorAll('.seg-txt').forEach((el, i) => {
    const text = el.textContent || '';
    let start  = 0;
    while (true) {
      const pos = text.toLowerCase().indexOf(lq, start);
      if (pos < 0) break;
      STATE.searchMatches.push({ elIdx: i, pos, len: query.length });
      start = pos + 1;
    }
  });
  if (STATE.searchMatches.length) {
    highlightSearchMatches(query);
    scrollToMatch(0);
  }
  updateSearchCount();
}

function highlightSearchMatches(query) {
  clearSearchHighlights();
  document.querySelectorAll('.seg-txt').forEach(el => {
    const html = el.innerHTML;
    const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    el.innerHTML = html.replace(new RegExp(`(${escaped})`, 'gi'),
      `<mark class="search-match-highlight">$1</mark>`);
  });
}

function clearSearchHighlights() {
  document.querySelectorAll('.seg-txt mark.search-match-highlight, .seg-txt mark.search-match-current')
    .forEach(m => {
      const parent = m.parentNode;
      parent.replaceChild(document.createTextNode(m.textContent), m);
      parent.normalize();
    });
}

function searchNext() {
  if (!STATE.searchMatches.length) return;
  STATE.searchIndex = (STATE.searchIndex + 1) % STATE.searchMatches.length;
  scrollToMatch(STATE.searchIndex);
  updateSearchCount();
}

function searchPrev() {
  if (!STATE.searchMatches.length) return;
  STATE.searchIndex = (STATE.searchIndex - 1 + STATE.searchMatches.length) % STATE.searchMatches.length;
  scrollToMatch(STATE.searchIndex);
  updateSearchCount();
}

function scrollToMatch(idx) {
  const marks = document.querySelectorAll('.seg-txt mark.search-match-highlight');
  marks.forEach((m, i) => {
    m.className = i === idx ? 'search-match-current' : 'search-match-highlight';
  });
  if (marks[idx]) marks[idx].scrollIntoView({ block: 'center', behavior: 'smooth' });
}

function updateSearchCount() {
  const el = document.getElementById('search-count');
  if (!el) return;
  if (!STATE.searchMatches.length) {
    el.textContent = STATE.searchActive ? 'Aucun résultat' : '';
  } else {
    el.textContent = `${STATE.searchIndex + 1} / ${STATE.searchMatches.length}`;
  }
}

function replaceCurrentMatch() {
  const query   = (document.getElementById('search-input')?.value    || '').trim();
  const replace = (document.getElementById('replace-input')?.value   || '');
  if (!query || !STATE.searchMatches.length) return;

  pushUndo('Remplacement');

  const match = STATE.searchMatches[STATE.searchIndex];
  const segEls = document.querySelectorAll('.seg-txt');
  const el = segEls[match.elIdx];
  if (!el) return;

  const idx = parseInt(el.dataset.index);
  const seg = STATE.segments[idx];
  if (!seg) return;

  const text = seg.text;
  const lq   = query.toLowerCase();
  const pos  = text.toLowerCase().indexOf(lq);
  if (pos < 0) return;

  seg.text = text.slice(0, pos) + replace + text.slice(pos + query.length);
  _markDirty();

  clearSearchHighlights();
  renderScript(STATE.segments);
  doSearch(query);
  log(`Remplacement : "${query}" → "${replace}"`, 'ok');
}

function replaceAllMatches() {
  const query   = (document.getElementById('search-input')?.value  || '').trim();
  const replace = (document.getElementById('replace-input')?.value || '');
  if (!query) return;

  pushUndo(`Remplacer tout "${query}"`);

  let count = 0;
  const re  = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
  STATE.segments.forEach(seg => {
    if (!seg._deleted && seg.text) {
      const newText = seg.text.replace(re, replace);
      if (newText !== seg.text) { seg.text = newText; count++; }
    }
  });

  _markDirty();
  renderScript(STATE.segments);
  if (count > 0) {
    log(`${count} segment(s) modifié(s) : "${query}" → "${replace}"`, 'ok');
    doSearch(query);
  } else {
    log(`Aucune occurrence de "${query}" trouvée.`, 'warn');
  }
}


/* ═══════════════════════════════════════════════════════════
   SECTION 14 — NETTOYAGE AUTOMATIQUE
═══════════════════════════════════════════════════════════ */

/* ── Supprimer les mots parasites d'un segment ──────────────── */
function removeFillersFromSegment(idx) {
  const seg = STATE.segments[idx];
  if (!seg || !seg.text) return;

  const lang     = document.getElementById('select-lang')?.value || 'fr';
  const fillers  = lang === 'en' ? FILLER_WORDS_EN : FILLER_WORDS_FR;
  const re       = new RegExp(`\\b(${fillers.join('|')})\\b`, 'gi');
  const newText  = seg.text.replace(re, '').replace(/\s{2,}/g, ' ').trim();

  if (newText === seg.text) { log(`Segment ${idx+1} : aucun mot parasite trouvé.`, 'info'); return; }

  pushUndo(`Nettoyer mots parasites segment ${idx+1}`);
  seg.text = newText;
  renderScript(STATE.segments);
  _markDirty();
  log(`Segment ${idx+1} nettoyé.`, 'ok');
}

/* ── Supprimer les mots parasites sur tout le script ─────────── */
async function removeAllFillers() {
  const lang    = document.getElementById('select-lang')?.value || 'fr';
  const fillers = lang === 'en' ? FILLER_WORDS_EN : FILLER_WORDS_FR;

  const ok = await tbConfirm(
    'Nettoyer tous les mots parasites',
    `Supprimer automatiquement les mots : ${fillers.slice(0,8).join(', ')}… de tout le script ?\n\nCette action peut être annulée (Ctrl+Z).`,
    { confirm: 'Nettoyer', type: 'warn' }
  );
  if (!ok) return;

  pushUndo('Nettoyer tous les mots parasites');

  const re = new RegExp(`\\b(${fillers.join('|')})\\b`, 'gi');
  let count = 0;
  STATE.segments.forEach(seg => {
    if (!seg._deleted && seg.text) {
      const newText = seg.text.replace(re, '').replace(/\s{2,}/g, ' ').trim();
      if (newText !== seg.text) { seg.text = newText; count++; }
    }
  });

  _markDirty();
  renderScript(STATE.segments);
  log(`${count} segment(s) nettoyé(s).`, 'ok');
}

/* ── Mise en majuscule des débuts de phrase ─────────────────── */
function capitalizeScript() {
  pushUndo('Capitaliser le script');
  STATE.segments.forEach(seg => {
    if (!seg._deleted && seg.text) {
      seg.text = seg.text.replace(/(^\s*|[.!?]\s+)([a-zà-ÿ])/g,
        (m, prefix, letter) => prefix + letter.toUpperCase());
    }
  });
  _markDirty();
  renderScript(STATE.segments);
  log('Majuscules appliquées en début de phrase.', 'ok');
}

/* ── Normaliser la ponctuation ──────────────────────────────── */
function normalizePunctuation() {
  pushUndo('Normaliser la ponctuation');
  let fixed = 0;
  STATE.segments.forEach(seg => {
    if (!seg._deleted && seg.text) {
      let t = seg.text;
      t = t.replace(/\s([,;.!?:])/g, '$1');       // Espace avant ponctuation
      t = t.replace(/([,;.!?:])([^\s])/g, '$1 $2'); // Pas d'espace après ponctuation
      t = t.replace(/\s{2,}/g, ' ').trim();
      if (t !== seg.text) { seg.text = t; fixed++; }
    }
  });
  _markDirty();
  renderScript(STATE.segments);
  log(`Ponctuation normalisée — ${fixed} segment(s) modifié(s).`, 'ok');
}


/* ═══════════════════════════════════════════════════════════
   SECTION 15 — SAUVEGARDE SCRIPT
═══════════════════════════════════════════════════════════ */

/* Helpers indicateur sauvegarde — compatibles nouveau HTML (save-indicator) ET ancien (saved-badge) */
function _markDirty() {
  STATE.scriptDirty = true;
  const el = document.getElementById('save-indicator');
  if (el) { el.className = 'save-indicator dirty'; el.textContent = '· non sauvegardé'; }
}

function _markSaved() {
  STATE.scriptDirty = false;
  const el = document.getElementById('save-indicator');
  if (el) {
    el.className = 'save-indicator saved'; el.textContent = '✓ sauvegardé';
    clearTimeout(el._t);
    el._t = setTimeout(() => { if (el.classList.contains('saved')) el.className = 'save-indicator hidden'; }, 8000);
  }
  // Compatibilité ancien badge flash
  const badge = document.getElementById('saved-badge');
  if (badge) { badge.classList.add('show'); setTimeout(() => badge.classList.remove('show'), 2500); }
}

async function saveScript() {
  if (!STATE.jobId) return false;

  const btn = document.getElementById('btn-save-script');
  if (btn) btn.disabled = true;

  // ── Vérification des débordements bloquants ──────────────────────────────
  const bloques = STATE.segments.filter(seg =>
    !seg._deleted && !seg._silence && segmentOverflow(seg).status === 'blocked'
  );
  if (bloques.length > 0) {
    const nums = bloques.map(s => {
      const idx  = STATE.segments.indexOf(s);
      const ov   = segmentOverflow(s);
      const maxW = Math.floor((s.end_ms - s.start_ms) / 1000 * TTS_WPM / 60);
      return `• Segment ${idx + 1} : retirez ${ov.words - maxW} mot(s) (vous en avez ${ov.words}, max ${maxW})`;
    }).join('\n');

    showError(
      `${bloques.length} segment(s) trop long(s) — sauvegarde impossible`,
      `${nums}\n\nRaccourcissez le texte ou scindez (Ctrl+Entrée).`
    );
    // Scroller + surligner le premier segment bloqué
    const firstIdx = STATE.segments.indexOf(bloques[0]);
    const firstRow = document.querySelector(`.seg-row[data-index="${firstIdx}"]`);
    if (firstRow) {
      firstRow.scrollIntoView({ block: 'center', behavior: 'smooth' });
      firstRow.style.outline = '2px solid var(--red)';
      firstRow.style.borderRadius = '6px';
      setTimeout(() => { firstRow.style.outline = ''; firstRow.style.borderRadius = ''; }, 3000);
    }
    if (btn) btn.disabled = false;
    return false;
  }

  // Construire la liste des segments à envoyer
  // Les segments silence sans texte sont envoyés avec text='' → le backend les traite en silence
  const editedSegments = [];
  STATE.segments.forEach((seg, i) => {
    if (seg._silence && !(seg.text || '').trim()) return; // silence pur → pas envoyé
    editedSegments.push({
      id:       seg.id,
      index:    seg.index !== undefined ? seg.index : i,
      start_ms: seg.start_ms || 0,
      end_ms:   seg.end_ms   || 0,
      text:     seg._deleted ? '' : (seg.text || ''),
      _added:   seg._added   || false,
      _deleted: seg._deleted || false,
    });
  });

  log(`Sauvegarde de ${editedSegments.length} segments…`);

  try {
    const res = await apiPost(`/api/jobs/${STATE.jobId}/split_segments/`,
                              { segments: editedSegments });
    if (res.segments && res.segments.length) {
      res.segments.forEach(s => {
        const seg = STATE.segments.find(x =>
          (x.index === s.index) ||
          (x._added && x.start_ms === s.start_ms && x.end_ms === s.end_ms)
        );
        if (seg) { seg.id = s.id; seg.index = s.index; seg._added = false; }
      });

      _markSaved();
      const w = document.getElementById('warn-step3');
      if (w) w.classList.remove('show');
      log(`${res.new_count || res.segments.length} segments sauvegardés.`, 'ok');
      if (btn) btn.disabled = false;
      return true;
    }
    log('Réponse inattendue du serveur : ' + JSON.stringify(res), 'warn');
    if (btn) btn.disabled = false;
    return false;
  } catch (e) {
    log('Erreur sauvegarde : ' + e.message, 'err');
    showError('Sauvegarde échouée', 'Impossible de sauvegarder. Vérifiez votre connexion.');
    if (btn) btn.disabled = false;
    return false;
  }
}


/* ═══════════════════════════════════════════════════════════
   SECTION 16 — IMPORT / EXPORT SCRIPT
═══════════════════════════════════════════════════════════ */

function exportScript() {
  if (!STATE.segments.length) { showError('Rien à exporter', 'Le script est vide.'); return; }

  const visible = STATE.segments.filter(s => !s._deleted);
  const lines   = visible.map((s, i) =>
    `[${i+1}]\t${msToTC(s.start_ms||0)}\t${s.text||''}`
  ).join('\n');

  const blob = new Blob([lines], { type: 'text/plain;charset=utf-8' });
  const a    = document.createElement('a');
  a.href     = URL.createObjectURL(blob);
  a.download = `script_${(STATE.jobId||'export').slice(0,8)}.txt`;
  a.click();
  log(`Script exporté (${visible.length} segments).`, 'ok');
}

function exportSRT() {
  if (!STATE.segments.length) return;
  const visible = STATE.segments.filter(s => !s._deleted && s.text.trim());
  const lines   = visible.map((s, i) => {
    const toSRTTime = ms => {
      const h = Math.floor(ms/3600000);
      const m = Math.floor((ms%3600000)/60000);
      const sec = Math.floor((ms%60000)/1000);
      const ms2 = ms%1000;
      return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')},${String(ms2).padStart(3,'0')}`;
    };
    return `${i+1}\n${toSRTTime(s.start_ms)} --> ${toSRTTime(s.end_ms)}\n${s.text}\n`;
  }).join('\n');

  const blob = new Blob([lines], { type: 'text/plain;charset=utf-8' });
  const a    = document.createElement('a');
  a.href     = URL.createObjectURL(blob);
  a.download = `subtitles_${(STATE.jobId||'export').slice(0,8)}.srt`;
  a.click();
  log(`Fichier SRT exporté (${visible.length} lignes).`, 'ok');
}

function importScript(input) {
  const file = input.files[0];
  if (!file) return;

  // Vérifier la taille (max 2Mo)
  if (file.size > 2 * 1024 * 1024) {
    showError('Fichier trop volumineux', 'Le fichier dépasse 2 Mo.');
    input.value = ''; return;
  }

  const reader = new FileReader();
  reader.onload = e => {
    const raw = e.target.result;
    let blocs = [];

    // ── Détection de format ──────────────────────────────────
    if (/^\d+\n\d{2}:\d{2}:\d{2},\d{3} --> /m.test(raw)) {
      // Format SRT
      raw.split(/\n\n+/).forEach(bloc => {
        const lines = bloc.trim().split('\n');
        if (lines.length >= 3) {
          const num   = parseInt(lines[0]);
          const texte = lines.slice(2).join(' ').trim();
          if (texte) blocs.push({ num, texte });
        }
      });
    } else if (/^\[\d+\]\t/m.test(raw)) {
      // Format tabs (export TutoBuilder)
      raw.split('\n').forEach(line => {
        line = line.trim(); if (!line) return;
        const m = line.match(/^\[(\d+)\]\t[\d:]+\t?(.*)/);
        if (m) blocs.push({ num: parseInt(m[1]), texte: m[2].trim() });
      });
    } else if (/^\[\d+\]\s+[\d:]+\s*$/m.test(raw)) {
      // Format [N] MM:SS suivi du texte
      raw.split(/\n{2,}/).forEach(bloc => {
        bloc = bloc.trim(); if (!bloc) return;
        const lignes  = bloc.split('\n');
        const header  = lignes[0].match(/^\[(\d+)\]\s+[\d:]+/);
        const num     = header ? parseInt(header[1]) : null;
        const texte   = lignes.slice(header ? 1 : 0)
          .filter(l => !/^\[\d+\]/.test(l.trim())).join(' ').trim();
        blocs.push({ num, texte });
      });
    } else {
      // Format brut : paragraphes
      raw.split(/\n{2,}/).forEach((bloc, i) => {
        const t = bloc.replace(/^\[\d+\]\s*[\d:]*\s*/gm, '').trim();
        if (t) blocs.push({ num: i + 1, texte: t });
      });
    }

    if (!blocs.length) {
      showError('Import échoué', 'Aucun segment détecté dans ce fichier. Vérifiez le format (.txt, .srt).');
      input.value = ''; return;
    }

    // Avertir si le nombre de blocs est très différent
    const diff = Math.abs(blocs.length - STATE.segments.length);
    if (diff > STATE.segments.length * 0.5) {
      log(`⚠ Le fichier contient ${blocs.length} segments, le projet en a ${STATE.segments.length}. Certains segments pourraient ne pas correspondre.`, 'warn');
    }

    pushUndo('Import script');

    let modifies = 0;
    blocs.forEach(bloc => {
      if (!bloc.texte) return;
      const seg = bloc.num ? STATE.segments[bloc.num - 1] : null;
      if (!seg || seg._deleted) return;
      seg.text = bloc.texte;
      modifies++;
    });

    renderScript(STATE.segments);
    _markDirty();
    log(`Import OK : ${modifies} texte(s) mis à jour. Timecodes inchangés.`, 'ok');
    input.value = '';
  };
  reader.readAsText(file, 'utf-8');
}

async function resetScript() {
  if (!STATE.jobId) return;
  const ok = await tbConfirm(
    'Réinitialiser le script',
    'Revenir aux textes originaux de Whisper ?\n\nToutes vos modifications seront perdues. Cette action ne peut pas être annulée.',
    { confirm: 'Réinitialiser', type: 'danger' }
  );
  if (!ok) return;

  try {
    const raw  = await (await fetch(`/api/jobs/${STATE.jobId}/segments/`)).json();
    const data = Array.isArray(raw) ? raw : (raw.results || []);
    if (data.length) {
      STATE.segments = data.map(s => ({
        id: s.id, index: s.index,
        start_ms: s.start_ms, end_ms: s.end_ms,
        start: s.start_ms, end: s.end_ms,
        text: s.text,
      }));
      STATE.undoStack = []; STATE.redoStack = [];
      renderScript(STATE.segments);
      STATE.scriptDirty = false;
      log('Script réinitialisé — textes Whisper originaux restaurés.', 'ok');
    }
  } catch (e) {
    log('Erreur réinitialisation : ' + e.message, 'err');
    showError('Erreur', 'Impossible de réinitialiser. Vérifiez votre connexion.');
  }
}


/* ═══════════════════════════════════════════════════════════
   SECTION 17 — ÉDITEUR PLEIN ÉCRAN
═══════════════════════════════════════════════════════════ */

function openFullEditor() {
  const visible = STATE.segments.filter(s => !s._deleted);
  document.getElementById('full-editor-textarea').value =
    visible.map(s => s.text || '').join('\n\n');
  document.getElementById('full-editor-modal').classList.add('open');
  document.getElementById('full-editor-textarea').focus();
}

function closeFullEditor() {
  document.getElementById('full-editor-modal').classList.remove('open');
}

function applyFullEditor() {
  const blocks  = document.getElementById('full-editor-textarea').value
    .split(/\n\n+/).filter(b => b.trim());
  const visible = STATE.segments.filter(s => !s._deleted);

  if (blocks.length !== visible.length) {
    log(`⚠ Attention : ${blocks.length} paragraphes pour ${visible.length} segments. Les segments supplémentaires seront ignorés.`, 'warn');
  }

  pushUndo('Éditeur plein écran');

  blocks.forEach((block, i) => {
    if (visible[i]) visible[i].text = block.trim();
  });

  renderScript(STATE.segments);
  _markDirty();
  closeFullEditor();
  log(`${Math.min(blocks.length, visible.length)} segments mis à jour.`, 'ok');
}


/* ═══════════════════════════════════════════════════════════
   SECTION 18 — BARRE DE TOOLBAR DU SCRIPT (HTML dynamique)
═══════════════════════════════════════════════════════════ */

function injectScriptToolbar() {
  // Barre de recherche
  const ed = document.getElementById('script-editor');
  if (!ed) return;

  const searchBar = document.createElement('div');
  searchBar.className = 'search-bar';
  searchBar.id = 'search-bar';
  searchBar.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13" style="color:var(--text-3);flex-shrink:0"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
    <input type="text" id="search-input" placeholder="Rechercher dans le script…" oninput="onSearchInput()" onkeydown="if(e.key==='Enter')searchNext(); if(e.key==='Escape')closeSearch();">
    <input type="text" id="replace-input" class="replace-input" placeholder="Remplacer par…" style="flex:1;padding:5px 8px;border-radius:var(--r);border:1px solid var(--border-md);background:var(--surface);color:var(--text-1);font-size:12px;outline:none;">
    <span class="search-count" id="search-count"></span>
    <button class="script-action-btn" onclick="searchPrev()" title="Précédent (Maj+Entrée)">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="10" height="10"><polyline points="18 15 12 9 6 15"/></svg>
    </button>
    <button class="script-action-btn" onclick="searchNext()" title="Suivant (Entrée)">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="10" height="10"><polyline points="6 9 12 15 18 9"/></svg>
    </button>
    <button class="script-action-btn" onclick="toggleReplaceBar()" title="Afficher remplacer">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="10" height="10"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
    </button>
    <button class="script-action-btn primary" onclick="replaceCurrentMatch()" title="Remplacer" style="display:none" id="btn-replace-one">Rem.</button>
    <button class="script-action-btn" onclick="replaceAllMatches()" title="Tout remplacer" style="display:none" id="btn-replace-all">Tout</button>
    <button class="script-action-btn" onclick="closeSearch()" title="Fermer">✕</button>`;

  ed.parentNode.insertBefore(searchBar, ed);

  // Toolbar étendue (nettoyage, capitalisation…)
  const toolbar = document.createElement('div');
  toolbar.className = 'script-toolbar-ext';
  toolbar.id = 'script-toolbar-ext';
  toolbar.style.display = 'none';
  toolbar.innerHTML = `
    <button class="script-action-btn" onclick="removeAllFillers()" title="Supprimer euh, hmm, voilà…">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="10" height="10"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
      Mots parasites
    </button>
    <button class="script-action-btn" onclick="capitalizeScript()" title="Capitaliser les débuts de phrase">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="10" height="10"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></svg>
      Capitaliser
    </button>
    <button class="script-action-btn" onclick="normalizePunctuation()" title="Corriger les espaces autour de la ponctuation">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="10" height="10"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      Ponctuation
    </button>
    <button class="script-action-btn" onclick="exportSRT()" title="Exporter en .srt">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="10" height="10"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
      Export SRT
    </button>
    <span id="script-stats" style="margin-left:auto"></span>`;

  ed.parentNode.insertBefore(toolbar, ed);
}

function toggleReplaceBar() {
  const bar = document.getElementById('search-bar');
  if (!bar) return;
  const showing = bar.classList.toggle('show-replace');
  const r1 = document.getElementById('btn-replace-one');
  const r2 = document.getElementById('btn-replace-all');
  if (r1) r1.style.display = showing ? '' : 'none';
  if (r2) r2.style.display = showing ? '' : 'none';
  if (showing) document.getElementById('replace-input')?.focus();
}

function toggleScriptToolbar() {
  const bar = document.getElementById('script-toolbar-ext');
  if (bar) bar.style.display = bar.style.display === 'none' ? 'flex' : 'none';
}

function showScriptUI() {
  // Nouveau HTML — script-actions-group + undo-group
  const actGroup = document.getElementById('script-actions-group');
  if (actGroup) actGroup.style.display = 'flex';
  const undoGrp = document.getElementById('undo-redo-btns');
  if (undoGrp) undoGrp.classList.add('visible');
  const sep = document.getElementById('sep-undo');
  if (sep) sep.style.display = 'block';

  // Ancien HTML — script-buttons (rangée 2) + btn-save-script
  const row2 = document.getElementById('script-buttons');
  if (row2) row2.classList.add('visible');
  const btnSave = document.getElementById('btn-save-script');
  if (btnSave) btnSave.style.display = 'flex';

  injectScriptToolbar();
}


/* ═══════════════════════════════════════════════════════════
   SECTION 19 — RETOUR EN ARRIÈRE
═══════════════════════════════════════════════════════════ */

async function resetToStep(step) {
  if (!STATE.jobId) return;
  const configs = {
    2: { title: 'Annuler la voix off', msg: 'Revenir à la correction du script ?\nLa transcription est conservée.', confirm: 'Annuler la voix' },
    3: { title: "Annuler l'export",   msg: "Revenir à la synthèse vocale ?\nLa voix générée est conservée.", confirm: "Annuler l'export" },
  };
  const cfg = configs[step];
  if (!cfg) return;

  const ok = await tbConfirm(cfg.title, cfg.msg, { confirm: cfg.confirm, type: 'warn' });
  if (!ok) return;

  try {
    const r = await apiPost(`/api/jobs/${STATE.jobId}/reset/`, { step });
    if (r.status !== 'ok') throw new Error(r.message || 'Réinitialisation refusée');

    const videoUrl = document.getElementById('video-url').value;
    if (videoUrl && step === 2) {
      STATE.isFinalVideo = false;
      const v = document.getElementById('video-player');
      if (v) { v.src = videoUrl; }
      const hud = document.getElementById('hud-mode');
      if (hud) { hud.textContent = 'SOURCE'; hud.style.color = ''; }
      const dl = document.getElementById('btn-download');
      if (dl) dl.style.display = 'none';
    }

    if (step === 2) {
      goToStep(3);
      const btnTts = document.getElementById('btn-tts');
      if (btnTts) btnTts.disabled = false;
      document.getElementById('btn-reset-to-transcribed').style.display = 'none';
      updateCallbackButtons(2);
      STATE.ttsReady = false;
      setProgress('Transcription disponible — regénérez la voix', 100, 'done');
      log('Voix annulée. Modifiez le script et regénérez.', 'ok');
    } else {
      goToStep(4);
      document.getElementById('btn-export').disabled = false;
      document.getElementById('btn-reset-to-tts').style.display = 'none';
      updateCallbackButtons(3);
      STATE.isFinalVideo = false;
      setProgress("Voix disponible — relancez l'assemblage", 100, 'done');
      log("Export annulé. Relancez l'assemblage.", 'ok');
    }
  } catch (e) {
    log('Erreur reset : ' + e.message, 'err');
    showError('Erreur', 'Impossible d\'annuler. Réessayez.');
  }
}


/* ═══════════════════════════════════════════════════════════
   SECTION 20 — REPRISE DE PROJET
═══════════════════════════════════════════════════════════ */

async function resumeProject(jobId, jobStatus) {
  log(`Reprise du projet (statut : ${jobStatus})…`);

  try {
    const raw  = await (await fetch(`/api/jobs/${jobId}/segments/`)).json();
    const data = Array.isArray(raw) ? raw : (raw.results || []);
    if (data.length) {
      STATE.segments = data.map(s => ({
        id: s.id, index: s.index,
        start_ms: s.start_ms, end_ms: s.end_ms,
        start: s.start_ms, end: s.end_ms,
        text: s.text,
      }));
      renderScript(STATE.segments);
      showScriptUI();
      if (['done','synthesizing'].includes(jobStatus)) {
        const rb = document.getElementById('btn-reset-to-transcribed');
        if (rb) rb.style.display = 'flex';
      }
      if (jobStatus === 'done') {
        const rb2 = document.getElementById('btn-reset-to-tts');
        if (rb2) rb2.style.display = 'flex';
      }
      if (data[0]) {
        document.getElementById('sub-preview-text').value = data[0].text || '';
        drawSubPreview();
      }
      log(`${STATE.segments.length} segments chargés.`, 'ok');
    }
  } catch (e) { log('Impossible de charger les segments : ' + e.message, 'warn'); }

  // Bannière de reprise
  const bannerMsgs = {
    pending:      ['Vidéo chargée',              'Lancez la transcription à l\'étape 2'],
    extracting:   ['Extraction audio en cours',  'Veuillez patienter…'],
    transcribing: ['Transcription en cours',     'Veuillez patienter…'],
    transcribed:  ['Transcription disponible',   'Corrigez le script puis générez la voix'],
    synthesizing: ['Synthèse vocale en cours',   'Veuillez patienter…'],
    done:         ['Voix générée',               'Assemblez la vidéo finale à l\'étape 4'],
    error:        ['Une erreur s\'est produite', 'Consultez le journal ci-dessous'],
  };
  const bm = bannerMsgs[jobStatus] || bannerMsgs.pending;
  const bt = document.getElementById('resume-banner-text');
  const bs = document.getElementById('resume-banner-step');
  const bb = document.getElementById('resume-banner');
  if (bt) bt.textContent = bm[0];
  if (bs) bs.textContent = bm[1];
  if (bb) bb.classList.add('show');

  // Initialiser l'état selon le statut
  switch (jobStatus) {
    case 'pending':
      const _videoUrl = document.getElementById('video-url').value;
      if (_videoUrl) {
        goToStep(2);
        document.getElementById('btn-transcribe').disabled = false;
        setProgress('Vidéo chargée — lancez la transcription à l\'étape 2', 5, 'done');
      } else {
        goToStep(1);
        document.getElementById('btn-transcribe').disabled = false;
        setProgress('Vidéo chargée — sélectionnez un projet et lancez la transcription', 5, 'done');
      }
      break;
    case 'extracting': case 'transcribing':
      goToStep(2); setProgress('Transcription en cours…', 40); poll(jobId);
      break;
    case 'transcribed':
      goToStep(3);
      document.getElementById('btn-transcribe').disabled = false;
      document.getElementById('btn-tts').disabled        = false;
      updateCallbackButtons(2);
      setProgress('Transcription terminée — corrigez le script puis générez la voix', 100, 'done');
      break;
    case 'synthesizing':
      goToStep(3); setProgress('Synthèse vocale en cours…', 60); poll(jobId);
      break;
    case 'done': {
      goToStep(4);
      document.getElementById('btn-transcribe').disabled = false;
      document.getElementById('btn-tts').disabled        = false;
      STATE.ttsReady = true;
      document.getElementById('btn-export').disabled     = false;
      updateCallbackButtons(STATE.isFinalVideo ? 4 : 3);
      const finalUrl = document.getElementById('final-url').value;
      if (finalUrl) {
        STATE.isFinalVideo = true;
        setProgress('Vidéo finale disponible', 100, 'done');
        document.getElementById('btn-download').href          = finalUrl;
        document.getElementById('btn-download').style.display = 'flex';
      } else {
        setProgress('Voix générée — assemblez la vidéo finale', 100, 'done');
      }
      break;
    }
    case 'error':
      goToStep(1);
      document.getElementById('btn-transcribe').disabled = false;
      if (STATE.segments.length) { document.getElementById('btn-tts').disabled = false; goToStep(3); }
      setProgress('Une erreur s\'est produite — consultez le journal', 0, 'error');
      showError('Erreur lors du traitement', 'Consultez le journal pour plus de détails.');
      break;
    default: goToStep(1);
  }
}


/* ═══════════════════════════════════════════════════════════
   SECTION 21 — UPLOAD
═══════════════════════════════════════════════════════════ */

const uploadZone = document.getElementById('upload-zone');
const fileInput  = document.getElementById('file-input');

if (uploadZone) {
  uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('dragover'); });
  uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
  uploadZone.addEventListener('drop', e => {
    e.preventDefault(); uploadZone.classList.remove('dragover');
    if (e.dataTransfer.files[0]) pickFile(e.dataTransfer.files[0]);
  });
}
if (fileInput) fileInput.addEventListener('change', () => { if (fileInput.files[0]) pickFile(fileInput.files[0]); });

const ALLOWED_VIDEO_TYPES = ['video/mp4','video/x-msvideo','video/quicktime','video/x-matroska','video/webm','video/x-ms-wmv','video/mpeg'];
const MAX_FILE_SIZE_MB     = 2048; // 2 Go

function pickFile(file) {
  // Validation type
  if (!ALLOWED_VIDEO_TYPES.includes(file.type) && !file.name.match(/\.(mp4|mkv|avi|mov|wmv|webm|mpeg|mpg)$/i)) {
    showError('Format non supporté', `Le fichier "${file.name}" n'est pas une vidéo reconnue.\nFormats acceptés : MP4, MKV, AVI, MOV, WMV, WebM`);
    return;
  }
  // Validation taille
  const sizeMB = file.size / (1024 * 1024);
  if (sizeMB > MAX_FILE_SIZE_MB) {
    showError('Fichier trop volumineux', `Le fichier fait ${sizeMB.toFixed(0)} Mo. Maximum : ${MAX_FILE_SIZE_MB} Mo.`);
    return;
  }

  STATE.uploadedFile = file;
  const fnEl = document.getElementById('upload-filename');
  if (fnEl) fnEl.textContent = `${file.name} (${sizeMB.toFixed(1)} Mo)`;

  const v = document.getElementById('video-player');
  if (v) { v.src = URL.createObjectURL(file); v.style.display = 'block'; }
  const ph = document.getElementById('video-placeholder');
  if (ph) ph.style.display = 'none';

  const btnT = document.getElementById('btn-transcribe');
  if (btnT) btnT.disabled = false;
  const sec2 = document.getElementById('wsec-2');
  if (sec2) { sec2.classList.remove('locked'); sec2.classList.add('active'); }

  setProgress(`Vidéo chargée (${sizeMB.toFixed(1)} Mo) — sélectionnez un projet et lancez la transcription`, 5, 'done');
  log(`Vidéo sélectionnée : ${file.name} (${sizeMB.toFixed(1)} Mo)`);
  document.getElementById('resume-banner')?.classList.remove('show');
  hideAllWarns();
}


/* ═══════════════════════════════════════════════════════════
   SECTION 22 — TRANSCRIPTION
═══════════════════════════════════════════════════════════ */

document.getElementById('btn-transcribe')?.addEventListener('click', async () => {
  const pid = document.getElementById('select-project')?.value;
  if (!pid) {
    showWarn('warn-step1');
    showError('Projet manquant', 'Sélectionnez un projet existant ou créez-en un nouveau avant de transcrire.');
    return;
  }
  if (!STATE.uploadedFile && !STATE.jobId) {
    showWarn('warn-step1');
    showError('Vidéo manquante', 'Importez une vidéo avant de lancer la transcription.');
    return;
  }
  // Avertir si script existant
  if (STATE.segments.length > 0) {
    const ok = await tbConfirm(
      'Relancer la transcription',
      'Relancer la transcription effacera le script actuel et la voix générée.\n\nContinuer ?',
      { confirm: 'Relancer', type: 'warn' }
    );
    if (!ok) return;
  }

  document.getElementById('btn-transcribe').disabled = true;
  goToStep(2); setProgress('Envoi de la vidéo…', 8);

  if (STATE.uploadedFile) {
    const fd = new FormData();
    fd.append('video_file', STATE.uploadedFile);
    fd.append('project_id', pid);
    fd.append('stt_engine', 'faster_whisper');
    fd.append('tts_engine', document.getElementById('select-tts').value);
    fd.append('language',   document.getElementById('select-lang').value);
    try {
      const r  = await fetch('/api/jobs/', { method:'POST', headers:{'X-CSRFToken':CSRF}, body:fd });
      const up = await r.json();
      if (!up.id) {
        log('Upload échoué : ' + JSON.stringify(up), 'err');
        showError('Upload échoué', 'La vidéo n\'a pas pu être envoyée. Vérifiez le format.');
        document.getElementById('btn-transcribe').disabled = false;
        return;
      }
      STATE.jobId = up.id;
    } catch (e) {
      log('Erreur upload : ' + e.message, 'err');
      showError('Erreur réseau', 'Impossible d\'envoyer la vidéo. Vérifiez votre connexion.');
      document.getElementById('btn-transcribe').disabled = false;
      return;
    }
  }

  log('Transcription démarrée…');
  setProgress('Transcription en cours…', 20);
  connectWS(STATE.jobId);

  const tr = await apiPost(`/api/jobs/${STATE.jobId}/transcribe/`, {
    stt_engine: 'faster_whisper',
    language:   document.getElementById('select-lang').value,
  });

  if (tr.task_id) {
    if (!STATE.ws || STATE.ws.readyState !== WebSocket.OPEN) poll(STATE.jobId);
  } else {
    log('Erreur : ' + JSON.stringify(tr), 'err');
    showError('Transcription échouée', tr.detail || 'Une erreur interne s\'est produite.');
    document.getElementById('btn-transcribe').disabled = false;
  }
});


/* ═══════════════════════════════════════════════════════════
   SECTION 23 — SYNTHÈSE VOCALE
═══════════════════════════════════════════════════════════ */

document.getElementById('btn-tts')?.addEventListener('click', async () => {
  if (!STATE.jobId) {
    showError('Aucun projet actif', 'Importez et transcrivez une vidéo en premier.');
    return;
  }
  if (!STATE.segments.length) {
    showError('Script vide', 'Aucun segment disponible. Lancez la transcription à l\'étape 2.');
    return;
  }

  // Vérifier qu'il y a au moins un segment non vide
  const nonVides = STATE.segments.filter(s => !s._deleted && (s.text||'').trim());
  if (nonVides.length === 0) {
    showError('Script vide', 'Tous les segments sont vides. Ajoutez du texte avant de synthétiser.');
    return;
  }

  // Avertir pour les segments trop longs
  const tropLongs = nonVides.filter(s => {
    const words    = (s.text||'').trim().split(/\s+/).length;
    const ttsMs    = words / 130 * 60 * 1000;
    const vidMs    = s.end_ms - s.start_ms;
    return ttsMs > vidMs * 1.4;
  });
  if (tropLongs.length > 0) {
    const ok = await tbConfirm(
      `${tropLongs.length} segment(s) trop long(s)`,
      `${tropLongs.length} segment(s) ont un texte un peu long pour leur durée vidéo.\nLa vidéo sera gelée quelques secondes le temps que la voix finisse naturellement.\nVous pouvez raccourcir les textes ou continuer quand même.`,
      { confirm: 'Continuer quand même', cancel: 'Corriger d\'abord', type: 'warn' }
    );
    if (!ok) return;
  }

  // Sauvegarder si nécessaire
  if (STATE.scriptDirty) {
    const ok = await tbConfirm(
      'Script non sauvegardé',
      'Vous avez des modifications non sauvegardées. Sauvegarder avant de synthétiser ?',
      { confirm: 'Sauvegarder et continuer', cancel: 'Annuler', type: 'warn' }
    );
    if (!ok) return;
    const saved = await saveScript();
    if (!saved) {
      showError('Sauvegarde échouée', 'Impossible de sauvegarder. Vérifiez votre connexion.');
      return;
    }
  }

  STATE.ttsReady = false; STATE.ttsOkCount = 0; STATE.ttsEchecs = [];
  document.getElementById('btn-export').disabled = true;
  document.getElementById('btn-tts').disabled    = true;
  goToStep(3); setProgress('Envoi de la requête…', 5);

  const r = await apiPost(`/api/jobs/${STATE.jobId}/synthesize/`, {
    tts_engine: document.getElementById('select-tts').value,
    voice:      STATE.selectedVoice,
    language:   document.getElementById('select-lang').value,
  });

  if (r.task_id) {
    log('Synthèse vocale démarrée…');
    if (!STATE.ws || STATE.ws.readyState !== WebSocket.OPEN) poll(STATE.jobId);
  } else {
    const msg = r.detail || r.error || r.message || JSON.stringify(r);
    log('Synthèse refusée : ' + msg, 'err');
    showError('Synthèse refusée', msg);
    setProgress('Échec — vérifiez le journal', 0, 'error');
    document.getElementById('btn-tts').disabled = false;
  }
});


/* ═══════════════════════════════════════════════════════════
   SECTION 24 — EXPORT
═══════════════════════════════════════════════════════════ */

document.getElementById('btn-export')?.addEventListener('click', async () => {
  if (!STATE.jobId) { showError('Aucun projet actif', 'Commencez par importer une vidéo.'); return; }

  if (!STATE.ttsReady) {
    if (STATE.ttsOkCount === 0 && STATE.ttsTotal === 0) {
      showError('Voix off manquante', 'Vous n\'avez pas encore généré la voix off. Complétez l\'étape 3 avant d\'assembler.');
    } else {
      showError('Synthèse échouée', `0 segment valide sur ${STATE.ttsTotal}. Relancez la synthèse à l\'étape 3.`);
    }
    return;
  }

  if (STATE.ttsEchecs.length > 0) {
    const ok = await tbConfirm(
      'Synthèse partielle',
      `${STATE.ttsEchecs.length} segment(s) seront silencieux (numéros : ${STATE.ttsEchecs.join(', ')}).\nContinuer quand même ?`,
      { confirm: 'Assembler quand même', cancel: 'Annuler', type: 'warn' }
    );
    if (!ok) return;
  }

  document.getElementById('btn-export').disabled = true;
  STATE.isFinalVideo = false;
  goToStep(4); setProgress('Démarrage du montage…', 3);
  log('Assemblage lancé…');

  const r = await apiPost(`/api/jobs/${STATE.jobId}/export/`, { subtitle_style: getSubStyle() });

  if (r.task_id) {
    log('Export en cours — cela peut prendre plusieurs minutes…', 'info');
    pollExport(STATE.jobId);
  } else {
    const msg = r.detail || r.error || r.message || JSON.stringify(r);
    log('Export refusé : ' + msg, 'err');
    showError('Export refusé', msg);
    setProgress('Échec — vérifiez le journal', 0, 'error');
    document.getElementById('btn-export').disabled = false;
  }
});

function pollExport(jobId) {
  const ETAPES = [
    'Corrections Whisper (overlaps, timecodes)…',
    'Construction de la timeline…',
    'Extraction des clips vidéo à x1.0…',
    'Compression des silences longs…',
    'Ajustement TTS (atempo)…',
    'Assemblage des clips…',
    'Mixage audio composite…',
    'Génération des sous-titres ASS…',
    'Encodage final H.264 + AAC…',
  ];
  let pct = 3, etapeIdx = 0;

  const iv = setInterval(async () => {
    pct      = Math.min(pct + 1.2, 88);
    etapeIdx = Math.min(Math.floor(pct / 11), ETAPES.length - 1);
    setProgress(ETAPES[etapeIdx], Math.round(pct));

    try {
      const job = await (await fetch(`/api/jobs/${jobId}/`)).json();
      if (job.status === 'done' && pct > 8) {
        clearInterval(iv);
        setProgress('Vérification du fichier final…', 92);
        const url = `/media/exports/${jobId}/final.mp4`;
        const ok  = await waitForFile(url);
        if (ok) {
          STATE.isFinalVideo = true;
          setProgress('Vidéo finale prête !', 100, 'done');
          showFinalVideo(url);
        } else {
          setProgress('Export terminé mais fichier inaccessible', 0, 'error');
          showError('Fichier inaccessible', 'L\'export a terminé mais le fichier n\'est pas encore accessible. Attendez 30s et rechargez.');
        }
        document.getElementById('btn-export').disabled = false;
      }
      if (job.status === 'error') {
        clearInterval(iv);
        const msg = job.error_message || 'Erreur inconnue';
        setProgress('Erreur lors du montage', 0, 'error');
        log('Export échoué : ' + msg, 'err');
        showError('Montage échoué', msg + '\n\nActions : (1) vérifiez ffmpeg, (2) relancez la synthèse, (3) essayez sans sous-titres.');
        document.getElementById('btn-export').disabled = false;
      }
    } catch (_) {}
  }, 3500);
}

async function waitForFile(url, maxAttempts = 60, delayMs = 5000) {
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const r    = await fetch(url, { method: 'HEAD' });
      const size = parseInt(r.headers.get('content-length') || '0');
      if (r.ok && size > 1_000_000) {
        log(`Fichier prêt (${(size/1024/1024).toFixed(1)} Mo)`, 'ok');
        return true;
      }
    } catch (_) {}
    await new Promise(res => setTimeout(res, delayMs));
  }
  return false;
}


/* ═══════════════════════════════════════════════════════════
   SECTION 25 — BOUTONS CALLBACK
═══════════════════════════════════════════════════════════ */

function updateCallbackButtons(step) {
  const r2t = document.getElementById('btn-reset-to-transcribed');
  const r2s = document.getElementById('btn-reset-to-tts');
  const dl  = document.getElementById('btn-download');
  if (r2t) r2t.style.display = step >= 3 ? 'flex' : 'none';
  if (r2s) r2s.style.display = step >= 4 ? 'flex' : 'none';
  if (dl  && step < 4) dl.style.display = 'none';
}

function showFinalVideo(url) {
  STATE.isFinalVideo = true;
  const v = document.getElementById('video-player');
  if (!v) return;
  v.pause(); v.removeAttribute('src'); v.load();
  setTimeout(() => {
    v.src = url + '?t=' + Date.now(); v.style.display = 'block';
    document.getElementById('video-placeholder').style.display = 'none';
    const hud = document.getElementById('hud-mode');
    if (hud) { hud.textContent = 'FINALE'; hud.style.color = '#1A8C50'; }
    const dl = document.getElementById('btn-download');
    if (dl) { dl.href = url; dl.style.display = 'flex'; }
    v.play().catch(() => {});
    log('Vidéo finale disponible — prévisualisez et téléchargez.', 'ok');
    updateCallbackButtons(4);
  }, 800);
}


/* ═══════════════════════════════════════════════════════════
   SECTION 26 — WEBSOCKET
═══════════════════════════════════════════════════════════ */

function connectWS(jid) {
  if (STATE.ws) STATE.ws.close();
  const proto  = location.protocol === 'https:' ? 'wss' : 'ws';
  STATE.ws     = new WebSocket(`${proto}://${location.host}/ws/job/${jid}/`);
  const dot    = document.getElementById('ws-dot');
  const lbl    = document.getElementById('ws-status');
  STATE.ws.onopen    = () => { if(dot) dot.className='ws-dot connected'; if(lbl) lbl.textContent='Connecté'; };
  STATE.ws.onclose   = () => { if(dot) dot.className='ws-dot'; if(lbl) lbl.textContent='Déconnecté'; };
  STATE.ws.onerror   = () => { if(dot) dot.className='ws-dot error'; if(lbl) lbl.textContent='Erreur WS'; };
  STATE.ws.onmessage = e => onWS(JSON.parse(e.data));
}

function onWS(msg) {
  switch (msg.type) {

    case 'status':
      log(msg.message, msg.level || 'info');
      break;

    case 'progress':
      setProgress(msg.label || '…', msg.percent || 0);
      break;

    case 'waveform':
      STATE.waveform = msg.data; drawWave(msg.data);
      break;

    case 'segments':
      STATE.segments = msg.data.map(s => ({
        id:       s.id,
        index:    s.index !== undefined ? s.index : s.i,
        start_ms: s.start_ms || s.start || 0,
        end_ms:   s.end_ms   || s.end   || 0,
        text:     s.text || '',
      }));
      renderScript(STATE.segments);
      showScriptUI();
      setProgress('Transcription terminée — corrigez le script si nécessaire', 100, 'done');
      goToStep(3);
      document.getElementById('btn-tts').disabled        = false;
      document.getElementById('btn-transcribe').disabled = false;
      if (STATE.segments[0]) {
        document.getElementById('sub-preview-text').value = STATE.segments[0].text || '';
        drawSubPreview();
      }
      log(`${STATE.segments.length} segments transcrits.`, 'ok');
      break;

    case 'tts_progress':
      STATE.ttsOkCount = msg.current;
      STATE.ttsTotal   = msg.total;
      setProgress(`Voix off — segment ${msg.current}/${msg.total}…`,
                  Math.round(msg.current / msg.total * 100));
      break;

    case 'tts_done': {
      const nbOk     = msg.nb_ok     || 0;
      const nbTotal  = msg.nb_total  || STATE.segments.length || 0;
      const nbEchecs = msg.nb_echecs || 0;
      const echecs   = msg.echecs    || [];

      STATE.ttsOkCount = nbOk; STATE.ttsTotal = nbTotal; STATE.ttsEchecs = echecs;
      STATE.ttsReady   = nbOk > 0;
      document.getElementById('btn-tts').disabled = false;
      document.getElementById('btn-reset-to-transcribed').style.display = 'flex';

      if (nbOk === 0) {
        STATE.ttsReady = false;
        setProgress('Synthèse échouée — aucun segment généré', 0, 'error');
        showError('Synthèse vocale échouée',
          'Aucun segment audio n\'a pu être généré.\nVérifiez votre clé API dans .env et votre connexion internet, puis relancez la synthèse.');
        log(`Synthèse échouée : 0/${nbTotal} segments.`, 'err');
        document.getElementById('btn-export').disabled = true;

      } else if (nbEchecs > 0) {
        STATE.ttsReady = true;
        setProgress(`Voix générée — ${nbOk}/${nbTotal} OK (${nbEchecs} ignorés)`,
                    Math.round(nbOk/nbTotal*100), 'done');
        showError(`${nbEchecs} segment(s) non générés`,
          `${nbOk}/${nbTotal} segments OK. Les segments ${echecs.join(', ')} seront silencieux.`);
        log(`Synthèse partielle : ${nbOk}/${nbTotal} OK`, 'warn');
        goToStep(4);
        document.getElementById('btn-export').disabled = false;

      } else {
        STATE.ttsReady = true;
        setProgress('Voix off générée — assemblez la vidéo finale', 100, 'done');
        log(`Synthèse réussie : ${nbOk}/${nbTotal} segments.`, 'ok');
        goToStep(4);
        document.getElementById('btn-export').disabled = false;
      }
      break;
    }

    case 'export_done':
      (async () => {
        if (STATE.isFinalVideo) return;
        const url = msg.download_url;
        const ok  = await waitForFile(url, 12, 3000);
        if (ok) { STATE.isFinalVideo = true; setProgress('Vidéo finale prête !', 100, 'done'); showFinalVideo(url); }
        else { setProgress('Fichier généré mais introuvable', 0, 'error'); showError('Fichier introuvable', 'Rechargez la page dans 10 secondes.'); }
        document.getElementById('btn-export').disabled = false;
      })();
      break;

    case 'error':
      log('Erreur : ' + msg.message, 'err');
      setProgress('Erreur — consultez le journal', 0, 'error');
      showError('Erreur de traitement', msg.message);
      document.getElementById('btn-transcribe').disabled = false;
      document.getElementById('btn-tts').disabled        = false;
      if (STATE.ttsReady) document.getElementById('btn-export').disabled = false;
      break;
  }
}


/* ═══════════════════════════════════════════════════════════
   SECTION 27 — POLLING HTTP
═══════════════════════════════════════════════════════════ */

function poll(jid) {
  if (STATE.pollInterval) clearInterval(STATE.pollInterval);
  STATE.pollInterval = setInterval(async () => {
    try {
      const job = await (await fetch(`/api/jobs/${jid}/`)).json();
      const s   = job.status;

      const labels = {
        extracting:   ['Extraction audio…',        30],
        transcribing: ['Transcription en cours…',  60],
        transcribed:  ['Transcription terminée',   100],
        synthesizing: ['Synthèse vocale en cours…', 70],
        done:         ['Traitement terminé',        100],
        error:        ['Erreur',                    0],
      };
      if (labels[s]) {
        const state = s === 'error' ? 'error' : (s === 'done' || s === 'transcribed' ? 'done' : 'active');
        setProgress(labels[s][0], labels[s][1], state);
      }

      if (s === 'transcribed' && !STATE.segments.length) {
        const raw = await (await fetch(`/api/jobs/${jid}/segments/`)).json();
        const sd  = Array.isArray(raw) ? raw : (raw.results || []);
        if (sd.length) {
          STATE.segments = sd.map(x => ({ id:x.id, index:x.index, start_ms:x.start_ms, end_ms:x.end_ms, start:x.start_ms, end:x.end_ms, text:x.text }));
          renderScript(STATE.segments); showScriptUI(); goToStep(3);
          document.getElementById('btn-tts').disabled        = false;
          document.getElementById('btn-transcribe').disabled = false;
          if (sd[0]) { document.getElementById('sub-preview-text').value = sd[0].text||''; drawSubPreview(); }
          setProgress('Transcription terminée', 100, 'done');
          log(`${sd.length} segments transcrits.`, 'ok');
          clearInterval(STATE.pollInterval); STATE.pollInterval = null;
        }
      }

      if (s === 'done') {
        clearInterval(STATE.pollInterval); STATE.pollInterval = null;
        document.getElementById('btn-transcribe').disabled = false;
        document.getElementById('btn-tts').disabled        = false;

        if (job.final_video_url && !STATE.isFinalVideo) {
          STATE.isFinalVideo = true; STATE.ttsReady = true;
          showFinalVideo(job.final_video_url);
          document.getElementById('btn-export').disabled = false;
          updateCallbackButtons(4);
          return;
        }
        // Récupérer le statut de la synthèse
        try {
          const planR = await fetch(`/api/jobs/${jid}/synthesis_status/`);
          if (planR.ok) {
            const plan = await planR.json();
            STATE.ttsOkCount = plan.nb_valides || 0;
            STATE.ttsTotal   = plan.nb_total   || STATE.segments.length;
            STATE.ttsEchecs  = plan.echecs_indices || [];
            STATE.ttsReady   = STATE.ttsOkCount > 0;
            if (STATE.ttsReady) {
              goToStep(4); document.getElementById('btn-export').disabled = false;
              updateCallbackButtons(3);
              setProgress('Voix off générée — assemblez la vidéo finale', 100, 'done');
              log(`Synthèse réussie : ${STATE.ttsOkCount}/${STATE.ttsTotal}.`, 'ok');
            } else {
              setProgress('Synthèse échouée', 0, 'error');
              showError('Synthèse échouée', 'Aucun segment valide. Relancez l\'étape 3.');
              document.getElementById('btn-export').disabled = true;
            }
          }
        } catch {
          STATE.ttsReady = true; goToStep(4);
          document.getElementById('btn-export').disabled = false;
          setProgress('Traitement terminé', 100, 'done');
        }
      }

      if (s === 'error') {
        clearInterval(STATE.pollInterval); STATE.pollInterval = null;
        log('Erreur : ' + (job.error_message||'inconnue'), 'err');
        showError('Erreur de traitement', job.error_message || 'Erreur inconnue');
        document.getElementById('btn-transcribe').disabled = false;
        document.getElementById('btn-tts').disabled        = false;
        if (STATE.ttsReady) document.getElementById('btn-export').disabled = false;
      }
    } catch (_) {}
  }, 2500);
}


/* ═══════════════════════════════════════════════════════════
   SECTION 28 — WAVEFORM
═══════════════════════════════════════════════════════════ */

function drawWave(data) {
  const c = document.getElementById('waveform-canvas');
  if (!c) return;
  const ctx = c.getContext('2d');
  const W = c.offsetWidth, H = c.offsetHeight;
  c.width = W; c.height = H;
  ctx.fillStyle = '#0D0D0D'; ctx.fillRect(0,0,W,H);
  if (!data || !data.length) return;
  const g = ctx.createLinearGradient(0,0,W,0);
  g.addColorStop(0,   'rgba(26,107,240,.5)');
  g.addColorStop(0.5, 'rgba(64,164,255,.65)');
  g.addColorStop(1,   'rgba(26,107,240,.5)');
  ctx.strokeStyle = g; ctx.lineWidth = 1;
  const step = W / data.length;
  for (let i = 0; i < data.length; i++) {
    const x = i * step, a = data[i] * (H/2);
    ctx.beginPath(); ctx.moveTo(x, H/2-a); ctx.lineTo(x, H/2+a); ctx.stroke();
  }
}

function drawHead() {
  if (!STATE.waveform.length) return;
  const c = document.getElementById('waveform-canvas');
  const v = document.getElementById('video-player');
  if (!c || !v || !v.duration) return;
  drawWave(STATE.waveform);
  const ctx = c.getContext('2d');
  const x   = (v.currentTime / v.duration) * c.width;
  ctx.strokeStyle = 'rgba(196,40,29,.7)'; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,c.height); ctx.stroke();
}


/* ═══════════════════════════════════════════════════════════
   SECTION 29 — TRANSPORT VIDÉO
═══════════════════════════════════════════════════════════ */

const vid = document.getElementById('video-player');
const sb  = document.getElementById('seek-bar');

function togglePlay() {
  if (!vid) return;
  const ico = document.getElementById('play-icon');
  if (vid.paused) {
    vid.play();
    if (ico) ico.innerHTML = '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>';
  } else {
    vid.pause();
    if (ico) ico.innerHTML = '<polygon points="5 3 19 12 5 21"/>';
  }
}

if (vid) {
  vid.addEventListener('timeupdate', () => {
    if (!vid.duration) return;
    const ms  = vid.currentTime * 1000;
    const ov  = document.getElementById('subtitle-overlay');
    if (sb) sb.value = (vid.currentTime / vid.duration) * 100;
    const td  = document.getElementById('time-display');
    if (td) td.textContent = `${msToTC(ms)} / ${msToTC(vid.duration*1000)}`;

    if (ov) {
      if (STATE.isFinalVideo) {
        ov.style.display = 'none';
      } else {
        const seg = STATE.segments.find(s =>
          !s._deleted &&
          ms >= (s.start_ms||s.start||0) &&
          ms <= (s.end_ms  ||s.end  ||0)
        );
        if (seg) { ov.textContent = seg.text; ov.style.display = 'block'; hlSeg(STATE.segments.indexOf(seg)); }
        else { ov.style.display = 'none'; }
      }
    }
    drawHead();
  });

  vid.addEventListener('loadedmetadata', () => {
    const el = document.getElementById('hud-res');
    if (el) { el.textContent = `${vid.videoWidth}×${vid.videoHeight}`; el.style.display = 'block'; }
  });

  vid.addEventListener('ended', () => {
    const ico = document.getElementById('play-icon');
    if (ico) ico.innerHTML = '<polygon points="5 3 19 12 5 21"/>';
  });
}

if (sb) sb.addEventListener('input', () => {
  if (vid && vid.duration) vid.currentTime = (sb.value / 100) * vid.duration;
});


/* ═══════════════════════════════════════════════════════════
   SECTION 30 — INITIALISATION
═══════════════════════════════════════════════════════════ */

window.addEventListener('load', async () => {
  buildVoiceGrids();

  const savedEngine = document.getElementById('current-job-tts').value || 'elevenlabs';
  document.getElementById('select-tts').value = savedEngine;
  onTtsEngineChange();
  selectVoice(document.getElementById('current-job-voice').value || 'narrateur_pro');
  initPresets();
  drawSubPreview();
  updateUndoRedoUI();

  if (STATE.jobId) {
    connectWS(STATE.jobId);

    try {
      const job = await (await fetch(`/api/jobs/${STATE.jobId}/`)).json();
      if (job.status) STATE.jobStatus = job.status;

      let videoUrl = document.getElementById('video-url').value;
      if (!videoUrl && job.video_file) {
        videoUrl = job.video_file.startsWith('http') ? job.video_file : `/media/${job.video_file}`;
      }

      let finalUrl = document.getElementById('final-url').value;
      if (!finalUrl && job.final_video_url) finalUrl = job.final_video_url;
      if (!finalUrl && job.status === 'done') {
        const candidate = `/media/exports/${STATE.jobId}/final.mp4`;
        try {
          const head = await fetch(candidate, { method: 'HEAD' });
          if (head.ok) finalUrl = candidate;
        } catch {}
      }

      if (finalUrl) {
        showFinalVideo(finalUrl);
        document.getElementById('upload-filename').textContent =
          job.video_filename || finalUrl.split('/').pop().split('?')[0];
      } else if (videoUrl) {
        if (vid) { vid.src = videoUrl + '?t=' + Date.now(); vid.style.display = 'block'; }
        const ph = document.getElementById('video-placeholder');
        if (ph) ph.style.display = 'none';
        document.getElementById('upload-filename').textContent =
          job.video_filename || videoUrl.split('/').pop();
        log(`Vidéo chargée : ${job.video_filename || 'source'}`, 'ok');
      }

    } catch (e) {
      const videoUrl = document.getElementById('video-url').value;
      const finalUrl = document.getElementById('final-url').value;
      if (finalUrl) showFinalVideo(finalUrl);
      else if (videoUrl && vid) {
        vid.src = videoUrl; vid.style.display = 'block';
        const ph = document.getElementById('video-placeholder');
        if (ph) ph.style.display = 'none';
      }
      log('Chargement via template (fallback)', 'warn');
    }

    resumeProject(STATE.jobId, STATE.jobStatus);
  }

  window.addEventListener('resize', () => { drawSubPreview(); drawWave(STATE.waveform); });

  // Avertissement avant de quitter si script non sauvegardé
  window.addEventListener('beforeunload', e => {
    if (STATE.scriptDirty) {
      e.preventDefault();
      e.returnValue = 'Vous avez des modifications non sauvegardées dans le script.';
      return e.returnValue;
    }
  });
});