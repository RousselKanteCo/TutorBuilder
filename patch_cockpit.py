"""
patch_cockpit.py — Script pour appliquer automatiquement les améliorations
au template cockpit.html

Usage :
    python patch_cockpit.py

Ce script modifie directement votre fichier cockpit.html.
"""

import re
import sys
from pathlib import Path

# ── Chercher le template ──────────────────────────────────────────────────────
POSSIBLE_PATHS = [
    Path("apps/studio/templates/studio/cockpit.html"),
    Path("templates/studio/cockpit.html"),
    Path("studio/templates/studio/cockpit.html"),
]

template_path = None
for p in POSSIBLE_PATHS:
    if p.exists():
        template_path = p
        break

if not template_path:
    print("❌ cockpit.html introuvable. Cherchez-le manuellement et ajustez POSSIBLE_PATHS.")
    sys.exit(1)

print(f"✅ Template trouvé : {template_path}")
content = template_path.read_text(encoding="utf-8")

# ════════════════════════════════════════════════════════════════════════════
#  PATCH 1 — CSS : ajouter les styles du script editor v2
# ════════════════════════════════════════════════════════════════════════════

NEW_CSS = """
/* ── SCRIPT EDITOR v2 ── */
.seg-row{display:flex;gap:0;border-radius:var(--r-sm);border:1px solid transparent;transition:var(--t);position:relative;background:transparent}
.seg-row:hover{background:var(--bg-2);border-color:var(--border-1)}
.seg-row.active{background:rgba(59,130,246,.06);border-color:var(--blue-border);box-shadow:inset 2px 0 0 var(--blue)}
.seg-num{font-family:var(--font-mono);font-size:9px;color:var(--text-3);min-width:20px;padding:8px 2px 8px 8px;flex-shrink:0;user-select:none}
.seg-row.active .seg-num{color:var(--blue)}
.seg-tc{font-family:var(--font-mono);font-size:9px;color:var(--text-3);min-width:36px;flex-shrink:0;padding:8px 4px;cursor:pointer;transition:var(--t)}
.seg-tc:hover{color:var(--blue)}.seg-row.active .seg-tc{color:var(--blue)}
.seg-body{flex:1;display:flex;flex-direction:column;padding:6px 4px;min-width:0}
.seg-txt{font-size:12px;color:var(--text-2);outline:none;line-height:1.55;cursor:text;word-break:break-word;min-height:18px}
.seg-row.active .seg-txt,.seg-txt:focus{color:var(--text-1)}
.seg-len-bar{height:2px;border-radius:1px;margin-top:4px;transition:width .3s,background .3s;background:var(--green);width:0%}
.seg-len-bar.warn{background:var(--amber)}.seg-len-bar.over{background:var(--red)}
.seg-len-label{font-size:8px;font-family:var(--font-mono);color:var(--text-3);margin-top:2px;display:none}
.seg-row:hover .seg-len-label,.seg-row.active .seg-len-label{display:block}
.seg-len-label.warn{color:var(--amber)}.seg-len-label.over{color:var(--red)}
.seg-actions{display:flex;flex-direction:column;gap:2px;padding:4px 4px 4px 2px;opacity:0;transition:opacity .15s;flex-shrink:0}
.seg-row:hover .seg-actions,.seg-row.active .seg-actions{opacity:1}
.seg-btn{width:20px;height:20px;border-radius:4px;border:1px solid var(--border-1);background:var(--bg-3);color:var(--text-3);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:11px;transition:var(--t);flex-shrink:0;font-family:var(--font-ui)}
.seg-btn:hover{border-color:var(--border-2);color:var(--text-1);background:var(--bg-2)}
.seg-btn.del:hover{border-color:rgba(239,68,68,.4);color:var(--red);background:rgba(239,68,68,.08)}
.seg-btn.merge-up:hover{border-color:rgba(59,130,246,.4);color:var(--blue);background:var(--blue-dim)}
.seg-merge-hint{height:12px;margin:0 8px;display:flex;align-items:center;justify-content:center;cursor:pointer;opacity:0;transition:opacity .15s}
.seg-merge-hint:hover{opacity:1}
.seg-merge-line{flex:1;height:1px;background:var(--border-1)}
.seg-merge-label{font-size:8px;color:var(--text-3);padding:0 6px;white-space:nowrap;transition:color .15s}
.seg-merge-hint:hover .seg-merge-label{color:var(--blue)}
.seg-row:hover + .seg-merge-hint,.seg-merge-hint:hover{opacity:1}
.script-stats{font-size:9px;color:var(--text-3);font-family:var(--font-mono);padding:2px 8px;background:var(--bg-2);border-radius:10px;border:1px solid var(--border-1)}
.script-action-btn{display:flex;align-items:center;gap:4px;padding:4px 9px;border-radius:var(--r-sm);border:1px solid var(--border-1);background:var(--bg-2);color:var(--text-3);font-size:10px;font-weight:500;font-family:var(--font-ui);cursor:pointer;transition:var(--t)}
.script-action-btn:hover{border-color:var(--border-2);color:var(--text-2)}
.script-action-btn.primary{background:var(--blue-dim);border-color:var(--blue-border);color:#93C5FD}
.script-action-btn.primary:hover{background:rgba(59,130,246,.18)}
.script-action-btn:disabled{opacity:.4;cursor:not-allowed}
.script-action-btn svg{width:10px;height:10px}
"""

# Insérer avant la fermeture du bloc style
if "/* ── SCRIPT EDITOR v2 ── */" not in content:
    content = content.replace("{% endblock %}\n\n{% block content %}", NEW_CSS + "\n{% endblock %}\n\n{% block content %}")
    print("✅ PATCH 1 CSS appliqué")
else:
    print("⚠️  PATCH 1 déjà appliqué")

# ════════════════════════════════════════════════════════════════════════════
#  PATCH 2 — HTML : remplacer l'aside panel-script
# ════════════════════════════════════════════════════════════════════════════

NEW_ASIDE = """  <!-- DROITE — SCRIPT + CONSOLE -->
  <aside class="panel-script">
    <div class="script-topbar">
      <div class="script-title">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
        Script<span class="seg-count" id="seg-count"></span>
      </div>
      <span class="script-stats" id="script-stats" style="display:none"></span>
      <span class="recalc-badge" id="recalc-badge">⏱ Recalculé</span>
      <div style="display:flex;gap:4px;margin-left:auto;align-items:center">
        <button class="script-action-btn" onclick="autoSplitLong()" id="btn-auto-split" style="display:none" title="Découper les segments trop longs">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="17" y1="10" x2="3" y2="10"/><line x1="21" y1="6" x2="3" y2="6"/><line x1="21" y1="14" x2="3" y2="14"/><line x1="17" y1="18" x2="3" y2="18"/></svg>
          Découper
        </button>
        <button class="script-action-btn primary" onclick="saveScript()" id="btn-save-script" style="display:none">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
          Sauvegarder
        </button>
      </div>
    </div>
    <div id="script-editor">
      <div class="script-empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        <p>Le texte apparaîtra ici après la transcription</p>
        <p style="font-size:10px;margin-top:4px;">Corrigez-le avant de générer la voix</p>
      </div>
    </div>
    <div class="console-wrap">
      <div class="console-head">
        <span class="ws-dot" id="ws-dot"></span>
        <span id="ws-status" style="font-size:10px;color:var(--text-3);">Non connecté</span>
        <span class="console-label" style="margin-left:8px;">Journal</span>
        <button class="console-clear" onclick="clearLog()">Effacer</button>
      </div>
      <div id="log-monitor">Prêt. Importez une vidéo pour commencer.
</div>
    </div>
  </aside>"""

# Remplacer l'aside existant
pattern = r'  <!-- DROITE — SCRIPT \+ CONSOLE -->.*?</aside>'
if re.search(pattern, content, re.DOTALL):
    content = re.sub(pattern, NEW_ASIDE, content, flags=re.DOTALL)
    print("✅ PATCH 2 HTML appliqué")
else:
    print("⚠️  PATCH 2 : aside non trouvé, vérifiez manuellement")

# ════════════════════════════════════════════════════════════════════════════
#  PATCH 3 — JS : remplacer les fonctions renderScript, hl, saveScript
# ════════════════════════════════════════════════════════════════════════════

NEW_JS_FUNCTIONS = """
// ═══════════════════════════════════════════════════════
//  SCRIPT EDITOR v2 — suppression, fusion, longueur
// ═══════════════════════════════════════════════════════

const MAX_SEG_CHARS = 120;
const MAX_SEG_HARD  = 200;

function renderScript(segs) {
  const ed = document.getElementById('script-editor');
  ed.innerHTML = '';
  const totalChars = segs.reduce((a,s) => a + (s.text||'').length, 0);
  const longSegs   = segs.filter(s => (s.text||'').length > MAX_SEG_CHARS).length;
  document.getElementById('seg-count').textContent = `— ${segs.length} segments`;
  const statsEl = document.getElementById('script-stats');
  if (statsEl) {
    statsEl.style.display = segs.length ? '' : 'none';
    statsEl.textContent = `${totalChars} car. · ${longSegs > 0 ? '⚠️ '+longSegs+' longs' : '✅ ok'}`;
    statsEl.style.color = longSegs > 0 ? 'var(--amber)' : 'var(--text-3)';
  }
  const autoBtn = document.getElementById('btn-auto-split');
  if (autoBtn) autoBtn.style.display = longSegs > 0 ? 'flex' : 'none';

  if (!segs.length) {
    ed.innerHTML = `<div class="script-empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
      <p>Le texte apparaîtra ici après la transcription</p>
      <p style="font-size:10px;margin-top:4px;">Corrigez-le avant de générer la voix</p>
    </div>`;
    return;
  }

  segs.forEach((seg, i) => {
    const text   = seg.text || '';
    const chars  = text.length;
    const pct    = Math.min(100, (chars / MAX_SEG_HARD) * 100);
    const isWarn = chars > MAX_SEG_CHARS;
    const isOver = chars > MAX_SEG_HARD;
    const bc     = isOver ? ' over' : isWarn ? ' warn' : '';
    const lc     = isOver ? ' over' : isWarn ? ' warn' : '';

    const row = document.createElement('div');
    row.className   = 'seg-row';
    row.dataset.index = i;
    row.innerHTML = `
      <span class="seg-num">${i+1}</span>
      <span class="seg-tc" title="Aller à ${msToTC(seg.start||seg.start_ms||0)}">${msToTC(seg.start||seg.start_ms||0)}</span>
      <div class="seg-body">
        <div class="seg-txt" contenteditable="true" data-index="${i}" spellcheck="true" oninput="onSegInput(this,${i})">${text}</div>
        <div class="seg-len-bar${bc}" style="width:${pct}%"></div>
        <div class="seg-len-label${lc}">${chars} car.${isOver?' — trop long !':isWarn?' — long':''}</div>
      </div>
      <div class="seg-actions">
        ${i>0?`<button class="seg-btn merge-up" title="Fusionner avec le précédent" onclick="mergeWithPrev(${i})">↑</button>`:'<div style="width:20px"></div>'}
        <button class="seg-btn del" title="Supprimer ce segment" onclick="deleteSegment(${i})">×</button>
      </div>`;

    row.querySelector('.seg-tc').addEventListener('click', e => {
      e.stopPropagation();
      document.getElementById('video-player').currentTime = (seg.start||seg.start_ms||0)/1000;
      hl(i);
      document.getElementById('sub-preview-text').value = text;
      drawSubPreview();
    });
    row.addEventListener('click', () => {
      hl(i);
      document.getElementById('sub-preview-text').value = seg.text||'';
      drawSubPreview();
    });
    ed.appendChild(row);

    if (i < segs.length - 1) {
      const sep = document.createElement('div');
      sep.className = 'seg-merge-hint';
      sep.title     = 'Fusionner ces deux segments';
      sep.innerHTML = '<div class="seg-merge-line"></div><span class="seg-merge-label">fusionner</span><div class="seg-merge-line"></div>';
      sep.addEventListener('click', () => mergeSegments(i, i+1));
      ed.appendChild(sep);
    }
  });
}

function hl(idx) {
  document.querySelectorAll('.seg-row').forEach(r => r.classList.remove('active'));
  const r = document.querySelector(`.seg-row[data-index="${idx}"]`);
  if (r) { r.classList.add('active'); r.scrollIntoView({block:'nearest',behavior:'smooth'}); }
}

function onSegInput(el, idx) {
  const chars  = el.textContent.length;
  const pct    = Math.min(100, (chars / MAX_SEG_HARD) * 100);
  const isWarn = chars > MAX_SEG_CHARS;
  const isOver = chars > MAX_SEG_HARD;
  const row    = el.closest('.seg-row');
  const bar    = row.querySelector('.seg-len-bar');
  bar.style.width = pct + '%';
  bar.className   = 'seg-len-bar' + (isOver?' over':isWarn?' warn':'');
  const lbl = row.querySelector('.seg-len-label');
  lbl.textContent = `${chars} car.${isOver?' — trop long !':isWarn?' — long':''}`;
  lbl.className   = 'seg-len-label' + (isOver?' over':isWarn?' warn':'');
  if (STATE.segments[idx]) STATE.segments[idx].text = el.textContent.trim();
}

function deleteSegment(idx) {
  if (STATE.segments.length <= 1) { log('⚠️ Impossible de supprimer le dernier segment.','warn'); return; }
  if (!confirm(`Supprimer le segment ${idx+1} ?`)) return;
  STATE.segments.splice(idx, 1);
  STATE.segments.forEach((s,i) => s.index = i);
  renderScript(STATE.segments);
  log(`🗑️ Segment ${idx+1} supprimé.`,'info');
}

function mergeWithPrev(idx) { if (idx > 0) mergeSegments(idx-1, idx); }

function mergeSegments(idxA, idxB) {
  if (idxB >= STATE.segments.length) return;
  const segA = STATE.segments[idxA];
  const segB = STATE.segments[idxB];
  const merged = {
    ...segA,
    text:     ((segA.text||'').trim() + ' ' + (segB.text||'').trim()).trim(),
    end_ms:   segB.end_ms||segB.end||segA.end_ms,
    end:      segB.end_ms||segB.end||segA.end_ms,
  };
  STATE.segments.splice(idxA, 2, merged);
  STATE.segments.forEach((s,i) => s.index = i);
  renderScript(STATE.segments);
  log(`🔗 Segments ${idxA+1} et ${idxB+1} fusionnés.`,'info');
}

function autoSplitLong() {
  let count = 0;
  const newSegs = [];
  STATE.segments.forEach(seg => {
    const text = (seg.text||'').trim();
    if (text.length <= MAX_SEG_CHARS) { newSegs.push(seg); return; }
    const parts = smartSplit(text, MAX_SEG_CHARS);
    if (parts.length <= 1) { newSegs.push(seg); return; }
    const dur   = ((seg.end_ms||seg.end||0) - (seg.start_ms||seg.start||0));
    const total = parts.reduce((a,p) => a+p.length, 0) || 1;
    let cursor  = seg.start_ms||seg.start||0;
    parts.forEach((part, pi) => {
      const ratio  = part.length / total;
      const subDur = Math.max(200, Math.round(dur * ratio));
      const subEnd = pi===parts.length-1 ? (seg.end_ms||seg.end||0) : cursor+subDur;
      newSegs.push({...seg, text:part, start_ms:cursor, start:cursor, end_ms:subEnd, end:subEnd});
      cursor = subEnd; count++;
    });
    count--;
  });
  if (count > 0) {
    STATE.segments = newSegs;
    STATE.segments.forEach((s,i) => s.index = i);
    renderScript(STATE.segments);
    log(`✂️ ${count} segments découpés automatiquement.`,'info');
  } else {
    log('✅ Aucun segment trop long.','info');
  }
}

function smartSplit(text, maxChars) {
  if (text.length <= maxChars) return [text];
  const sentences = text.split(/(?<=[.!?])\\s+/);
  const chunks = []; let current = '';
  for (const sent of sentences) {
    const test = current ? current+' '+sent : sent;
    if (test.length <= maxChars) { current = test; }
    else { if (current) chunks.push(current); current = sent; }
  }
  if (current) chunks.push(current);
  const result = [];
  for (const chunk of chunks) {
    if (chunk.length <= maxChars) { result.push(chunk); continue; }
    const parts2 = chunk.split(/(?<=,)\\s+/);
    let cur2 = '';
    for (const p of parts2) {
      const t = cur2 ? cur2+' '+p : p;
      if (t.length <= maxChars) { cur2 = t; }
      else { if (cur2) result.push(cur2); cur2 = p; }
    }
    if (cur2) result.push(cur2);
  }
  return result.filter(Boolean);
}

function showRecalcBadge(){const b=document.getElementById('recalc-badge');b.classList.add('show');setTimeout(()=>b.classList.remove('show'),3000);}

async function saveScript() {
  if (!STATE.jobId) return;
  const btn = document.getElementById('btn-save-script');
  btn.disabled = true;
  const editedSegments = [];
  document.querySelectorAll('.seg-txt').forEach(el => {
    const idx = parseInt(el.dataset.index);
    const seg = STATE.segments[idx];
    if (!seg) return;
    editedSegments.push({
      id:       seg.id,
      index:    seg.index!==undefined ? seg.index : idx,
      start_ms: seg.start_ms||seg.start||0,
      end_ms:   seg.end_ms||seg.end||0,
      text:     el.textContent.trim(),
    });
  });
  log(`💾 Sauvegarde de ${editedSegments.length} segments…`,'info');
  try {
    const res = await apiPost(`/api/jobs/${STATE.jobId}/split_segments/`, {segments: editedSegments});
    if (res.segments && res.segments.length) {
      STATE.segments = res.segments.map(s => ({
        id:s.id, index:s.index, start:s.start_ms, end:s.end_ms,
        start_ms:s.start_ms, end_ms:s.end_ms, text:s.text,
      }));
      renderScript(STATE.segments);
      document.getElementById('seg-count').textContent = `— ${STATE.segments.length} segments`;
      const delta = res.new_count - res.original_count;
      log(delta>0 ? `✅ ${res.original_count} → ${res.new_count} segments.` : `✅ ${res.new_count} segments sauvegardés.`,'info');
      showRecalcBadge();
      document.querySelectorAll('.seg-row').forEach(r=>{r.classList.add('tc-updated');setTimeout(()=>r.classList.remove('tc-updated'),2000)});
    } else { log('⚠️ '+JSON.stringify(res),'warn'); }
  } catch(e) { log('❌ '+e.message,'err'); }
  btn.disabled = false;
}
"""

# Chercher et remplacer le bloc renderScript...saveScript dans le JS
pattern_js = r'// ═+\n//  SCRIPT EDITOR.*?function showRecalcBadge\(\)\{.*?\}'
if re.search(pattern_js, content, re.DOTALL):
    content = re.sub(pattern_js, NEW_JS_FUNCTIONS.strip(), content, flags=re.DOTALL)
    print("✅ PATCH 3 JS appliqué (renderScript trouvé et remplacé)")
else:
    # Fallback : chercher juste function renderScript
    pattern_js2 = r'function renderScript\(segs\)\{.*?function showRecalcBadge\(\)\{.*?\}'
    if re.search(pattern_js2, content, re.DOTALL):
        content = re.sub(pattern_js2, NEW_JS_FUNCTIONS.strip(), content, flags=re.DOTALL)
        print("✅ PATCH 3 JS appliqué (fallback)")
    else:
        print("⚠️  PATCH 3 : fonctions JS non trouvées automatiquement")
        print("   → Ajoutez manuellement le contenu de script_editor_v2.js")

# ════════════════════════════════════════════════════════════════════════════
#  Sauvegarder
# ════════════════════════════════════════════════════════════════════════════

# Backup
backup = template_path.with_suffix('.html.bak')
backup.write_text(template_path.read_text(encoding='utf-8'), encoding='utf-8')
print(f"💾 Backup créé : {backup}")

template_path.write_text(content, encoding='utf-8')
print(f"✅ Template mis à jour : {template_path}")
print("\nRedémarrez le serveur Django pour voir les changements.")