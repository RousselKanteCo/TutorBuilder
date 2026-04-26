/**
 * export.js — Module export vidéo finale
 */
'use strict';

const POLL_EXPORT_INTERVAL = 3000;
let _exportPollTimer = null;
let _originalVideoUrl = '';
let _finalVideoUrl    = '';
let _vttUrl           = '';
let _showingFinal     = false;

/* ═══════════════════════════════════════════════════
   LANCER L'EXPORT
═══════════════════════════════════════════════════ */

async function startExport() {
  const jobId = document.getElementById('current-job-id')?.value;
  if (!jobId) { window.Toast?.error('Aucun job actif.'); return; }

  const csrf  = document.getElementById('csrf-token')?.value || '';
  const btn   = document.getElementById('btn-export');
  const errEl = document.getElementById('export-error');

  if (btn) btn.disabled = true;
  if (errEl) errEl.textContent = '';
  document.getElementById('export-progress-wrap').style.display = 'block';
  setExportProgress(5, 'Démarrage…');
  window.Toast?.info('Assemblage vidéo lancé…');

  try {
    const res  = await fetch(`/api/jobs/${jobId}/export/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({}),
    });
    const data = await res.json();

    if (!res.ok) {
      if (btn) btn.disabled = false;
      document.getElementById('export-progress-wrap').style.display = 'none';
      if (errEl) errEl.textContent = data.error || 'Erreur.';
      window.Toast?.error(data.error || 'Erreur.');
      return;
    }

    startExportPolling(jobId);

  } catch (e) {
    if (btn) btn.disabled = false;
    document.getElementById('export-progress-wrap').style.display = 'none';
    window.Toast?.error(`Erreur : ${e.message}`);
  }
}

/* ═══════════════════════════════════════════════════
   POLLING
═══════════════════════════════════════════════════ */

function startExportPolling(jobId) {
  if (_exportPollTimer) clearInterval(_exportPollTimer);

  _exportPollTimer = setInterval(async () => {
    try {
      const res  = await fetch(`/api/jobs/${jobId}/export/status/`);
      const data = await res.json();

      if (data.status === 'done' && data.has_video) {
        clearInterval(_exportPollTimer);
        setExportProgress(100, 'Vidéo assemblée !');
        setTimeout(() => {
          document.getElementById('export-progress-wrap').style.display = 'none';
        }, 1500);

        const btn = document.getElementById('btn-export');
        if (btn) btn.disabled = false;

        _showDownloadBtns(data.download_url, data.vtt_url);
        _loadFinalVideo(data.download_url, data.vtt_url);
        window.Toast?.success('Vidéo prête !');

      } else if (data.status === 'synthesizing') {
        setExportProgress(60, 'Assemblage en cours…');
      } else if (data.status === 'error') {
        clearInterval(_exportPollTimer);
        document.getElementById('export-progress-wrap').style.display = 'none';
        const btn = document.getElementById('btn-export');
        if (btn) btn.disabled = false;
        window.Toast?.error('Export échoué.');
      }
    } catch (_) {}
  }, POLL_EXPORT_INTERVAL);
}

/* ═══════════════════════════════════════════════════
   BOUTONS TÉLÉCHARGEMENT
═══════════════════════════════════════════════════ */

function _showDownloadBtns(downloadUrl, vttUrl) {
  const wrap = document.getElementById('download-btns');
  if (wrap) wrap.style.display = 'flex';

  const btnDl = document.getElementById('btn-download');
  if (btnDl && downloadUrl) {
    btnDl.href     = `${downloadUrl}?t=${Date.now()}`;
    btnDl.download = 'tutorbuilder.mp4';
  }
}

async function downloadWithSubs() {
  const jobId = document.getElementById('current-job-id')?.value;
  const csrf  = document.getElementById('csrf-token')?.value || '';
  if (!jobId) return;

  const btn       = document.getElementById('btn-download-subs');
  const progWrap  = document.getElementById('burn-progress-wrap');
  const progFill  = document.getElementById('burn-progress-fill');

  if (btn) btn.disabled = true;
  if (progWrap) progWrap.style.display = 'block';

  // Animation de progression
  let pct = 0;
  const timer = setInterval(() => {
    pct = Math.min(pct + 2, 90);
    if (progFill) progFill.style.width = `${pct}%`;
  }, 300);

  try {
    const res = await fetch(`/api/jobs/${jobId}/export/burn/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({ font_size: 28, position: 2 }),
    });

    clearInterval(timer);
    if (progFill) progFill.style.width = '100%';

    if (!res.ok) {
      const data = await res.json();
      window.Toast?.error(data.error || 'Intégration échouée.');
      return;
    }

    // Télécharger le blob
    const blob = await res.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = 'tutorbuilder_avec_sous_titres.mp4';
    a.click();
    URL.revokeObjectURL(url);
    window.Toast?.success('Téléchargement avec sous-titres intégrés !');

  } catch (e) {
    clearInterval(timer);
    window.Toast?.error(`Erreur : ${e.message}`);
  } finally {
    setTimeout(() => {
      if (progWrap) progWrap.style.display = 'none';
      if (btn) btn.disabled = false;
    }, 1000);
  }
}

window.downloadWithSubs = downloadWithSubs;

/* ═══════════════════════════════════════════════════
   PLAYER VTT
═══════════════════════════════════════════════════ */

function _loadFinalVideo(url, vttUrl) {
  if (!url) return;
  _finalVideoUrl = `${url}?t=${Date.now()}`;
  _vttUrl        = vttUrl || '';

  // Sauvegarder URL originale — depuis le player ou JOB_DATA
  if (!_originalVideoUrl) {
    const vid = document.getElementById('video-player');
    if (vid && vid.src && !vid.src.includes('/outputs/')) {
      _originalVideoUrl = vid.src;
    } else if (window.JOB_DATA?.video_url) {
      _originalVideoUrl = window.JOB_DATA.video_url;
    }
  }

  _showFinalVideo();
  _addBeforeAfterToggle();
}

function _showFinalVideo() {
  const vid = document.getElementById('video-player');
  if (!vid) return;

  // Charger la vidéo finale — sans sous-titres dans le player
  vid.innerHTML = '';
  vid.src = _finalVideoUrl;
  vid.load();
  vid.play();
  vid.controls = true;
  _showingFinal = true;
  _updateToggleBtn();
}

function _applySubtitleStyle() {
  let style = document.getElementById('vtt-style');
  if (!style) {
    style = document.createElement('style');
    style.id = 'vtt-style';
    document.head.appendChild(style);
  }
  style.textContent = `
  video::cue {
    background: rgba(0,0,0,0.65);
    color: white;
    font-size: 18px;
    font-family: Arial, 'DM Sans', sans-serif;
    font-weight: 700;
    line-height: 1.5;
    border-radius: 4px;
    padding: 3px 10px;
    text-shadow: 1px 1px 2px black;
  }`;
}

function _showOriginalVideo() {
  const vid = document.getElementById('video-player');
  if (!vid) return;

  const origUrl = _originalVideoUrl || window.JOB_DATA?.video_url;
  if (!origUrl) return;

  // Retirer les tracks sous-titres
  vid.innerHTML = '';
  vid.src = origUrl;
  vid.load();
  _showingFinal = false;
  _updateToggleBtn();
}

function _addBeforeAfterToggle() {
  if (document.getElementById('btn-before-after')) return;
  const transport = document.getElementById('player-transport');
  if (!transport) return;

  const btn = document.createElement('button');
  btn.id        = 'btn-before-after';
  btn.className = 'before-after-btn';
  btn.innerHTML = `
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
      <circle cx="12" cy="12" r="3"/>
    </svg>
    Voir original`;
  btn.addEventListener('click', () => {
    if (_showingFinal) _showOriginalVideo();
    else _showFinalVideo();
  });
  transport.appendChild(btn);

  const style = document.createElement('style');
  style.textContent = `
  .before-after-btn {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 4px 10px; border-radius: 6px;
    border: 1px solid var(--blue-border);
    background: var(--blue-bg); color: var(--blue);
    font-size: 11px; font-weight: 600;
    font-family: var(--font); cursor: pointer;
    transition: var(--transition); white-space: nowrap;
    margin-left: 8px;
  }
  .before-after-btn:hover { background: rgba(59,130,246,.18); }
  .before-after-btn.showing-original {
    border-color: var(--green-border);
    background: var(--green-bg); color: var(--green);
  }`;
  document.head.appendChild(style);
}

function _updateToggleBtn() {
  const btn = document.getElementById('btn-before-after');
  if (!btn) return;
  if (_showingFinal) {
    btn.classList.remove('showing-original');
    btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg> Voir original`;
  } else {
    btn.classList.add('showing-original');
    btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21"/></svg> Voir résultat`;
  }
}

/* ═══════════════════════════════════════════════════
   PROGRESSION
═══════════════════════════════════════════════════ */

function setExportProgress(pct, label) {
  const fill  = document.getElementById('export-progress-fill');
  const lbl   = document.getElementById('export-progress-label');
  const pctEl = document.getElementById('export-progress-pct');
  if (fill)  fill.style.width  = `${pct}%`;
  if (lbl && label) lbl.textContent = label;
  if (pctEl) pctEl.textContent = `${Math.round(pct)}%`;
}

/* ═══════════════════════════════════════════════════
   INIT
═══════════════════════════════════════════════════ */

function updateSubStyle() {
  const fontSize  = parseInt(document.getElementById('sub-font-size')?.value || 20);
  const bgOpacity = parseInt(document.getElementById('sub-bg-opacity')?.value || 72);
  const color     = document.getElementById('sub-color')?.value || '#ffffff';

  document.getElementById('sub-font-size-val').textContent  = `${fontSize}px`;
  document.getElementById('sub-bg-opacity-val').textContent = `${bgOpacity}%`;

  _applySubtitleStyle(fontSize, color, `rgba(0,0,0,${bgOpacity/100})`);
}

window.updateSubStyle = updateSubStyle;

// CSS
const EXPORT_STYLE = document.createElement('style');
EXPORT_STYLE.textContent = `
.sub-style-grid {
  display: flex; flex-direction: column; gap: 8px;
  padding: 10px; background: var(--surface-2);
  border-radius: var(--radius); border: 1px solid var(--border);
}
.sub-style-label {
  font-size: 10.5px; font-weight: 600;
  color: var(--text-3); margin-bottom: 3px;
}
`;
document.head.appendChild(EXPORT_STYLE);

document.addEventListener('DOMContentLoaded', () => {
  const btnExport = document.getElementById('btn-export');
  if (btnExport) btnExport.addEventListener('click', startExport);

  // Activer le bouton si job déjà done
  const status = window.JOB_DATA?.status;
  if (status === 'done' && btnExport) btnExport.disabled = false;

  // Reload — charger vidéo finale si elle existe
  setTimeout(async () => {
    if (window.JOB_DATA?.status !== 'done') return;
    const jobId = document.getElementById('current-job-id')?.value;
    if (!jobId) return;

    // Sauvegarder l'URL originale depuis JOB_DATA (toujours disponible)
    if (window.JOB_DATA?.video_url && !_originalVideoUrl) {
      _originalVideoUrl = window.JOB_DATA.video_url;
    }

    try {
      const res  = await fetch(`/api/jobs/${jobId}/export/status/`);
      const data = await res.json();
      if (data.has_video && data.download_url) {
        _showDownloadBtns(data.download_url, data.vtt_url);
        _loadFinalVideo(data.download_url, data.vtt_url);
      }
    } catch (_) {}
  }, 700);
});