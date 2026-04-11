"""
patch_cockpit_v2.py — Patch complet cockpit.html
=================================================

Corrections :
1. Responsive — panneau script adaptatif
2. Éditeur plein écran — grand champ texte unique
3. Annulation correcte — revenir à la bonne étape + vidéo originale
4. Reset sans retranscrire — annuler voix = rester sur étape 3 (voix)

Usage :
    python patch_cockpit_v2.py
"""

import re, sys
from pathlib import Path

POSSIBLE_PATHS = [
    Path("templates/studio/cockpit.html"),
    Path("apps/studio/templates/studio/cockpit.html"),
]

template_path = None
for p in POSSIBLE_PATHS:
    if p.exists():
        template_path = p
        break

if not template_path:
    print("❌ cockpit.html introuvable.")
    sys.exit(1)

print(f"✅ Template trouvé : {template_path}")
content = template_path.read_text(encoding="utf-8")

# ════════════════════════════════════════════════════════════════════════════
#  PATCH 1 — CSS responsive + éditeur plein écran amélioré
# ════════════════════════════════════════════════════════════════════════════

NEW_CSS = """
/* ── RESPONSIVE ── */
@media (max-width: 1100px) {
  .studio { grid-template-columns: 220px 1fr 260px; }
}
@media (max-width: 900px) {
  .studio { grid-template-columns: 1fr; grid-template-rows: auto 1fr auto; height:auto; }
  .panel-workflow { max-height: 300px; }
  .panel-script { max-height: 400px; }
}

/* ── SCRIPT TABS ── */
.script-tabs { display:flex; border-bottom:1px solid var(--border-1); flex-shrink:0; }
.script-tab { flex:1; padding:7px 0; font-size:10px; font-weight:600; color:var(--text-3); text-align:center; cursor:pointer; transition:var(--t); border-bottom:2px solid transparent; }
.script-tab.active { color:var(--text-1); border-bottom-color:var(--blue); }
.script-tab-panel { display:none; flex:1; overflow:hidden; flex-direction:column; }
.script-tab-panel.active { display:flex; }

/* ── ÉDITEUR TEXTE LIBRE ── */
#free-editor { flex:1; width:100%; background:transparent; border:none; color:var(--text-1); font-family:var(--font-ui); font-size:12px; line-height:1.8; padding:10px; outline:none; resize:none; }
#free-editor::placeholder { color:var(--text-3); }

/* ── MODAL PLEIN ÉCRAN ── */
#full-editor-modal { display:none; position:fixed; inset:0; z-index:500; background:rgba(0,0,0,.92); backdrop-filter:blur(12px); flex-direction:column; padding:28px; }
#full-editor-modal.open { display:flex; }
#full-editor-textarea { flex:1; width:100%; background:var(--bg-1); border:1px solid var(--border-1); border-radius:var(--r-lg); color:var(--text-1); font-family:var(--font-ui); font-size:13px; line-height:1.9; padding:20px; outline:none; resize:none; }
#full-editor-textarea:focus { border-color:var(--blue-border); }
"""

# Insérer le CSS avant {% endblock %} du bloc style
if "/* ── RESPONSIVE ── */" not in content:
    content = content.replace(
        "{% endblock %}\n\n{% block content %}",
        NEW_CSS + "\n{% endblock %}\n\n{% block content %}"
    )
    print("✅ PATCH 1 CSS responsive appliqué")
else:
    print("⚠️  PATCH 1 déjà appliqué")

# ════════════════════════════════════════════════════════════════════════════
#  PATCH 2 — HTML : panneau script avec tabs Segments / Texte libre
# ════════════════════════════════════════════════════════════════════════════

NEW_SCRIPT_PANEL = """  <!-- DROITE — SCRIPT + CONSOLE -->
  <aside class="panel-script">
    <div class="script-topbar">
      <div class="script-title">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
        Script<span class="seg-count" id="seg-count"></span>
      </div>
      <span class="script-stats" id="script-stats" style="display:none"></span>
      <span class="recalc-badge" id="recalc-badge">⏱ Recalculé</span>
      <div style="display:flex;gap:4px;margin-left:auto;align-items:center">
        <input type="file" id="import-script-input" accept=".txt" style="display:none" onchange="importScript(this)">
        <button class="script-action-btn" onclick="document.getElementById('import-script-input').click()" id="btn-import-script" style="display:none" title="Importer .txt">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Import
        </button>
        <button class="script-action-btn" onclick="exportScript()" id="btn-export-script" style="display:none" title="Exporter .txt">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
          Export
        </button>
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

    <!-- TABS : Segments / Texte libre -->
    <div class="script-tabs" id="script-tabs" style="display:none">
      <div class="script-tab active" onclick="switchScriptTab('segments', this)">📋 Segments</div>
      <div class="script-tab" onclick="switchScriptTab('free', this)">✏️ Texte libre</div>
    </div>

    <!-- TAB 1 : Éditeur segments -->
    <div class="script-tab-panel active" id="script-tab-segments">
      <div id="script-editor" style="flex:1;overflow-y:auto;padding:6px;display:flex;flex-direction:column;gap:1px">
        <div class="script-empty">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          <p>Le texte apparaîtra ici après la transcription</p>
          <p style="font-size:10px;margin-top:4px;">Corrigez-le avant de générer la voix</p>
        </div>
      </div>
    </div>

    <!-- TAB 2 : Texte libre -->
    <div class="script-tab-panel" id="script-tab-free">
      <textarea id="free-editor" placeholder="Modifiez le texte ici — un segment par paragraphe (ligne vide entre chaque)&#10;&#10;Cliquez sur 'Sauvegarder' quand vous avez terminé."></textarea>
      <div style="padding:6px 10px;border-top:1px solid var(--border-1);display:flex;justify-content:flex-end;gap:6px;flex-shrink:0">
        <button class="script-action-btn" onclick="syncFreeToSegments()" style="color:var(--amber);border-color:rgba(245,158,11,.3)">
          ↕ Synchroniser
        </button>
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
  </aside>

  <!-- MODAL ÉDITEUR PLEIN ÉCRAN -->
  <div id="full-editor-modal">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-shrink:0">
      <h2 style="font-size:16px;font-weight:700;color:var(--text-1)">✏️ Éditeur de script — plein écran</h2>
      <span style="font-size:11px;color:var(--text-3)">Un segment par paragraphe (ligne vide entre chaque)</span>
      <div style="margin-left:auto;display:flex;gap:8px">
        <button class="script-action-btn primary" onclick="applyFullEditor()">✅ Appliquer et fermer</button>
        <button class="script-action-btn" onclick="closeFullEditor()">✕ Annuler</button>
      </div>
    </div>
    <textarea id="full-editor-textarea" placeholder="Un segment par paragraphe...&#10;&#10;Exemple segment 1&#10;&#10;Exemple segment 2"></textarea>
  </div>"""

# Remplacer le panneau script existant
pattern = r'  <!-- DROITE — SCRIPT \+ CONSOLE -->.*?</div>\s*\n\n<input'
if re.search(pattern, content, re.DOTALL):
    content = re.sub(
        pattern,
        NEW_SCRIPT_PANEL + '\n\n<input',
        content, flags=re.DOTALL
    )
    print("✅ PATCH 2 HTML panneau script appliqué")
else:
    print("⚠️  PATCH 2 : panneau script non trouvé, vérifiez manuellement")

# ════════════════════════════════════════════════════════════════════════════
#  PATCH 3 — JS : nouvelles fonctions
# ════════════════════════════════════════════════════════════════════════════

NEW_JS = """
// ═══════════════════════════════════════════════════════
//  SCRIPT TABS — Segments / Texte libre
// ═══════════════════════════════════════════════════════
function switchScriptTab(name, el) {
  document.querySelectorAll('.script-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.script-tab-panel').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('script-tab-' + name).classList.add('active');

  if (name === 'free') {
    // Synchroniser les segments vers le texte libre
    syncSegmentsToFree();
  }
}

function syncSegmentsToFree() {
  const ta = document.getElementById('free-editor');
  ta.value = STATE.segments.map(s => s.text || '').join('\n\n');
}

function syncFreeToSegments() {
  const ta = document.getElementById('free-editor');
  const blocks = ta.value.split(/\n\n+/).filter(b => b.trim());
  if (blocks.length === 0) return;

  blocks.forEach((block, i) => {
    if (STATE.segments[i]) {
      STATE.segments[i].text = block.trim();
    }
  });

  // Mettre à jour l'onglet segments
  const segTab = document.querySelector('.script-tab');
  renderScript(STATE.segments);
  log(`✅ ${blocks.length} segments synchronisés depuis le texte libre`, 'info');
}

// ═══════════════════════════════════════════════════════
//  EXPORT / IMPORT SCRIPT
// ═══════════════════════════════════════════════════════
function exportScript() {
  if (!STATE.segments.length) return;
  const lines = STATE.segments.map((s, i) =>
    `[${i+1}] ${msToTC(s.start_ms||s.start||0)}\\n${s.text}`
  ).join('\\n\\n');
  const blob = new Blob([lines], {type: 'text/plain;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `script_${(STATE.jobId||'export').slice(0,8)}.txt`;
  a.click();
  log('📥 Script exporté en .txt', 'info');
}

function importScript(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    const text = e.target.result;
    const blocks = text.split(/\\n\\n+/);
    let idx = 0;
    blocks.forEach(block => {
      const lines = block.trim().split('\\n');
      const content = lines.filter(l => !l.match(/^\\[\\d+\\]/)).join(' ').trim();
      if (content && STATE.segments[idx]) {
        STATE.segments[idx].text = content;
        idx++;
      }
    });
    renderScript(STATE.segments);
    syncSegmentsToFree();
    log(`📤 Script importé — ${idx} segments mis à jour`, 'info');
    input.value = '';
  };
  reader.readAsText(file);
}

// ═══════════════════════════════════════════════════════
//  ÉDITEUR PLEIN ÉCRAN
// ═══════════════════════════════════════════════════════
function openFullEditor() {
  const modal = document.getElementById('full-editor-modal');
  const ta = document.getElementById('full-editor-textarea');
  ta.value = STATE.segments.map(s => s.text || '').join('\\n\\n');
  modal.classList.add('open');
  ta.focus();
}

function closeFullEditor() {
  document.getElementById('full-editor-modal').classList.remove('open');
}

function applyFullEditor() {
  const ta = document.getElementById('full-editor-textarea');
  const blocks = ta.value.split(/\\n\\n+/).filter(b => b.trim());
  blocks.forEach((block, i) => {
    if (STATE.segments[i]) {
      STATE.segments[i].text = block.trim();
    }
  });
  renderScript(STATE.segments);
  syncSegmentsToFree();
  closeFullEditor();
  log(`✅ ${blocks.length} segments mis à jour depuis l'éditeur plein écran`, 'info');
}

// ═══════════════════════════════════════════════════════
//  RETOUR EN ARRIÈRE — version corrigée
// ═══════════════════════════════════════════════════════
async function resetToStep(step) {
  if (!STATE.jobId) return;

  const msgs = {
    2: 'Annuler la voix et revenir à l\\'étape "Générer la voix" ?\\n(La transcription est conservée)',
    3: 'Annuler l\\'export et revenir à l\\'étape "Assembler" ?\\n(La voix est conservée)',
    1: 'Recommencer depuis le début ?\\n(Toutes les données seront supprimées)',
  };

  if (!confirm(msgs[step] || 'Confirmer ?')) return;

  try {
    const r = await apiPost(`/api/jobs/${STATE.jobId}/reset/`, {step});
    if (r.status === 'ok') {
      log(`↩️ Retour à l'étape ${step}`, 'info');

      // Revenir à la vidéo originale si on annule export ou voix
      if (step <= 3) {
        const videoUrl = document.getElementById('video-url').value;
        if (videoUrl) {
          STATE.isFinalVideo = false;
          const v = document.getElementById('video-player');
          v.src = videoUrl;
          document.getElementById('hud-mode').textContent = 'APERÇU SOURCE';
          document.getElementById('hud-mode').style.color = '';
          document.getElementById('btn-download').style.display = 'none';
        }
      }

      if (step === 2) {
        // Annuler la voix → rester sur étape 3 (générer la voix)
        // La transcription est déjà faite, pas besoin de retranscrire !
        goToStep(3);
        document.getElementById('btn-tts').disabled = false;
        document.getElementById('btn-reset-to-transcribed').style.display = 'none';
        setProgress('Transcription disponible — générez la voix', 100, 'done');
        log('✅ Voix annulée — vous pouvez regénérer la voix off', 'info');

      } else if (step === 3) {
        // Annuler l'export → rester sur étape 4 (assembler)
        goToStep(4);
        document.getElementById('btn-export').disabled = false;
        document.getElementById('btn-reset-to-tts').style.display = 'none';
        setProgress('Voix disponible — relancez l\\'assemblage', 100, 'done');
        log('✅ Export annulé — vous pouvez relancer l\\'assemblage', 'info');

      } else if (step === 1) {
        // Retour au début
        goToStep(1);
        STATE.segments = [];
        renderScript([]);
        setProgress('Prêt — importez une nouvelle vidéo', 0, 'active');
      }
    } else {
      log('❌ Erreur reset : ' + JSON.stringify(r), 'err');
    }
  } catch(e) {
    log('❌ Erreur reset : ' + e.message, 'err');
  }
}

// ═══════════════════════════════════════════════════════
//  AFFICHER LES BOUTONS SCRIPT (import/export/tabs)
// ═══════════════════════════════════════════════════════
function showScriptButtons() {
  const ids = ['btn-import-script', 'btn-export-script', 'btn-save-script'];
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'flex';
  });
  // Afficher les tabs
  const tabs = document.getElementById('script-tabs');
  if (tabs) tabs.style.display = 'flex';
}
"""

# Chercher où insérer — avant TRANSPORT
if "//  EXPORT / IMPORT SCRIPT" not in content:
    content = content.replace(
        "// ═══════════════════════════════════════════════════════\n//  TRANSPORT",
        NEW_JS + "\n// ═══════════════════════════════════════════════════════\n//  TRANSPORT"
    )
    print("✅ PATCH 3 JS nouvelles fonctions appliqué")
else:
    print("⚠️  PATCH 3 déjà appliqué")

# ════════════════════════════════════════════════════════════════════════════
#  PATCH 4 — JS : corriger resumeProject pour afficher les boutons et tabs
# ════════════════════════════════════════════════════════════════════════════

OLD_RESUME = """      document.getElementById('btn-export-script').style.display = 'flex';
      document.getElementById('btn-import-script').style.display = 'flex';
      document.getElementById('btn-full-editor').style.display = 'flex';
      // Bouton retour selon l'étape
      if (jobStatus === 'done' || jobStatus === 'synthesizing') {
        document.getElementById('btn-reset-to-transcribed').style.display = 'flex';
      }
      document.getElementById('seg-count').textContent = `— ${STATE.segments.length} segments`;
      document.getElementById('btn-save-script').style.display = 'flex';"""

NEW_RESUME = """      showScriptButtons();
      // Bouton retour selon l'étape
      if (jobStatus === 'done' || jobStatus === 'synthesizing') {
        const rb = document.getElementById('btn-reset-to-transcribed');
        if (rb) rb.style.display = 'flex';
      }
      document.getElementById('seg-count').textContent = `— ${STATE.segments.length} segments`;
      syncSegmentsToFree();"""

if OLD_RESUME in content:
    content = content.replace(OLD_RESUME, NEW_RESUME)
    print("✅ PATCH 4 resumeProject corrigé")
else:
    print("⚠️  PATCH 4 : bloc resumeProject non trouvé")

# ════════════════════════════════════════════════════════════════════════════
#  PATCH 5 — JS : corriger timeupdate (bug ov non défini)
# ════════════════════════════════════════════════════════════════════════════

OLD_TIMEUPDATE = """vid.addEventListener('timeupdate',()=>{
  if (STATE.isFinalVideo) {
      ov.style.display = 'none'; // ← cache l'overlay HTML
  }
  if(!vid.duration)return;"""

NEW_TIMEUPDATE = """vid.addEventListener('timeupdate',()=>{
  if(!vid.duration)return;
  const ov=document.getElementById('subtitle-overlay');
  if (STATE.isFinalVideo) {
      ov.style.display = 'none';
      sb.value=(vid.currentTime/vid.duration)*100;
      document.getElementById('time-display').textContent=`${msToTC(vid.currentTime*1000)} / ${msToTC(vid.duration*1000)}`;
      drawHead();
      return;
  }"""

if OLD_TIMEUPDATE in content:
    # Aussi corriger la ligne ov= dupliquée plus bas
    content = content.replace(OLD_TIMEUPDATE, NEW_TIMEUPDATE)
    # Supprimer la ligne const ov= dupliquée
    content = content.replace(
        """  document.getElementById('time-display').textContent=`${msToTC(vid.currentTime*1000)} / ${msToTC(vid.duration*1000)}`;
  const ov=document.getElementById('subtitle-overlay');
  if(STATE.isFinalVideo){ov.style.display='none';}
  else{""",
        """  document.getElementById('time-display').textContent=`${msToTC(vid.currentTime*1000)} / ${msToTC(vid.duration*1000)}`;
  {"""
    )
    print("✅ PATCH 5 timeupdate corrigé")
else:
    print("⚠️  PATCH 5 : timeupdate non trouvé")

# ════════════════════════════════════════════════════════════════════════════
#  PATCH 6 — JS : afficher tabs après transcription (onWS segments)
# ════════════════════════════════════════════════════════════════════════════

OLD_WS_SEGS = """      STATE.segments=msg.data; renderScript(msg.data);
      setProgress('Transcription terminée — corrigez puis sauvegardez',100,'done');
      goToStep(3);
      document.getElementById('btn-tts').disabled=false;
      document.getElementById('btn-save-script').style.display='flex';"""

NEW_WS_SEGS = """      STATE.segments=msg.data; renderScript(msg.data);
      syncSegmentsToFree();
      showScriptButtons();
      setProgress('Transcription terminée — corrigez puis sauvegardez',100,'done');
      goToStep(3);
      document.getElementById('btn-tts').disabled=false;"""

if OLD_WS_SEGS in content:
    content = content.replace(OLD_WS_SEGS, NEW_WS_SEGS)
    print("✅ PATCH 6 onWS segments corrigé")
else:
    print("⚠️  PATCH 6 : bloc onWS segments non trouvé")

# ════════════════════════════════════════════════════════════════════════════
#  PATCH 7 — JS : afficher tabs après polling (poll transcribed)
# ════════════════════════════════════════════════════════════════════════════

OLD_POLL = """          renderScript(STATE.segments); goToStep(3);
          document.getElementById('btn-tts').disabled=false;
          document.getElementById('btn-transcribe').disabled=false;
          document.getElementById('btn-save-script').style.display='flex';"""

NEW_POLL = """          renderScript(STATE.segments);
          syncSegmentsToFree();
          showScriptButtons();
          goToStep(3);
          document.getElementById('btn-tts').disabled=false;
          document.getElementById('btn-transcribe').disabled=false;"""

if OLD_POLL in content:
    content = content.replace(OLD_POLL, NEW_POLL)
    print("✅ PATCH 7 poll corrigé")
else:
    print("⚠️  PATCH 7 : bloc poll non trouvé")

# ════════════════════════════════════════════════════════════════════════════
#  Sauvegarder
# ════════════════════════════════════════════════════════════════════════════
backup = template_path.with_suffix('.html.bak2')
backup.write_text(template_path.read_text(encoding='utf-8'), encoding='utf-8')
print(f"💾 Backup : {backup}")

template_path.write_text(content, encoding='utf-8')
print(f"✅ cockpit.html mis à jour !")
print()
print("═══════════════════════════════════════════════")
print("RAPPEL : ajoutez aussi dans apps/api/views.py")
print("═══════════════════════════════════════════════")
print("""
@action(detail=True, methods=["post"], url_path="reset")
def reset(self, request, pk=None):
    from pathlib import Path
    from django.conf import settings
    import shutil

    job  = self.get_object()
    step = int(request.data.get("step", 2))

    if step == 2:
        # Annuler la voix → revenir à TRANSCRIBED
        job.set_status(Job.Status.TRANSCRIBED)
        # Supprimer les fichiers audio TTS
        tts_dir = job.output_dir / "tts"
        if tts_dir.exists():
            shutil.rmtree(str(tts_dir))
        # Supprimer le plan de synthèse
        plan = job.output_dir / "synthesis_plan.json"
        if plan.exists():
            plan.unlink()

    elif step == 3:
        # Annuler l'export → revenir à DONE (voix ok)
        job.set_status(Job.Status.DONE)
        # Supprimer la vidéo finale
        exports_dir = Path(settings.MEDIA_ROOT) / "exports" / str(job.pk)
        if exports_dir.exists():
            shutil.rmtree(str(exports_dir))
        # Supprimer les fichiers assemblés
        for f in ["assembled.mp4", "composite.wav", "subtitles.ass"]:
            fp = job.output_dir / f
            if fp.exists():
                fp.unlink()

    elif step == 1:
        # Retour au début
        job.set_status(Job.Status.PENDING)

    return Response({"status": "ok", "new_status": job.status})
""")