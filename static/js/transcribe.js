/**
 * transcribe.js — Module transcription + timeline + éditeur segment
 * Responsabilité :
 *   - Choix voix + lancement transcription
 *   - Rendu de la timeline
 *   - Édition des segments (texte, vitesse, couper, fusionner, supprimer)
 *   - Undo/Redo
 *   - Polling WebSocket fallback
 */

'use strict';

/* ═══════════════════════════════════════════════════
   CONSTANTES
═══════════════════════════════════════════════════ */

const VOICES = [
  { id: 'narrateur_pro', name: 'Narrateur Pro', wpm: 145 },
  { id: 'narratrice_pro', name: 'Narratrice Pro', wpm: 140 },
  { id: 'expert', name: 'Expert', wpm: 138 },
  { id: 'experte', name: 'Experte', wpm: 142 },
  { id: 'guide', name: 'Guide', wpm: 148 },
  { id: 'pedagogique', name: 'Pédagogique', wpm: 135 },
];

const DEFAULT_WPM = 145;
const MIN_SPEED = 0.25;
const MAX_SPEED = 4.0;
const POLL_INTERVAL = 2500;

/* ═══════════════════════════════════════════════════
   ÉTAT
═══════════════════════════════════════════════════ */

const transcribeState = {
  segments: [],
  selectedIdx: null,
  undoStack: [],
  redoStack: [],
  dirty: false,
  pollTimer: null,
  zoomLevel: 4.0,
  selectedVoice: VOICES[0],
  wpm: DEFAULT_WPM,
  videoDuration: 0,
  waveformData: [],
  modifiedSegments: new Set(), // IDs des segments modifiés depuis dernière synthèse TTS
  unsavedSegments: new Set(), // IDs des segments modifiés non encore sauvegardés en base
};

/* ═══════════════════════════════════════════════════
   DOM
═══════════════════════════════════════════════════ */

const TDOM = {
  wsec2: document.getElementById('wsec-2'),
  btnTranscribe: document.getElementById('btn-transcribe'),
  transcribeError: document.getElementById('transcribe-error'),
  progressWrap: document.getElementById('transcribe-progress-wrap'),
  progressLabel: document.getElementById('transcribe-progress-label'),
  progressFill: document.getElementById('transcribe-progress-fill'),
  voiceGrid: document.getElementById('voice-grid'),
  selectEngine: document.getElementById('select-tts-engine'),
  selectLanguage: document.getElementById('select-language'),
  timelineWrap: document.getElementById('timeline-wrap'),
  timelineSegments: document.getElementById('timeline-segments'),
  timelineEmpty: document.getElementById('timeline-empty'),
  timelineRuler: document.getElementById('timeline-ruler'),
  waveformCanvas: document.getElementById('waveform-canvas'),
  timelineCursor: document.getElementById('timeline-cursor'),
  zoomLabel: document.getElementById('tl-zoom-label'),
  segmentEditor: document.getElementById('segment-editor'),
  segEditorIndex: document.getElementById('seg-editor-index'),
  segEditorTc: document.getElementById('seg-editor-tc'),
  segThumb: document.getElementById('seg-thumb'),
  segThumbPlaceholder: document.getElementById('seg-thumb-placeholder'),
  segTextInput: document.getElementById('seg-text-input'),
  speedValue: document.getElementById('speed-value'),
  speedBar: document.getElementById('speed-bar'),
  speedHint: document.getElementById('speed-hint'),
  speedSlider: document.getElementById('speed-slider'),
  speedSliderVal: document.getElementById('speed-slider-val'),
};

/* ═══════════════════════════════════════════════════
   INIT VOIX
═══════════════════════════════════════════════════ */

function buildVoiceGrid() {
  if (!TDOM.voiceGrid) return;
  TDOM.voiceGrid.innerHTML = '';

  // Voix sauvegardée en base ou première voix par défaut
  const savedVoiceId = window.JOB_DATA?.tts_voice || VOICES[0].id;
  const savedVoice = VOICES.find(v => v.id === savedVoiceId) || VOICES[0];
  transcribeState.selectedVoice = savedVoice;
  transcribeState.wpm = savedVoice.wpm;

  VOICES.forEach((v) => {
    const card = document.createElement('div');
    const isSelected = v.id === savedVoice.id;
    card.className = 'voice-card' + (isSelected ? ' selected' : '');
    card.dataset.voiceId = v.id;
    card.innerHTML = `
      <div class="voice-card-name">${v.name}</div>
      <div class="voice-card-wpm">~${v.wpm} WPM</div>`;
    card.addEventListener('click', () => selectVoice(v, card));
    TDOM.voiceGrid.appendChild(card);
  });
}

function selectVoice(voice, cardEl) {
  transcribeState.selectedVoice = voice;
  transcribeState.wpm = voice.wpm;
  document.querySelectorAll('.voice-card').forEach(c => c.classList.remove('selected'));
  cardEl.classList.add('selected');

  // Sauvegarder en base immédiatement
  const jobId = document.getElementById('current-job-id')?.value;
  const csrf = document.getElementById('csrf-token')?.value || '';
  if (jobId) {
    fetch(`/api/jobs/${jobId}/set-voice/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({ voice: voice.id }),
    }).catch(() => { });
  }

  // Recalculer les speed_factor si des segments existent
  if (transcribeState.segments.length > 0) {
    recalcAllSpeeds();
    renderTimeline();
  }
}

/* ═══════════════════════════════════════════════════
   ACTIVER ÉTAPE 2
═══════════════════════════════════════════════════ */

function unlockTranscription() {
  const wsec2 = TDOM.wsec2;
  if (!wsec2) return;
  wsec2.classList.remove('locked');
  wsec2.classList.add('active');
  const badge = wsec2.querySelector('.wsec-badge-num');
  if (badge) { badge.classList.add('active'); }
  if (TDOM.btnTranscribe) TDOM.btnTranscribe.disabled = false;
}

// Exposer globalement — appelé par upload.js après succès upload
window.unlockTranscription = unlockTranscription;

/* ═══════════════════════════════════════════════════
   LANCER TRANSCRIPTION
═══════════════════════════════════════════════════ */

async function startTranscription() {
  const jobId = document.getElementById('current-job-id')?.value;
  if (!jobId) {
    window.Toast?.error('Aucun job actif. Importez une vidéo d\'abord.');
    return;
  }

  const csrf = document.getElementById('csrf-token')?.value || '';
  const language = TDOM.selectLanguage?.value || 'fr';

  TDOM.btnTranscribe.disabled = true;
  TDOM.progressWrap.style.display = 'block';
  setTranscribeProgress(10, 'Extraction audio…');
  window.Toast?.info('Transcription lancée…');

  try {
    const res = await fetch(`/api/jobs/${jobId}/transcribe/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf,
      },
      body: JSON.stringify({
        language: language,
        stt_engine: 'faster_whisper',
      }),
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.error || 'Erreur lors du lancement.');
    }

    window.Toast?.log('Transcription démarrée…', 'info');
    startPolling(jobId);

  } catch (e) {
    TDOM.btnTranscribe.disabled = false;
    TDOM.progressWrap.style.display = 'none';
    window.Toast?.error(`Transcription échouée : ${e.message}`);
  }
}

function setTranscribeProgress(pct, label = '') {
  if (TDOM.progressFill) TDOM.progressFill.style.width = `${pct}%`;
  if (TDOM.progressLabel && label) TDOM.progressLabel.textContent = label;
}

/* ═══════════════════════════════════════════════════
   POLLING
═══════════════════════════════════════════════════ */

function startPolling(jobId) {
  if (transcribeState.pollTimer) clearInterval(transcribeState.pollTimer);

  transcribeState.pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}/`);
      const job = await res.json();

      if (job.status === 'transcribed' || job.status === 'done') {
        clearInterval(transcribeState.pollTimer);
        setTranscribeProgress(90, 'Chargement des segments…');
        await loadSegments(jobId);
        setTimeout(() => markStep2Done(), 100);
      }

      if (job.status === 'error') {
        clearInterval(transcribeState.pollTimer);
        TDOM.btnTranscribe.disabled = false;
        TDOM.progressWrap.style.display = 'none';
        window.Toast?.error(`Erreur : ${job.error_message || 'Transcription échouée.'}`);
      }

      if (job.status === 'extracting') {
        setTranscribeProgress(25, 'Extraction audio…');
      }

      if (job.status === 'transcribing') {
        setTranscribeProgress(60, 'Transcription en cours…');
      }

    } catch (_) { }
  }, POLL_INTERVAL);
}

/* ═══════════════════════════════════════════════════
   CHARGER LES SEGMENTS
═══════════════════════════════════════════════════ */

async function loadSegments(jobId) {
  try {
    const res = await fetch(`/api/jobs/${jobId}/segments/`);
    const data = await res.json();
    const segs = Array.isArray(data) ? data : (data.results || []);

    transcribeState.segments = segs.map(s => ({
      id: s.id,
      index: s.index,
      start_ms: s.start_ms,
      end_ms: s.end_ms,
      trim_start_ms: s.trim_start_ms || 0,
      trim_end_ms: s.trim_end_ms || 0,
      text: s.text || '',
      speed_factor: s.speed_factor || 1.0,
      speed_forced: s.speed_forced || false,
      thumb_url: s.thumb_url || '',
      has_audio: s.has_audio || false,
      deleted: false,
    }));

    // Restaurer les segments modifiés depuis sessionStorage
    restoreModifiedSegments();

    setTranscribeProgress(100, 'Transcription terminée !');
    setTimeout(() => { TDOM.progressWrap.style.display = 'none'; }, 1500);

    showTimeline();

    // Attendre que le DOM soit rendu avant renderTimeline
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        renderTimeline();
      });
    });

    window.Toast?.success(`${transcribeState.segments.length} segments chargés.`);

  } catch (e) {
    window.Toast?.error(`Chargement segments échoué : ${e.message}`);
  }
}

/* ═══════════════════════════════════════════════════
   MARQUER ÉTAPE 2 DONE
═══════════════════════════════════════════════════ */

function markStep2Done() {
  const wsec2 = document.getElementById('wsec-2');
  if (!wsec2) return;
  wsec2.classList.remove('active');
  wsec2.classList.add('done');

  const badge = wsec2.querySelector('.wsec-badge-num');
  if (badge) {
    badge.classList.remove('active');
    badge.classList.add('done');
    badge.innerHTML = `
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <polyline points="20 6 9 17 4 12"/>
      </svg>`;
  }

  const status = document.getElementById('wsec-2-status');
  if (status) status.innerHTML = `<span class="wsec-done-badge">✓ Fait</span>`;

  const pill2 = document.getElementById('pill-2');
  if (pill2) { pill2.classList.remove('active'); pill2.classList.add('done'); }

  // Désactiver bouton transcription
  const btn = document.getElementById('btn-transcribe');
  if (btn) {
    btn.disabled = true;
    btn.style.opacity = '0.5';
    btn.style.cursor = 'not-allowed';
  }

  // Déverrouiller étape 3
  const _doUnlock3 = () => {
    if (typeof window.unlockStep3 === 'function') {
      window.unlockStep3();
    } else {
      setTimeout(_doUnlock3, 100);
    }
  };
  setTimeout(_doUnlock3, 50);
}

/* ═══════════════════════════════════════════════════
   AFFICHER LA TIMELINE
═══════════════════════════════════════════════════ */

function showTimeline() {
  if (TDOM.timelineWrap) TDOM.timelineWrap.style.display = 'flex';
  if (TDOM.segmentEditor) TDOM.segmentEditor.style.display = 'block';
}

function updateUnsavedBadge() {
  const count = transcribeState.unsavedSegments.size;
  const badge = document.getElementById('unsaved-badge');
  const countEl = document.getElementById('unsaved-count');
  const btn = document.getElementById('btn-save-all');

  if (badge) badge.style.display = count > 0 ? 'flex' : 'none';
  if (countEl) countEl.textContent = count;
  if (btn) {
    if (count > 0) btn.classList.add('btn-save-pulse');
    else btn.classList.remove('btn-save-pulse');
  }
}

function markSegmentModified(idx) {
  const seg = transcribeState.segments[idx];
  if (seg?.id) {
    transcribeState.modifiedSegments.add(String(seg.id));
    transcribeState.unsavedSegments.add(String(seg.id));
    const jobId = document.getElementById('current-job-id')?.value;
    if (jobId) {
      sessionStorage.setItem(`modified_segs_${jobId}`, JSON.stringify([...transcribeState.modifiedSegments]));
    }
    updateUnsavedBadge();
  }
}

function clearModifiedSegments() {
  transcribeState.modifiedSegments.clear();
  transcribeState.unsavedSegments.clear();
  const jobId = document.getElementById('current-job-id')?.value;
  if (jobId) sessionStorage.removeItem(`modified_segs_${jobId}`);
  updateUnsavedBadge();
}

function restoreModifiedSegments() {
  const jobId = document.getElementById('current-job-id')?.value;
  if (!jobId) return;

  // Ne pas restaurer si voix déjà générée — on repart de zéro
  const status = window.JOB_DATA?.status;
  if (status === 'done') {
    sessionStorage.removeItem(`modified_segs_${jobId}`);
    return;
  }

  const key = `modified_segs_${jobId}`;
  const stored = sessionStorage.getItem(key);
  if (stored) {
    try {
      const ids = JSON.parse(stored);
      ids.forEach(id => transcribeState.modifiedSegments.add(id));
    } catch (_) { }
  }
}

window.getModifiedSegmentIds = () => [...transcribeState.modifiedSegments];
window.clearModifiedSegments = clearModifiedSegments;
window.restoreModifiedSegments = restoreModifiedSegments;
window.transcribeState = transcribeState;
window.updateUnsavedBadge = updateUnsavedBadge;

/* ═══════════════════════════════════════════════════
   CALCUL VITESSE
═══════════════════════════════════════════════════ */

function calcSpeedFactor(text, durationMs, wpm) {
  if (!text || !text.trim()) {
    // Silence — accélérer selon la durée
    const s = durationMs / 1000;
    if (s < 2) return 1.0;
    if (s < 5) return 2.0;
    if (s < 15) return 4.0;
    return 4.0;
  }
  const nbMots = text.trim().split(/\s+/).filter(Boolean).length;
  const voixS = (nbMots / wpm) * 60;
  const videoS = durationMs / 1000;
  if (videoS <= 0 || voixS <= 0) return 1.0;
  const factor = videoS / voixS;
  // Clamper entre 0.25 et 4.0
  return Math.round(Math.max(0.25, Math.min(4.0, factor)) * 10000) / 10000;
}

function recalcAllSpeeds() {
  transcribeState.segments.forEach(seg => {
    if (!seg.speed_forced) {
      seg.speed_factor = calcSpeedFactor(
        seg.text,
        seg.end_ms - seg.start_ms,
        transcribeState.wpm,
      );
    }
  });
}

function speedClass(factor) {
  if (factor < 0.8) return 'warn';  // ralenti significatif
  if (factor < 1.0) return 'slow';  // légèrement ralenti
  if (factor === 1.0) return 'normal';
  return 'fast';                      // accéléré
}

function speedHintText(factor, text, durationMs, isForced, autoFactor) {
  const isEmpty = !text || !text.trim();
  if (isEmpty) return `Silence — accéléré à x${factor.toFixed(2)}.`;

  if (isForced && factor < autoFactor) {
    const silenceS = ((autoFactor - factor) / autoFactor * durationMs / 1000).toFixed(1);
    return `Vidéo ralentie manuellement à x${factor.toFixed(2)} — ${silenceS}s de silence après la voix.`;
  }
  if (factor < 1.0) return `Vidéo ralentie à x${factor.toFixed(2)} — le texte est long.`;
  if (factor === 1.0) return 'Vitesse normale — parfaitement synchronisé.';
  return `Vidéo accélérée à x${factor.toFixed(2)} — le texte est court.`;
}

/* ═══════════════════════════════════════════════════
   RENDU TIMELINE
═══════════════════════════════════════════════════ */

function renderTimeline() {
  const container = TDOM.timelineSegments;
  if (!container) return;

  const segs = transcribeState.segments;
  const visibleSegs = segs.filter(s => !s.deleted);

  if (!visibleSegs.length) {
    container.innerHTML = `<div class="timeline-empty"><p>La timeline apparaît après la transcription</p></div>`;
    return;
  }

  const totalMs = segs[segs.length - 1].end_ms;
  const zoom = transcribeState.zoomLevel;

  const inner = document.createElement('div');
  inner.style.cssText = `
    position: relative;
    width: ${100 * zoom}%;
    min-width: ${100 * zoom}%;
    height: 100%;
  `;

  segs.forEach((seg, i) => {
    const durMs = seg.end_ms - seg.start_ms;
    const leftPct = (seg.start_ms / totalMs) * 100;
    const widthPct = Math.max(0.5, (durMs / totalMs) * 100);
    const sc = speedClass(seg.speed_factor);
    const isSilence = !seg.text?.trim();
    const durS = (durMs / 1000).toFixed(1);
    const tcStart = msToTC(seg.start_ms);
    const tcEnd = msToTC(seg.end_ms);

    const block = document.createElement('div');
    block.className = 'tl-segment';
    block.dataset.idx = i;
    block.style.cssText = `
      position: absolute;
      left: ${leftPct}%;
      width: ${widthPct}%;
      top: 4px;
      bottom: 4px;
    `;

    if (seg.deleted) {
      block.classList.add('deleted');
    } else if (isSilence) {
      block.classList.add('silence');
    }
    if (i === transcribeState.selectedIdx) block.classList.add('selected');

    if (seg.deleted) {
      block.classList.add('deleted');
      // Pas cliquable, pas de poignées — le voisin vivant absorbe via ses propres poignées
      block.style.pointerEvents = 'none';
      block.innerHTML = `
          <div class="tl-seg-deleted-overlay">
            <span class="tl-seg-deleted-label">Coupé</span>
          </div>`;
    }

    else {
      block.innerHTML = `
        ${seg.thumb_url ? `<img class="tl-seg-thumb" src="${seg.thumb_url}" alt="" loading="lazy">` : ''}
        <div class="tl-seg-tc">${tcStart} → ${tcEnd}</div>
        <div class="tl-seg-text">${seg.text || '<em>Silence</em>'}</div>
        <span class="tl-seg-speed ${sc}">x${seg.speed_factor.toFixed(2)} · ${durS}s</span>
        <div class="tl-resize-handle tl-resize-left"  data-seg="${i}" data-side="left"></div>
        <div class="tl-resize-handle tl-resize-right" data-seg="${i}" data-side="right"></div>`;

      block.addEventListener('click', (e) => {
        if (e.target.closest('.tl-resize-handle')) return;
        selectSegment(i);
      });

      block.addEventListener('dblclick', (e) => {
        if (e.target.closest('.tl-resize-handle')) return;
        cutSegmentAt(i, e);
      });
    }

    inner.appendChild(block);
  });

  container.innerHTML = '';
  container.appendChild(inner);

  // Poignées drag
  inner.querySelectorAll('.tl-resize-handle').forEach(handle => {
    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const idx = parseInt(handle.dataset.seg);
      const side = handle.dataset.side;
      const innerW = inner.offsetWidth || container.offsetWidth || 900;
      startResize(e, idx, side, innerW, totalMs);
    });
  });

  renderRuler(totalMs, inner.style.width);
}

/* ═══════════════════════════════════════════════════
   LECTURE AUDIO SEGMENT
═══════════════════════════════════════════════════ */

let _audioPlayer = null;

function playSegmentAudio() {
  const idx = transcribeState.selectedIdx;
  if (idx === null) return;

  const seg = transcribeState.segments[idx];
  const jobId = document.getElementById('current-job-id')?.value;
  if (!seg?.id || !jobId) return;

  const btn = document.getElementById('seg-audio-btn');

  // Si déjà en lecture → arrêter
  if (_audioPlayer && !_audioPlayer.paused) {
    _audioPlayer.pause();
    _audioPlayer.currentTime = 0;
    if (btn) {
      btn.classList.remove('playing');
      btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21"/></svg>`;
    }
    return;
  }

  const url = `/api/jobs/${jobId}/segments/${seg.id}/audio/`;
  _audioPlayer = new Audio(url);

  _audioPlayer.addEventListener('play', () => {
    if (btn) {
      btn.classList.add('playing');
      btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`;
    }
  });

  _audioPlayer.addEventListener('ended', () => {
    if (btn) {
      btn.classList.remove('playing');
      btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21"/></svg>`;
    }
  });

  _audioPlayer.addEventListener('error', () => {
    window.Toast?.error('Fichier audio introuvable — générez la voix d\'abord.');
    if (btn) btn.classList.remove('playing');
  });

  _audioPlayer.play();
}

window.playSegmentAudio = playSegmentAudio;

function renderRuler(totalMs, widthPx) {
  const ruler = TDOM.timelineRuler;
  if (!ruler) return;
  ruler.innerHTML = '';
  ruler.style.position = 'relative';
  ruler.style.width = `${widthPx}px`;

  const stepMs = totalMs > 60000 ? 10000 : totalMs > 30000 ? 5000 : 2000;
  for (let ms = 0; ms <= totalMs; ms += stepMs) {
    const pct = ms / totalMs;
    const left = pct * widthPx;
    const tick = document.createElement('div');
    tick.className = 'ruler-tick';
    tick.style.left = `${left}px`;
    tick.style.position = 'absolute';
    tick.innerHTML = `
      <div class="ruler-tick-line"></div>
      <div class="ruler-tick-label">${msToTC(ms)}</div>`;
    ruler.appendChild(tick);
  }
}

function msToTC(ms) {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

/* ═══════════════════════════════════════════════════
   WAVEFORM
═══════════════════════════════════════════════════ */

function drawWaveform(data) {
  const canvas = TDOM.waveformCanvas;
  if (!canvas) return;
  const W = canvas.offsetWidth || 800;
  const H = canvas.offsetHeight || 40;
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#0A0A0B';
  ctx.fillRect(0, 0, W, H);
  if (!data.length) return;

  const g = ctx.createLinearGradient(0, 0, W, 0);
  g.addColorStop(0, 'rgba(59,130,246,.5)');
  g.addColorStop(0.5, 'rgba(96,165,250,.7)');
  g.addColorStop(1, 'rgba(59,130,246,.5)');
  ctx.strokeStyle = g;
  ctx.lineWidth = 1;

  const step = W / data.length;
  for (let i = 0; i < data.length; i++) {
    const x = i * step;
    const a = data[i] * (H / 2);
    ctx.beginPath();
    ctx.moveTo(x, H / 2 - a);
    ctx.lineTo(x, H / 2 + a);
    ctx.stroke();
  }
}

/* ═══════════════════════════════════════════════════
   SÉLECTIONNER UN SEGMENT
═══════════════════════════════════════════════════ */

function selectSegment(idx) {
  transcribeState.selectedIdx = idx;
  renderTimeline();
  loadSegmentEditor(idx);

  // Aller au bon moment dans la vidéo
  const seg = transcribeState.segments[idx];
  const vid = document.getElementById('video-player');
  if (vid && seg) {
    const t = seg.start_ms / 1000;
    if (isFinite(t) && t >= 0) vid.currentTime = t;
  }
}

function loadSegmentEditor(idx) {
  const seg = transcribeState.segments[idx];
  if (!seg || !TDOM.segmentEditor) return;

  TDOM.segmentEditor.style.display = 'block';
  TDOM.segEditorIndex.textContent = `Segment ${idx + 1}`;

  // Afficher le bouton sauvegarder
  const btnSave = document.getElementById('btn-save-segment');
  if (btnSave) btnSave.style.display = 'flex';

  // Afficher les boutons Fusionner / Supprimer
  const actionsPanel = document.getElementById('seg-actions-panel');
  if (actionsPanel) actionsPanel.style.display = 'flex';

  // Timecodes éditables
  renderEditableTc(idx);

  // Mini player — toujours réinitialiser au segment sélectionné
  if (window._miniPlayerLoop) {
    cancelAnimationFrame(window._miniPlayerLoop);
    window._miniPlayerLoop = null;
  }
  initMiniPlayer(seg);
  TDOM.segmentEditor.dataset.currentIdx = String(idx);

  // Bouton lecture audio
  const audioBtn = document.getElementById('seg-audio-btn');
  if (audioBtn) {
    audioBtn.style.display = seg.has_audio ? 'flex' : 'none';
  }

  const effStart = seg.trim_start_ms > 0 ? seg.trim_start_ms : seg.start_ms;
  const effEnd = seg.trim_end_ms > 0 ? seg.trim_end_ms : seg.end_ms;
  const effDur = effEnd - effStart;

  const autoFactor = calcSpeedFactor(seg.text, effDur, transcribeState.wpm);
  const maxMots = calcMaxMots(effDur, transcribeState.wpm, 0.25);

  // Texte
  TDOM.segTextInput.value = seg.text || '';
  TDOM.segTextInput.removeAttribute('maxLength');
  updateWordBudget(seg.text, maxMots);

  // Vitesse
  updateSpeedUI(seg.speed_factor, seg.text, effDur, seg.speed_forced, autoFactor);

  // Slider
  TDOM.speedSlider.min = '0.25';
  TDOM.speedSlider.max = Math.max(autoFactor, 0.25).toFixed(2);
  TDOM.speedSlider.value = seg.speed_forced
    ? Math.min(seg.speed_factor, autoFactor).toFixed(2)
    : autoFactor.toFixed(2);
  TDOM.speedSliderVal.textContent = `x${(+TDOM.speedSlider.value).toFixed(2)}`;
}

function calcMaxMots(durationMs, wpm, speedFactor) {
  const speed = Math.max(0.25, Math.min(4.0, speedFactor || 1.0));
  const dureeVoixS = (durationMs / 1000) / speed;
  return Math.max(1, Math.floor(dureeVoixS * wpm / 60));
}

function updateWordBudget(text, maxMots) {
  const nbMots = (text || '').trim().split(/\s+/).filter(Boolean).length;
  const budgetEl = document.getElementById('seg-word-budget');
  if (!budgetEl) return;

  const restant = maxMots - nbMots;
  if (restant < 0) {
    budgetEl.textContent = `Trop long — retirez ${Math.abs(restant)} mot(s)`;
    budgetEl.style.color = 'var(--red)';
  } else if (restant <= 3) {
    budgetEl.textContent = `Budget presque plein — ${restant} mot(s) restant(s)`;
    budgetEl.style.color = 'var(--amber)';
  } else {
    budgetEl.textContent = `${nbMots} / ${maxMots} mots`;
    budgetEl.style.color = 'var(--text-3)';
  }
}

/* ═══════════════════════════════════════════════════
   TIMECODES ÉDITABLES
═══════════════════════════════════════════════════ */

function renderEditableTc(idx) {
  const tcEl = TDOM.segEditorTc;
  if (!tcEl) return;

  const seg = transcribeState.segments[idx];
  const origStart = seg._origStart ?? seg.start_ms;
  const origEnd = seg._origEnd ?? seg.end_ms;

  // Sauvegarder originaux si première fois
  if (!seg._origStart) { seg._origStart = seg.start_ms; seg._origEnd = seg.end_ms; }

  tcEl.innerHTML = `
    <div class="tc-edit-row">
      <input class="tc-input" id="tc-start" type="text"
        value="${msToTC(seg.start_ms)}" title="Début — format MM:SS">
      <span class="tc-arrow">→</span>
      <input class="tc-input" id="tc-end" type="text"
        value="${msToTC(seg.end_ms)}" title="Fin — format MM:SS">
      <button class="tc-reset-btn" title="Réinitialiser au timing Whisper"
        onclick="resetTiming(${idx})">
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="1 4 1 10 7 10"/>
          <path d="M3.51 15a9 9 0 1 0 .49-3.85"/>
        </svg>
      </button>
    </div>`;

  const inputStart = document.getElementById('tc-start');
  const inputEnd = document.getElementById('tc-end');

  inputStart.addEventListener('change', () => applyTcEdit(idx, 'start', inputStart.value));
  inputEnd.addEventListener('change', () => applyTcEdit(idx, 'end', inputEnd.value));

  // Confirmer avec Entrée
  [inputStart, inputEnd].forEach(inp => {
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter') inp.blur();
      if (e.key === 'Escape') { inp.value = inp.defaultValue; inp.blur(); }
    });
  });
}

function tcToMs(tc) {
  const parts = tc.trim().split(':');
  if (parts.length === 2) {
    const m = parseInt(parts[0]) || 0;
    const s = parseFloat(parts[1]) || 0;
    return Math.round((m * 60 + s) * 1000);
  }
  return null;
}

function applyTcEdit(idx, side, value) {
  const seg = transcribeState.segments[idx];
  const prevSeg = transcribeState.segments[idx - 1];
  const nextSeg = transcribeState.segments[idx + 1];
  if (!seg) return;

  const newMs = tcToMs(value);
  if (newMs === null) {
    window.Toast?.error('Format invalide — utilisez MM:SS (ex: 01:23)');
    renderEditableTc(idx);
    return;
  }

  if (side === 'start') {
    const minMs = prevSeg ? prevSeg.end_ms : 0;
    const maxMs = seg.end_ms - MIN_SEG_MS;
    if (newMs < minMs) {
      window.Toast?.error(`Début ne peut pas être avant la fin du segment précédent (${msToTC(minMs)})`);
      renderEditableTc(idx); return;
    }
    if (newMs > maxMs) {
      window.Toast?.error(`Début ne peut pas dépasser ${msToTC(maxMs)}`);
      renderEditableTc(idx); return;
    }
    pushUndo('Modifier timecode début');
    seg.start_ms = newMs;
    if (prevSeg) prevSeg.end_ms = newMs;

  } else {
    const minMs = seg.start_ms + MIN_SEG_MS;
    const maxMs = nextSeg ? nextSeg.start_ms : seg.end_ms + 60000;
    if (newMs < minMs) {
      window.Toast?.error(`Fin ne peut pas être avant ${msToTC(minMs)}`);
      renderEditableTc(idx); return;
    }
    if (nextSeg && newMs > nextSeg.start_ms) {
      window.Toast?.error(`Fin ne peut pas dépasser le début du segment suivant (${msToTC(nextSeg.start_ms)})`);
      renderEditableTc(idx); return;
    }
    pushUndo('Modifier timecode fin');
    seg.end_ms = newMs;
    if (nextSeg) nextSeg.start_ms = newMs;
  }

  // Recalculer speed
  if (!seg.speed_forced) {
    seg.speed_factor = calcSpeedFactor(seg.text, seg.end_ms - seg.start_ms, transcribeState.wpm);
  }

  transcribeState.dirty = true;
  markSegmentModified(idx);
  renderTimeline();
  loadSegmentEditor(idx);
}

function resetTiming(idx) {
  const seg = transcribeState.segments[idx];
  if (!seg || !seg._origStart) return;
  pushUndo('Réinitialiser timing');
  seg.start_ms = seg._origStart;
  seg.end_ms = seg._origEnd;
  if (!seg.speed_forced) {
    seg.speed_factor = calcSpeedFactor(seg.text, seg.end_ms - seg.start_ms, transcribeState.wpm);
  }
  transcribeState.dirty = true;
  renderTimeline();
  loadSegmentEditor(idx);
  window.Toast?.success('Timing réinitialisé.');
}
window.resetTiming = resetTiming;

function updateSpeedUI(factor, text, durationMs, isForced, autoFactor) {
  const sc = speedClass(factor);
  TDOM.speedValue.textContent = `x${factor.toFixed(2)}`;
  TDOM.speedValue.className = `speed-value ${sc}`;

  const pct = factor <= 1.0
    ? (factor / 1.0) * 50
    : 50 + Math.min(((factor - 1.0) / 3.0) * 50, 50);
  TDOM.speedBar.style.width = `${pct}%`;
  TDOM.speedBar.style.background =
    sc === 'warn' ? 'var(--red)' :
      sc === 'slow' ? 'var(--amber)' :
        sc === 'fast' ? 'var(--green)' : 'var(--blue)';

  TDOM.speedHint.textContent = speedHintText(factor, text, durationMs, isForced, autoFactor);
}

/* ═══════════════════════════════════════════════════
   ÉDITION TEXTE EN TEMPS RÉEL
═══════════════════════════════════════════════════ */

if (TDOM.segTextInput) {
  TDOM.segTextInput.addEventListener('input', () => {
    const idx = transcribeState.selectedIdx;
    if (idx === null) return;
    const seg = transcribeState.segments[idx];
    if (!seg) return;

    const newText = TDOM.segTextInput.value;
    const effStart = seg.trim_start_ms > 0 ? seg.trim_start_ms : seg.start_ms;
    const effEnd = seg.trim_end_ms > 0 ? seg.trim_end_ms : seg.end_ms;
    const effDur = effEnd - effStart;

    const maxMots = calcMaxMots(effDur, transcribeState.wpm, 0.25);
    const words = newText.trim().split(/\s+/).filter(Boolean);

    if (words.length > maxMots) {
      const truncated = words.slice(0, maxMots).join(' ');
      TDOM.segTextInput.value = truncated;
      seg.text = truncated;
      updateWordBudget(truncated, maxMots);
      window.Toast?.warn(`Texte tronqué — max ${maxMots} mots à x0.25.`);
    } else {
      seg.text = newText;
      updateWordBudget(newText, maxMots);
    }

    markSegmentModified(idx);

    const newSpeed = calcSpeedFactor(seg.text, effDur, transcribeState.wpm);
    if (!seg.speed_forced) {
      seg.speed_factor = newSpeed;
      TDOM.speedSlider.max = Math.max(newSpeed, 0.25).toFixed(2);
      TDOM.speedSlider.value = newSpeed.toFixed(2);
      TDOM.speedSliderVal.textContent = `x${newSpeed.toFixed(2)}`;
      updateSpeedUI(newSpeed, seg.text, effDur, false, newSpeed);
    }

    transcribeState.dirty = true;
    renderTimeline();
  });
}

/* ═══════════════════════════════════════════════════
   SLIDER VITESSE FORCÉE
═══════════════════════════════════════════════════ */

if (TDOM.speedSlider) {
  TDOM.speedSlider.addEventListener('input', () => {
    const idx = transcribeState.selectedIdx;
    if (idx === null) return;
    const seg = transcribeState.segments[idx];
    if (!seg) return;

    const effStart = seg.trim_start_ms > 0 ? seg.trim_start_ms : seg.start_ms;
    const effEnd = seg.trim_end_ms > 0 ? seg.trim_end_ms : seg.end_ms;
    const effDur = effEnd - effStart;
    const val = parseFloat(TDOM.speedSlider.value);
    const autoFactor = calcSpeedFactor(seg.text, effDur, transcribeState.wpm);

    seg.speed_factor = val;
    seg.speed_forced = true;
    TDOM.speedSliderVal.textContent = `x${val.toFixed(2)}`;
    updateSpeedUI(val, seg.text, effDur, true, autoFactor);

    const maxMots = calcMaxMots(effDur, transcribeState.wpm, val);
    updateWordBudget(seg.text, maxMots);

    transcribeState.dirty = true;
    clearTimeout(TDOM.speedSlider._timer);
    TDOM.speedSlider._timer = setTimeout(() => renderTimeline(), 200);
  });
}

function resetSpeedFactor() {
  const idx = transcribeState.selectedIdx;
  if (idx === null) return;
  const seg = transcribeState.segments[idx];
  if (!seg) return;

  seg.speed_forced = false;
  seg.speed_factor = calcSpeedFactor(seg.text, seg.end_ms - seg.start_ms, transcribeState.wpm);
  TDOM.speedSlider.max = seg.speed_factor.toFixed(2);
  TDOM.speedSlider.value = seg.speed_factor;
  TDOM.speedSliderVal.textContent = `x${seg.speed_factor.toFixed(2)}`;
  updateSpeedUI(seg.speed_factor, seg.text, seg.end_ms - seg.start_ms, false, seg.speed_factor);
  renderTimeline();
  window.Toast?.info('Vitesse recalculée automatiquement.');
}

/* ═══════════════════════════════════════════════════
   COUPER UN SEGMENT
═══════════════════════════════════════════════════ */

function cutSegmentAt(idx, eventOrMs) {
  const seg = transcribeState.segments[idx];
  const container = TDOM.timelineSegments;
  if (!seg) return;

  let clickMs;

  // Si on reçoit un timecode direct (depuis cutAtMiniPlayer)
  if (typeof eventOrMs === 'number') {
    clickMs = eventOrMs;
  } else {
    // Si on reçoit un event (depuis double-clic timeline)
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const clickX = eventOrMs.clientX - rect.left + container.scrollLeft;
    const innerW = container.firstChild?.offsetWidth || container.offsetWidth;
    const totalMs = transcribeState.segments[transcribeState.segments.length - 1].end_ms;
    clickMs = Math.round((clickX / innerW) * totalMs);
  }

  // Vérifier que le point de coupe est dans le segment
  if (clickMs <= seg.start_ms + 200 || clickMs >= seg.end_ms - 200) {
    window.Toast?.warn('Le point de coupe doit être à l\'intérieur du segment.');
    return;
  }

  pushUndo('Couper segment');

  // Répartir le texte proportionnellement
  const ratio = (clickMs - seg.start_ms) / (seg.end_ms - seg.start_ms);
  const words = (seg.text || '').trim().split(/\s+/).filter(Boolean);
  const split = Math.max(1, Math.round(words.length * ratio));
  const text1 = words.slice(0, split).join(' ');
  const text2 = words.slice(split).join(' ');

  const dur1 = clickMs - seg.start_ms;
  const dur2 = seg.end_ms - clickMs;

  // Vérifier budget texte pour chaque morceau
  const maxMots1 = calcMaxMots(dur1, transcribeState.wpm, 0.25);
  const maxMots2 = calcMaxMots(dur2, transcribeState.wpm, 0.25);

  if (text1 && text1.split(/\s+/).filter(Boolean).length > maxMots1) {
    window.Toast?.error(`Le premier morceau dépasse son budget (${maxMots1} mots max). Coupez plus loin à droite.`);
    return;
  }
  if (text2 && text2.split(/\s+/).filter(Boolean).length > maxMots2) {
    window.Toast?.error(`Le second morceau dépasse son budget (${maxMots2} mots max). Coupez plus loin à gauche.`);
    return;
  }

  const seg1 = {
    ...seg,
    end_ms: clickMs,
    text: text1,
    trim_start_ms: seg.trim_start_ms || seg.start_ms,
    trim_end_ms: clickMs,
    has_audio: false,
    speed_forced: false,
  };
  const seg2 = {
    id: `new_${Date.now()}`,
    index: seg.index + 0.5,
    start_ms: clickMs,
    end_ms: seg.end_ms,
    trim_start_ms: clickMs,
    trim_end_ms: seg.trim_end_ms || seg.end_ms,
    text: text2,
    speed_forced: false,
    thumb_url: seg.thumb_url,
    has_audio: false,
    deleted: false,
    speed_factor: 1.0,
  };

  seg1.speed_factor = calcSpeedFactor(text1, dur1, transcribeState.wpm);
  seg2.speed_factor = calcSpeedFactor(text2, dur2, transcribeState.wpm);

  transcribeState.segments.splice(idx, 1, seg1, seg2);
  reindexSegments();
  renderTimeline();
  selectSegment(idx);

  const sc1 = seg1.speed_factor.toFixed(2);
  const sc2 = seg2.speed_factor.toFixed(2);
  window.Toast?.success(`Segment coupé — x${sc1} / x${sc2}`);
  transcribeState.dirty = true;
}

/* ═══════════════════════════════════════════════════
   FUSIONNER AVEC LE SUIVANT
═══════════════════════════════════════════════════ */

function mergeWithNext(idx) {
  if (idx === undefined) idx = transcribeState.selectedIdx;
  if (idx === null) return;

  // Trouver le vrai voisin non supprimé
  const nextSeg = transcribeState.segments.slice(idx + 1).find(s => !s.deleted);
  if (!nextSeg) {
    window.Toast?.warn('Pas de segment suivant à fusionner.');
    return;
  }
  const nextIdx = transcribeState.segments.indexOf(nextSeg);

  pushUndo('Fusionner segments');

  const s1 = transcribeState.segments[idx];
  const merged = {
    ...s1,
    end_ms: nextSeg.end_ms,
    trim_end_ms: nextSeg.trim_end_ms > 0 ? nextSeg.trim_end_ms : nextSeg.end_ms,
    text: [s1.text, nextSeg.text].filter(Boolean).join(' '),
    speed_forced: false,
    has_audio: false,
  };
  merged.speed_factor = calcSpeedFactor(merged.text, merged.end_ms - merged.start_ms, transcribeState.wpm);

  // Remplacer s1 par le merged et marquer nextSeg comme supprimé
  transcribeState.segments[idx] = merged;
  transcribeState.segments[nextIdx].deleted = true;
  transcribeState.segments[nextIdx].text = '';

  reindexSegments();
  renderTimeline();
  selectSegment(idx);
  transcribeState.dirty = true;
  markSegmentModified(idx);

  saveAllSegments().then(() => {
    window.Toast?.success('Segments fusionnés et sauvegardés.');
  });
}

/* ═══════════════════════════════════════════════════
   SUPPRIMER UN SEGMENT
═══════════════════════════════════════════════════ */

function confirmDeleteSegment(idx) {
  if (idx === undefined) idx = transcribeState.selectedIdx;
  if (idx === null) return;

  const seg = transcribeState.segments[idx];
  const msg = seg.text?.trim()
    ? `Supprimer le segment "${seg.text.slice(0, 40)}…" ?`
    : 'Supprimer ce silence ?';

  if (!confirm(msg)) return;
  deleteSegment(idx);
}

function deleteSegment(idx) {
  if (idx === undefined) idx = transcribeState.selectedIdx;
  if (idx === null) return;

  const seg = transcribeState.segments[idx];
  const apercu = seg?.text ? `"${seg.text.slice(0, 60)}${seg.text.length > 60 ? '...' : ''}"` : '(segment vide)';

  showConfirmModal({
    title: 'Supprimer ce segment ?',
    message: `Vous allez supprimer le segment ${idx + 1} : ${apercu}. Cette action est irréversible — le segment sera définitivement retiré de la vidéo.`,
    confirmLabel: 'Supprimer',
    confirmClass: 'danger',
    onConfirm: () => {
      pushUndo('Supprimer segment');

      // Marquer supprimé — les timecodes source restent intacts
      // L'export saute ce segment et colle les voisins automatiquement
      transcribeState.segments[idx].deleted = true;
      transcribeState.segments[idx].text = '';

      // Sélectionner le voisin
      const visibleAfter = transcribeState.segments.find((s, i) => i > idx && !s.deleted);
      const visibleBefore = [...transcribeState.segments].slice(0, idx).reverse().find(s => !s.deleted);
      const newSel = visibleAfter || visibleBefore;
      transcribeState.selectedIdx = newSel ? transcribeState.segments.indexOf(newSel) : null;

      renderTimeline();
      if (transcribeState.selectedIdx !== null) {
        loadSegmentEditor(transcribeState.selectedIdx);
      } else {
        if (TDOM.segmentEditor) TDOM.segmentEditor.style.display = 'none';
      }

      transcribeState.dirty = true;
      saveAllSegments().then(() => {
        window.Toast?.warn('Segment supprimé.');
      });
    }
  });
}

/* ═══════════════════════════════════════════════════
   MODALE DE CONFIRMATION
═══════════════════════════════════════════════════ */

function showConfirmModal({ title, message, confirmLabel, confirmClass, onConfirm }) {
  // Supprimer modale existante
  const existing = document.getElementById('confirm-modal-overlay');
  if (existing) existing.remove();

  const overlay = document.createElement('div');
  overlay.id = 'confirm-modal-overlay';
  overlay.style.cssText = `
    position:fixed; inset:0; z-index:99999;
    display:flex; align-items:center; justify-content:center;
    background:rgba(0,0,0,.5); backdrop-filter:blur(4px);
  `;

  const isDanger = confirmClass === 'danger';

  overlay.innerHTML = `
    <div style="
      background:var(--surface); border:1px solid var(--border);
      border-radius:14px; padding:28px; width:min(440px,92vw);
      box-shadow:0 24px 48px rgba(0,0,0,.2);
      animation: confirm-pop .18s cubic-bezier(.22,1,.36,1);
    ">
      <style>@keyframes confirm-pop { from{opacity:0;transform:scale(.95)} to{opacity:1;transform:scale(1)} }</style>
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">
        <div style="
          width:38px;height:38px;border-radius:10px;flex-shrink:0;
          background:${isDanger ? '#FEE2E2' : '#DBEAFE'};
          display:flex;align-items:center;justify-content:center;
          color:${isDanger ? '#DC2626' : '#2563EB'};
        ">
          ${isDanger
      ? `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/>
                <path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/>
               </svg>`
      : `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="8" x2="12" y2="12"/>
                <line x1="12" y1="16" x2="12.01" y2="16"/>
               </svg>`
    }
        </div>
        <div style="font-size:15px;font-weight:800;color:var(--text-1)">${title}</div>
      </div>
      <p style="font-size:13px;color:var(--text-2);line-height:1.6;margin:0 0 22px">${message}</p>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button id="confirm-modal-cancel" style="
          padding:9px 18px;border-radius:8px;border:1px solid var(--border);
          background:var(--surface);color:var(--text-2);font-size:12px;
          font-weight:600;font-family:var(--font);cursor:pointer;
        ">Annuler</button>
        <button id="confirm-modal-ok" style="
          padding:9px 20px;border-radius:8px;border:none;
          background:${isDanger ? '#DC2626' : '#2563EB'};
          color:white;font-size:12px;font-weight:700;
          font-family:var(--font);cursor:pointer;
          box-shadow:0 4px 12px rgba(${isDanger ? '220,38,38' : '37,99,235'},.3);
        ">${confirmLabel}</button>
      </div>
    </div>`;

  document.body.appendChild(overlay);

  overlay.querySelector('#confirm-modal-cancel').onclick = () => overlay.remove();
  overlay.querySelector('#confirm-modal-ok').onclick = () => {
    overlay.remove();
    onConfirm();
  };
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
}

window.showConfirmModal = showConfirmModal;

/* ═══════════════════════════════════════════════════
   MINI PLAYER & TRIM IN/OUT
═══════════════════════════════════════════════════ */

let _trimDragging = null;

function initMiniPlayer(seg) {
  const placeholder = document.getElementById('seg-mini-placeholder');
  const videoUrl = window.JOB_DATA?.video_url || '';

  // Arrêter loop précédent
  if (window._miniPlayerLoop) {
    cancelAnimationFrame(window._miniPlayerLoop);
    window._miniPlayerLoop = null;
  }

  const inMs = seg.trim_start_ms > 0 ? seg.trim_start_ms : seg.start_ms;
  const outMs = seg.trim_end_ms > 0 ? seg.trim_end_ms : seg.end_ms;

  // Supprimer img miniature si présente
  const oldThumb = document.getElementById('seg-mini-thumb');
  if (oldThumb) oldThumb.remove();
  const oldCanvas = document.getElementById('seg-mini-canvas');
  if (oldCanvas) oldCanvas.remove();

  // Créer ou récupérer l'élément vidéo
  let video = document.getElementById('seg-mini-video');
  if (!video) {
    video = document.createElement('video');
    video.id = 'seg-mini-video';
    video.muted = false;
    video.style.cssText = 'width:100%;max-height:140px;object-fit:cover;display:block;background:#000;';
    const player = document.getElementById('seg-mini-player');
    if (player) player.insertBefore(video, player.firstChild);
  }

  if (videoUrl) {
    video.src = videoUrl;
    video.style.display = 'block';
    if (placeholder) placeholder.style.display = 'none';

    const doSeek = () => {
      video.pause(); // s'assurer qu'on est bien en pause avant de seeker
      requestAnimationFrame(() => {
        video.currentTime = inMs / 1000;
      });
    };

    if (video.readyState >= 1) {
      doSeek();
    } else {
      video.addEventListener('loadedmetadata', doSeek, { once: true });
    }

    // Arrêter au point OUT
    video.ontimeupdate = () => {
      const s = transcribeState.segments[transcribeState.selectedIdx];
      if (!s) return;
      const sOut = s.trim_end_ms > 0 ? s.trim_end_ms : s.end_ms;

      if (!video.paused && video.currentTime * 1000 >= sOut) {
        // Stocker le currentTime avant pause pour éviter la race
        const inMs = (s.trim_start_ms > 0 ? s.trim_start_ms : s.start_ms) / 1000;
        video.pause();
        // Setter currentTime après pause, pas avant
        requestAnimationFrame(() => {
          video.currentTime = inMs;
          updateMiniPlayBtn(false);
        });
      }

      updateMiniTc(video.currentTime * 1000, s);
      updateTrimCursor(video.currentTime * 1000, s);
    };
  } else {
    video.style.display = 'none';
    if (placeholder) placeholder.style.display = 'flex';
  }

  initTrimBar(seg);
}
function seekMiniVideo(ms) {
  const video = document.getElementById('seg-mini-video');
  if (!video) return;
  const t = ms / 1000;
  if (!isFinite(t) || t < 0) return;
  video.currentTime = t;
  const seg = transcribeState.segments[transcribeState.selectedIdx];
  if (seg) {
    updateTrimCursor(ms, seg);
    updateMiniTc(ms, seg);
  }
}

function updateMiniPlayBtn(playing) {
  const btn = document.getElementById('seg-mini-play');
  if (!btn) return;
  btn.innerHTML = playing
    ? `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
        <rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>
       </svg>`
    : `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
        <polygon points="5 3 19 12 5 21"/>
       </svg>`;
}

function toggleMiniPlay() {
  const video = document.getElementById('seg-mini-video');
  const seg = transcribeState.segments[transcribeState.selectedIdx];
  if (!video || !seg) return;

  if (video.paused) {
    const inMs = seg.trim_start_ms > 0 ? seg.trim_start_ms : seg.start_ms;
    const outMs = seg.trim_end_ms > 0 ? seg.trim_end_ms : seg.end_ms;
    if (video.currentTime * 1000 < inMs || video.currentTime * 1000 >= outMs) {
      video.currentTime = inMs / 1000;
    }
    const playPromise = video.play();
    if (playPromise !== undefined) {
      playPromise
        .then(() => updateMiniPlayBtn(true))
        .catch(err => {
          // AbortError = interrompu avant de démarrer, on ignore
          if (err.name !== 'AbortError') console.warn('play() error:', err);
        });
    }
  } else {
    video.pause();
    updateMiniPlayBtn(false);
  }
}

function updateMiniTc(currentMs, seg) {
  const tc = document.getElementById('seg-mini-tc');
  if (!tc) return;
  const inMs = seg.trim_start_ms > 0 ? seg.trim_start_ms : seg.start_ms;
  const outMs = seg.trim_end_ms > 0 ? seg.trim_end_ms : seg.end_ms;
  tc.textContent = `${msToTC(currentMs - inMs)} / ${msToTC(outMs - inMs)}`;
}

function initTrimBar(seg) {
  const wrap = document.getElementById('seg-trim-bar-wrap');
  if (!wrap) return;

  const totalMs = seg.end_ms - seg.start_ms;
  const inMs = seg.trim_start_ms > 0 ? seg.trim_start_ms : seg.start_ms;
  const outMs = seg.trim_end_ms > 0 ? seg.trim_end_ms : seg.end_ms;

  updateTrimUI(seg, inMs, outMs, totalMs);

  // Clic direct sur la barre pour positionner le curseur
  wrap.onclick = (e) => {
    if (_trimDragging) return;
    if (e.target.closest('.seg-trim-handle')) return;
    const currentSeg = transcribeState.segments[transcribeState.selectedIdx];
    if (!currentSeg) return;
    const currentTotal = currentSeg.end_ms - currentSeg.start_ms;
    const rect = wrap.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const clickMs = currentSeg.start_ms + Math.round(pct * currentTotal);
    const video = document.getElementById('seg-mini-video');
    if (video) {
      seekMiniVideo(clickMs);
      updateTrimCursor(clickMs, currentSeg);
      updateMiniTc(clickMs, currentSeg);
    }
  };

  const handleIn = document.getElementById('seg-trim-in');
  const handleOut = document.getElementById('seg-trim-out');
  if (handleIn) handleIn.onmousedown = (e) => startTrimDrag(e, 'in');
  if (handleOut) handleOut.onmousedown = (e) => startTrimDrag(e, 'out');
}

function updateTrimUI(seg, inMs, outMs, totalMs) {
  const inPct = ((inMs - seg.start_ms) / totalMs) * 100;
  const outPct = ((outMs - seg.start_ms) / totalMs) * 100;

  const cutLeft = document.getElementById('seg-trim-cut-left');
  const active = document.getElementById('seg-trim-active');
  const cutRight = document.getElementById('seg-trim-cut-right');
  const handleIn = document.getElementById('seg-trim-in');
  const handleOut = document.getElementById('seg-trim-out');
  const inTc = document.getElementById('seg-trim-in-tc');
  const outTc = document.getElementById('seg-trim-out-tc');

  if (cutLeft) { cutLeft.style.left = '0'; cutLeft.style.width = `${inPct}%`; }
  if (active) { active.style.left = `${inPct}%`; active.style.width = `${outPct - inPct}%`; }
  if (cutRight) { cutRight.style.right = '0'; cutRight.style.width = `${100 - outPct}%`; }
  if (handleIn) handleIn.style.left = `${inPct}%`;
  if (handleOut) handleOut.style.left = `${outPct}%`;
  if (inTc) inTc.textContent = msToTC(inMs - seg.start_ms);
  if (outTc) outTc.textContent = msToTC(outMs - seg.start_ms);
}

function updateTrimCursor(currentMs, seg) {
  const cursor = document.getElementById('seg-trim-cursor');
  const totalMs = seg.end_ms - seg.start_ms;
  if (!cursor || !totalMs) return;
  const pct = Math.max(0, Math.min(100, ((currentMs - seg.start_ms) / totalMs) * 100));
  cursor.style.left = `${pct}%`;
}

function startTrimDrag(e, handle) {
  e.preventDefault();
  _trimDragging = handle;

  const onMove = (ev) => {
    const seg = transcribeState.segments[transcribeState.selectedIdx];
    const wrap = document.getElementById('seg-trim-bar-wrap');
    if (!seg || !wrap) return;

    const rect = wrap.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
    const totalMs = seg.end_ms - seg.start_ms;
    const ms = seg.start_ms + Math.round(pct * totalMs);

    let inMs = seg.trim_start_ms > 0 ? seg.trim_start_ms : seg.start_ms;
    let outMs = seg.trim_end_ms > 0 ? seg.trim_end_ms : seg.end_ms;

    const nbMots = (seg.text || '').trim().split(/\s+/).filter(Boolean).length;
    const minDur = Math.max(500, Math.ceil(nbMots / (transcribeState.wpm / 60 * 4) * 1000));

    if (handle === 'in') {
      inMs = Math.max(seg.start_ms, Math.min(ms, outMs - minDur));
      seg.trim_start_ms = inMs;
    } else {
      outMs = Math.min(seg.end_ms, Math.max(ms, inMs + minDur));
      seg.trim_end_ms = outMs;
    }

    updateTrimUI(seg, inMs, outMs, totalMs);

    const video = document.getElementById('seg-mini-video');
    if (video) {
      const t = (handle === 'in' ? inMs : outMs) / 1000;
      if (isFinite(t) && t >= 0) video.currentTime = t;
    }
  };

  const onUp = () => {
    _trimDragging = null;
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    saveTrim();
  };

  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
}

async function saveTrim() {
  const idx = transcribeState.selectedIdx;
  const seg = transcribeState.segments[idx];
  const jobId = document.getElementById('current-job-id')?.value;
  const csrf = document.getElementById('csrf-token')?.value || '';
  if (!seg || !jobId) return;

  const inMs = seg.trim_start_ms > 0 ? seg.trim_start_ms : seg.start_ms;
  const outMs = seg.trim_end_ms > 0 ? seg.trim_end_ms : seg.end_ms;

  try {
    await fetch(`/api/jobs/${jobId}/segments/${seg.index}/set-trim/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({ trim_start_ms: inMs, trim_end_ms: outMs }),
    });
    window.Toast?.success(`Trim : ${msToTC(inMs - seg.start_ms)} → ${msToTC(outMs - seg.start_ms)}`);
    const effDur = outMs - inMs;
    const maxMots = calcMaxMots(effDur, transcribeState.wpm, 0.25);
    updateWordBudget(seg.text, maxMots);
  } catch {
    window.Toast?.error('Erreur lors de la sauvegarde du trim.');
  }
}

function resetTrim() {
  const idx = transcribeState.selectedIdx;
  const seg = transcribeState.segments[idx];
  if (!seg) return;
  seg.trim_start_ms = seg.start_ms;
  seg.trim_end_ms = seg.end_ms;
  initTrimBar(seg);
  saveTrim();
}

function cutAtMiniPlayer() {
  const video = document.getElementById('seg-mini-video');
  const idx = transcribeState.selectedIdx;
  if (!video || idx === null) return;

  const seg = transcribeState.segments[idx];
  const cutMs = Math.round(video.currentTime * 1000);
  const inMs = seg.trim_start_ms > 0 ? seg.trim_start_ms : seg.start_ms;
  const outMs = seg.trim_end_ms > 0 ? seg.trim_end_ms : seg.end_ms;

  // Le cut doit être dans la zone active
  if (cutMs <= inMs + 200 || cutMs >= outMs - 200) {
    window.Toast?.warn('Placez le curseur au moins 200ms à l\'intérieur du segment.');
    return;
  }

  // Couper au timecode exact
  cutSegmentAt(idx, cutMs);
}

window.cutAtMiniPlayer = cutAtMiniPlayer;

function pushUndo(label) {
  const snapshot = JSON.parse(JSON.stringify(transcribeState.segments));
  transcribeState.undoStack.push({ segments: snapshot, label });
  if (transcribeState.undoStack.length > 50) transcribeState.undoStack.shift();
  transcribeState.redoStack = [];
}

function undoSegment() {
  if (!transcribeState.undoStack.length) {
    window.Toast?.info('Rien à annuler.');
    return;
  }
  const snap = transcribeState.undoStack.pop();
  transcribeState.redoStack.push({
    segments: JSON.parse(JSON.stringify(transcribeState.segments)),
    label: snap.label,
  });
  transcribeState.segments = snap.segments;
  renderTimeline();
  window.Toast?.info(`Annulé : ${snap.label}`);
}

function redoSegment() {
  if (!transcribeState.redoStack.length) {
    window.Toast?.info('Rien à rétablir.');
    return;
  }
  const snap = transcribeState.redoStack.pop();
  transcribeState.undoStack.push({
    segments: JSON.parse(JSON.stringify(transcribeState.segments)),
    label: snap.label,
  });
  transcribeState.segments = snap.segments;
  renderTimeline();
  window.Toast?.info(`Rétabli : ${snap.label}`);
}

/* ═══════════════════════════════════════════════════
   ZOOM
═══════════════════════════════════════════════════ */

function zoomTimeline(dir) {
  // Zoom réel : 2.0 → 3.0 → 4.0 → 6.0 → 8.0
  // Label affiché : 50% → 75% → 100% → 150% → 200%
  const levels = [2.0, 3.0, 4.0, 6.0, 8.0];
  const labels = ['50%', '75%', '100%', '150%', '200%'];
  const cur = levels.indexOf(transcribeState.zoomLevel);
  const idx = cur === -1 ? 2 : cur;
  const next = Math.max(0, Math.min(levels.length - 1, idx + dir));
  transcribeState.zoomLevel = levels[next];
  if (TDOM.zoomLabel) TDOM.zoomLabel.textContent = labels[next];
  renderTimeline();
}

/* ═══════════════════════════════════════════════════
   UTILITAIRES
═══════════════════════════════════════════════════ */

function reindexSegments() {
  transcribeState.segments.forEach((s, i) => { s.index = i; });
}

/* ═══════════════════════════════════════════════════
   RACCOURCIS CLAVIER
═══════════════════════════════════════════════════ */

document.addEventListener('keydown', (e) => {
  const tag = e.target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;

  if ((e.ctrlKey || e.metaKey) && e.key === 'z') { e.preventDefault(); undoSegment(); }
  if ((e.ctrlKey || e.metaKey) && e.key === 'y') { e.preventDefault(); redoSegment(); }
  if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveAllSegments(); }
  if (e.key === 'Delete' || e.key === 'Backspace') { confirmDeleteSegment(); }
  if (e.key === 'm' || e.key === 'M') { mergeWithNext(); }
});

/* ═══════════════════════════════════════════════════
   ÉVÉNEMENTS
═══════════════════════════════════════════════════ */

if (TDOM.btnTranscribe) {
  TDOM.btnTranscribe.addEventListener('click', startTranscription);
}

// Zoom molette sur la timeline
if (TDOM.timelineSegments) {
  TDOM.timelineSegments.addEventListener('wheel', (e) => {
    if (e.ctrlKey) {
      e.preventDefault();
      zoomTimeline(e.deltaY < 0 ? 1 : -1);
    }
  }, { passive: false });
}

/* ═══════════════════════════════════════════════════
   INIT
═══════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', async () => {
  buildVoiceGrid();

  // Charger segments si job déjà transcrit
  if (window._pendingLoadSegments) {
    const jobId = window._pendingLoadSegments;
    window._pendingLoadSegments = null;
    await loadSegments(jobId);
  }

  // Marquer étape 2 done
  if (window._pendingMarkStep2Done) {
    window._pendingMarkStep2Done = false;
    markStep2Done();
  }
});

// Exposer globalement
window.undoSegment = undoSegment;
window.redoSegment = redoSegment;
window.zoomTimeline = zoomTimeline;
window.mergeWithNext = mergeWithNext;
window.cutSegmentAt = cutSegmentAt;
window.confirmDeleteSegment = confirmDeleteSegment;
window.deleteSegment = deleteSegment;
window.splitSegmentAtCursor = cutSegmentAt;
window.resetSpeedFactor = resetSpeedFactor;
window.saveAllSegments = saveAllSegments;
window.saveCurrentSegment = saveCurrentSegment;
window.importScript = importScript;
window.loadSegments = loadSegments;
window.markStep2Done = markStep2Done;
window.drawWaveform = drawWaveform;

/* ═══════════════════════════════════════════════════
   DRAG RESIZE — REDIMENSIONNEMENT DES SEGMENTS
═══════════════════════════════════════════════════ */

const MIN_SEG_MS = 300;
let _dragState = null;

function startResize(e, idx, side, innerW, totalMs) {
  e.preventDefault();
  e.stopPropagation();
  const seg = transcribeState.segments[idx];
  if (!seg) return;
  pushUndo('Redimensionner segment');
  _dragState = {
    idx, side, innerW, totalMs,
    startX: e.clientX,
    origStart: seg.start_ms,
    origEnd: seg.end_ms,
  };
  document.addEventListener('mousemove', onResizeMove);
  document.addEventListener('mouseup', onResizeEnd);
  document.body.style.cursor = 'ew-resize';
  document.body.style.userSelect = 'none';
}

function onResizeMove(e) {
  if (!_dragState) return;
  const { idx, side, innerW, totalMs, startX, origStart, origEnd } = _dragState;
  const dx = e.clientX - startX;
  const dMs = Math.round((dx / innerW) * totalMs);
  const seg = transcribeState.segments[idx];
  const prevSeg = [...transcribeState.segments].slice(0, idx).reverse().find(s => !s.deleted);
  const nextAlive = transcribeState.segments.slice(idx + 1).find(s => !s.deleted);

  if (!seg) return;

  function effDurMs(s) {
    if (!s) return 0;
    const eStart = s.trim_start_ms > 0 ? s.trim_start_ms : s.start_ms;
    const eEnd = s.trim_end_ms > 0 ? s.trim_end_ms : s.end_ms;
    return eEnd - eStart;
  }
  function minDurMs(s) {
    if (!s) return MIN_SEG_MS;
    const nbMots = (s.text || '').trim().split(/\s+/).filter(Boolean).length;
    if (nbMots === 0) return MIN_SEG_MS;
    return Math.max(MIN_SEG_MS, Math.round((nbMots / transcribeState.wpm) * 60 * 1000 / 4.0));
  }
  function maxDurMs(s) {
    if (!s) return Infinity;
    const nbMots = (s.text || '').trim().split(/\s+/).filter(Boolean).length;
    if (nbMots === 0) return Infinity;
    return Math.round((nbMots / transcribeState.wpm) * 60 * 1000 / 0.25);
  }

  if (side === 'right') {
    // Trouver le prochain segment VIVANT (pas supprimé) comme vraie limite
    const nextAlive = transcribeState.segments.slice(idx + 1).find(s => !s.deleted);

    const hardStop = nextAlive
      ? nextAlive.end_ms - minDurMs(nextAlive)
      : origEnd + 60000;

    const maxEnd = Math.min(hardStop, seg.start_ms + maxDurMs(seg));
    const minEnd = seg.start_ms + minDurMs(seg);
    const cappedEnd = Math.max(minEnd, Math.min(maxEnd, origEnd + dMs));

    seg.end_ms = cappedEnd;
    if (seg.trim_end_ms === 0 || seg.trim_end_ms >= origEnd) {
      seg.trim_end_ms = cappedEnd;
    } else {
      seg.trim_end_ms = Math.min(seg.trim_end_ms, cappedEnd);
    }

    // Les supprimés entre idx et nextAlive : ajuster leur start_ms si absorbés
    for (let j = idx + 1; j < transcribeState.segments.length; j++) {
      const s = transcribeState.segments[j];
      if (!s.deleted) break;
      if (cappedEnd >= s.end_ms) {
        // Totalement absorbé dans le segment vivant
        s.start_ms = cappedEnd;
        s.end_ms = cappedEnd;
      } else if (cappedEnd > s.start_ms) {
        // Partiellement absorbé
        s.start_ms = cappedEnd;
      }
    }

    // Pousser nextAlive seulement si on empiète dessus
    if (nextAlive && cappedEnd > nextAlive.start_ms) {
      nextAlive.start_ms = cappedEnd;
      if (nextAlive.trim_start_ms === 0 || nextAlive.trim_start_ms <= nextAlive.start_ms) {
        nextAlive.trim_start_ms = cappedEnd;
      }
    }
  } else { // side === 'left'
    const prevAlive = [...transcribeState.segments].slice(0, idx).reverse().find(s => !s.deleted);

    const minStart = prevAlive
      ? prevAlive.start_ms + minDurMs(prevAlive)
      : 0;

    const maxStart = Math.min(
      seg.end_ms - minDurMs(seg),
      seg.end_ms - 0 // pas de maxDurMs par la gauche ici
    );
    const cappedStart = Math.max(
      Math.max(minStart, seg.end_ms - maxDurMs(seg)),
      Math.min(seg.end_ms - minDurMs(seg), origStart + dMs)
    );

    seg.start_ms = cappedStart;
    if (seg.trim_start_ms === 0 || seg.trim_start_ms <= origStart) {
      seg.trim_start_ms = cappedStart;
    } else {
      seg.trim_start_ms = Math.max(seg.trim_start_ms, cappedStart);
    }

    // Absorber les supprimés entre prevAlive et idx
    for (let j = idx - 1; j >= 0; j--) {
      const s = transcribeState.segments[j];
      if (!s.deleted) break;
      if (cappedStart <= s.start_ms) {
        s.start_ms = cappedStart;
        s.end_ms = cappedStart;
      } else if (cappedStart < s.end_ms) {
        s.end_ms = cappedStart;
      }
    }

    if (prevAlive && cappedStart < prevAlive.end_ms) {
      prevAlive.end_ms = cappedStart;
      if (prevAlive.trim_end_ms === 0 || prevAlive.trim_end_ms >= prevAlive.end_ms) {
        prevAlive.trim_end_ms = cappedStart;
      }
    }
  }

  // Recalculer speed_factor
  [seg, prevSeg, nextAlive].forEach(s => {
    if (!s || s.speed_forced || s.deleted) return;
    const effStart = s.trim_start_ms > 0 ? s.trim_start_ms : s.start_ms;
    const effEnd = s.trim_end_ms > 0 ? s.trim_end_ms : s.end_ms;
    s.speed_factor = calcSpeedFactor(s.text, effEnd - effStart, transcribeState.wpm);
  });

  // Mise à jour panneau segment sélectionné
  const selIdx = transcribeState.selectedIdx;
  const selSeg = selIdx !== null ? transcribeState.segments[selIdx] : null;
  if (selSeg && !selSeg.deleted) {
    const effStart = selSeg.trim_start_ms > 0 ? selSeg.trim_start_ms : selSeg.start_ms;
    const effEnd = selSeg.trim_end_ms > 0 ? selSeg.trim_end_ms : selSeg.end_ms;
    const effDur = effEnd - effStart;
    const autoFactor = calcSpeedFactor(selSeg.text, effDur, transcribeState.wpm);
    const maxMots = calcMaxMots(effDur, transcribeState.wpm, 0.25);
    updateSpeedUI(selSeg.speed_factor, selSeg.text, effDur, selSeg.speed_forced, autoFactor);
    updateWordBudget(selSeg.text, maxMots);
    if (TDOM.speedSlider) {
      TDOM.speedSlider.max = Math.max(autoFactor, 0.25).toFixed(2);
      TDOM.speedSlider.value = autoFactor.toFixed(2);
    }
    if (TDOM.speedSliderVal) {
      TDOM.speedSliderVal.textContent = `x${autoFactor.toFixed(2)}`;
    }
    initTrimBar(selSeg);
  }

  clearTimeout(_dragState?._timer);
  if (_dragState) {
    _dragState._timer = setTimeout(() => renderTimeline(), 30);
  }
}

function onResizeEnd() {
  if (_dragState) {
    clearTimeout(_dragState._timer);
    const idx = _dragState.idx;
    renderTimeline();

    // Mettre à jour le panneau segment sans réinitialiser le mini player
    if (transcribeState.selectedIdx !== null) {
      const seg = transcribeState.segments[transcribeState.selectedIdx];
      if (seg) {
        const effStart = seg.trim_start_ms > 0 ? seg.trim_start_ms : seg.start_ms;
        const effEnd = seg.trim_end_ms > 0 ? seg.trim_end_ms : seg.end_ms;
        const effDur = effEnd - effStart;
        const autoFactor = calcSpeedFactor(seg.text, effDur, transcribeState.wpm);
        const maxMots = calcMaxMots(effDur, transcribeState.wpm, 0.25);
        updateSpeedUI(seg.speed_factor, seg.text, effDur, seg.speed_forced, autoFactor);
        updateWordBudget(seg.text, maxMots);
        if (TDOM.speedSlider) {
          TDOM.speedSlider.max = Math.max(autoFactor, 0.25).toFixed(2);
          TDOM.speedSlider.value = autoFactor.toFixed(2);
        }
        if (TDOM.speedSliderVal) {
          TDOM.speedSliderVal.textContent = `x${autoFactor.toFixed(2)}`;
        }
        renderEditableTc(transcribeState.selectedIdx);
        initTrimBar(seg);
        markSegmentModified(transcribeState.selectedIdx);
      }
    }
  }
  _dragState = null;
  document.removeEventListener('mousemove', onResizeMove);
  document.removeEventListener('mouseup', onResizeEnd);
  document.body.style.cursor = '';
  document.body.style.userSelect = '';
  transcribeState.dirty = true;
}

/* ═══════════════════════════════════════════════════
   SAUVEGARDE
═══════════════════════════════════════════════════ */

async function saveCurrentSegment() {
  const idx = transcribeState.selectedIdx;
  if (idx === null) { window.Toast?.warn('Aucun segment sélectionné.'); return; }

  const seg = transcribeState.segments[idx];
  if (!seg?.id) { window.Toast?.warn('Ce segment n\'a pas encore d\'ID — sauvegardez tout.'); return; }

  // Guard contre double clic
  const btnSave = document.getElementById('btn-save-segment');
  if (btnSave?.dataset.saving === '1') return;
  if (btnSave) btnSave.dataset.saving = '1';

  const jobId = document.getElementById('current-job-id')?.value;
  const csrf = document.getElementById('csrf-token')?.value || '';

  try {
    const res = await fetch(`/api/jobs/${jobId}/segments/${seg.id}/save/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({
        text: seg.text,
        speed_factor: seg.speed_factor,
        speed_forced: seg.speed_forced,
        start_ms: seg.start_ms,
        end_ms: seg.end_ms,
      }),
    });
    if (res.ok) {
      const segId = String(transcribeState.segments[idx]?.id);
      transcribeState.modifiedSegments.add(segId);
      transcribeState.unsavedSegments.delete(segId);
      window.Toast?.success(`Segment ${idx + 1} sauvegardé.`);
      markSegmentSaved(idx);
      updateUnsavedBadge();
    } else {
      const d = await res.json();
      window.Toast?.error(d.error || 'Erreur sauvegarde.');
    }
  } catch (e) {
    window.Toast?.error('Erreur réseau lors de la sauvegarde.');
  } finally {
    if (btnSave) btnSave.dataset.saving = '0';
  }
}

async function saveAllSegments() {
  const jobId = document.getElementById('current-job-id')?.value;
  const csrf = document.getElementById('csrf-token')?.value || '';

  if (!jobId || !transcribeState.segments.length) {
    window.Toast?.warn('Aucun segment à sauvegarder.');
    return;
  }

  const segsToSave = transcribeState.segments
    .filter(s => s.id && !s.deleted)
    .map(s => ({
      id: s.id,
      index: s.index,
      text: s.text,
      speed_factor: s.speed_factor,
      speed_forced: s.speed_forced,
      start_ms: s.start_ms,
      end_ms: s.end_ms,
    }));

  console.log('save-all: envoi de', segsToSave.length, 'segments, IDs:', segsToSave.map(s => s.id));

  try {
    const res = await fetch(`/api/jobs/${jobId}/segments/save-all/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({ segments: segsToSave }),
    });
    const data = await res.json();
    if (res.ok) {
      transcribeState.dirty = false;
      transcribeState.unsavedSegments.clear();
      updateUnsavedBadge();
      window.Toast?.success(`${data.updated} segment(s) sauvegardés.`);
      if (data.errors?.length) {
        window.Toast?.warn(`${data.errors.length} erreur(s) : ${data.errors[0]}`);
      }
    } else {
      window.Toast?.error(data.error || 'Erreur lors de la sauvegarde.');
    }
  } catch (e) {
    window.Toast?.error('Erreur réseau lors de la sauvegarde.');
  }
}

function markSegmentSaved(idx) {
  const block = TDOM.timelineSegments?.querySelector(`.tl-segment[data-idx="${idx}"]`);
  if (block) block.classList.remove('unsaved');
}

/* ═══════════════════════════════════════════════════
   IMPORT SCRIPT — avec prévisualisation
═══════════════════════════════════════════════════ */

let _importBlocs = []; // blocs parsés en attente de confirmation

function importScript(input) {
  const file = input?.files?.[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = e => {
    const content = e.target.result;
    const blocs = parseScriptFile(content);

    if (blocs === null) {
      // Format non reconnu — ouvrir une modale d'erreur claire
      openFormatErrorModal();
      input.value = '';
      return;
    }

    if (!blocs.length) {
      window.Toast?.error('Aucun texte détecté dans ce fichier. Exportez le template pour voir le bon format.');
      input.value = '';
      return;
    }

    const segs = transcribeState.segments;
    const nbSegs = segs.length;
    const nbBlocs = blocs.length;

    // Préparer les données avec coupe auto et silences
    const preview = [];
    const warnings = [];

    for (let i = 0; i < nbSegs; i++) {
      const seg = segs[i];
      const durMs = seg.end_ms - seg.start_ms;
      const maxMots = calcMaxMots(durMs, transcribeState.wpm, 0.25);

      if (i >= nbBlocs) {
        // Pas de texte → silence
        preview.push({ seg, texte: '', texteOriginal: '', coupe: false, silence: true, maxMots });
        warnings.push(`Segment ${i + 1} sera mis en silence (pas de texte dans le fichier).`);
      } else {
        let texte = blocs[i].trim();
        const mots = texte.split(/\s+/).filter(Boolean);

        if (mots.length > maxMots) {
          // Couper automatiquement
          const texteCoupé = mots.slice(0, maxMots).join(' ');
          const texteSupprimé = mots.slice(maxMots).join(' ');
          preview.push({ seg, texte: texteCoupé, texteOriginal: texte, coupe: true, silence: false, maxMots, texteSupprimé });
          warnings.push(`Segment ${i + 1} : "${texteSupprimé}" a été retiré (dépassait le budget de ${maxMots} mots).`);
        } else {
          preview.push({ seg, texte, texteOriginal: texte, coupe: false, silence: false, maxMots });
        }
      }
    }

    if (nbBlocs > nbSegs) {
      warnings.push(`${nbBlocs - nbSegs} ligne(s) ignorée(s) car le projet n'a que ${nbSegs} segments.`);
    }

    _importBlocs = preview.map(p => p.texte);
    _importPreview = preview;
    openImportModal(preview, warnings);
    input.value = '';
  };
  reader.readAsText(file, 'utf-8');
}

let _importPreview = [];

/* ═══════════════════════════════════════════════════
   EXPORT TEMPLATE
═══════════════════════════════════════════════════ */

function exportTemplate() {
  const segs = transcribeState.segments;
  if (!segs.length) {
    window.Toast?.error('Pas de segments à exporter. Transcrivez d\'abord la vidéo.');
    return;
  }

  const lines = segs.map((seg, i) => {
    const durMs = seg.end_ms - seg.start_ms;
    const maxMots = calcMaxMots(durMs, transcribeState.wpm, 0.25);
    const tc = `${msToTC(seg.start_ms)} → ${msToTC(seg.end_ms)}`;
    const texte = seg.text || '';
    return `[${i + 1}] (max ${maxMots} mots | ${tc})\n${texte || '(silence)'}`;
  });

  const content = lines.join('\n\n');
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'script_template.txt';
  a.click();
  URL.revokeObjectURL(url);
  window.Toast?.success('Template exporté. Modifiez-le et réimportez-le.');
}

window.exportTemplate = exportTemplate;

function parseScriptFile(content) {
  content = content.trim();

  // Format template TutoBuilder : [N] (max X mots | TC)\ntexte
  if (content.match(/^\[(\d+)\]\s*\(max \d+ mots/m)) {
    const blocs = [];
    const sections = content.split(/\n\n+/);
    for (const section of sections) {
      const lignes = section.trim().split('\n');
      const texte = lignes
        .filter(l => !l.match(/^\[\d+\]\s*\(max \d+ mots/))
        .join(' ')
        .trim();
      blocs.push(texte === '(silence)' ? '' : texte);
    }
    return blocs;
  }

  // Format SRT/VTT avec timecodes -->
  if (content.includes('-->')) {
    const blocs = [];
    for (const bloc of content.split(/\n\n+/)) {
      const lignes = bloc.trim().split('\n').filter(l => l.trim());
      const texte = lignes
        .filter(l => !l.match(/^\d+$/) && !l.includes('-->'))
        .join(' ').trim();
      if (texte) blocs.push(texte);
    }
    return blocs;
  }

  // Format inconnu — retourner null pour bloquer
  return null;
}

function openFormatErrorModal() {
  const modal = document.getElementById('modal-import');
  if (!modal) return;

  modal.innerHTML = `
    <div class="im-backdrop" onclick="closeImportModal()"></div>
    <div class="im-panel" style="max-width:480px">
      <div class="im-header">
        <div class="im-header-left">
          <div class="im-icon" style="background:linear-gradient(135deg,#ef4444,#dc2626)">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"/>
              <line x1="15" y1="9" x2="9" y2="15"/>
              <line x1="9" y1="9" x2="15" y2="15"/>
            </svg>
          </div>
          <div>
            <div class="im-title">Format non reconnu</div>
            <div class="im-subtitle">Ce fichier ne peut pas être importé</div>
          </div>
        </div>
        <button class="im-close" onclick="closeImportModal()">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>

      <div style="padding:20px 24px;display:flex;flex-direction:column;gap:14px">
        <p style="font-size:13px;color:var(--text-1);line-height:1.6;margin:0">
          TutoBuilder accepte uniquement les fichiers exportés depuis le bouton <strong>Template</strong>.
          Votre fichier a un format différent et ne peut pas être traité automatiquement.
        </p>

        <div style="background:var(--surface-2);border:1px solid var(--border);border-radius:10px;padding:14px;font-size:11px;font-family:var(--font-mono);color:var(--text-2);line-height:1.8">
          <div style="color:var(--text-3);margin-bottom:6px;font-family:var(--font);font-weight:600;font-size:11px">Format attendu :</div>
          [1] (max 12 mots | 00:00 → 00:10)<br>
          Votre texte ici sur cette ligne<br>
          <br>
          [2] (max 18 mots | 00:10 → 00:20)<br>
          Votre texte ici sur cette ligne
        </div>

        <p style="font-size:12px;color:var(--text-3);margin:0;line-height:1.5">
          Cliquez sur <strong>Exporter le template</strong> dans la timeline pour obtenir le bon fichier,
          modifiez-le avec votre texte en respectant les budgets de mots, puis réimportez-le.
        </p>
      </div>

      <div class="im-footer">
        <button class="im-btn-cancel" onclick="closeImportModal()">Fermer</button>
        <button class="im-btn-confirm" onclick="closeImportModal();exportTemplate()">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="17 8 12 3 7 8"/>
            <line x1="12" y1="3" x2="12" y2="15"/>
          </svg>
          Exporter le template
        </button>
      </div>
    </div>`;

  modal.style.display = 'flex';
}

window.openFormatErrorModal = openFormatErrorModal;

function openImportModal(preview, warnings) {
  const modal = document.getElementById('modal-import');
  if (!modal) return;

  const nbCoupés = preview.filter(p => p.coupe).length;
  const nbSilences = preview.filter(p => p.silence).length;
  const nbOk = preview.filter(p => !p.coupe && !p.silence).length;

  // Header statut
  let statusHtml = '';
  if (nbCoupés === 0 && nbSilences === 0) {
    statusHtml = `<div class="im-status im-status-ok">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
      ${nbOk} segments prêts à importer
    </div>`;
  } else {
    statusHtml = `<div class="im-status im-status-warn">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      ${nbOk} OK &nbsp;·&nbsp; ${nbCoupés} coupé(s) &nbsp;·&nbsp; ${nbSilences} silence(s)
    </div>`;
  }

  // Avertissements
  let warningsHtml = '';
  if (warnings.length) {
    warningsHtml = `<div class="im-warnings">
      ${warnings.map(w => `<div class="im-warning-item">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        ${w}
      </div>`).join('')}
    </div>`;
  }

  // Tableau des segments
  const rowsHtml = preview.map((p, i) => {
    const pct = Math.min(100, Math.round((p.texte.split(/\s+/).filter(Boolean).length / p.maxMots) * 100));
    const barColor = p.silence ? '#94a3b8' : p.coupe ? '#f59e0b' : pct > 85 ? '#f59e0b' : '#10b981';

    let statusBadge = '';
    if (p.silence) statusBadge = `<span class="im-badge im-badge-silence">Silence</span>`;
    else if (p.coupe) statusBadge = `<span class="im-badge im-badge-warn">Coupé</span>`;
    else statusBadge = `<span class="im-badge im-badge-ok">OK</span>`;

    const nbMots = p.texte.split(/\s+/).filter(Boolean).length;

    return `<div class="im-row ${p.silence ? 'im-row-silence' : p.coupe ? 'im-row-warn' : ''}">
      <div class="im-row-header">
        <span class="im-seg-num">${i + 1}</span>
        <span class="im-seg-tc">${msToTC(p.seg.start_ms)} → ${msToTC(p.seg.end_ms)}</span>
        <span id="im-badge-${i}" class="im-badge ${p.silence ? 'im-badge-silence' : p.coupe ? 'im-badge-warn' : 'im-badge-ok'}">${p.silence ? 'Silence' : p.coupe ? 'Coupé' : 'OK'}</span>
        <span class="im-seg-budget" id="im-budget-${i}">${nbMots} / ${p.maxMots} mots</span>
      </div>
      <div class="im-bar-wrap">
        <div class="im-bar-fill" id="im-bar-${i}" style="width:${pct}%;background:${barColor}"></div>
      </div>
      <textarea class="im-seg-textarea" id="im-text-${i}" data-idx="${i}" data-max="${p.maxMots}"
        rows="2" placeholder="${p.silence ? '(silence — laissez vide)' : ''}">${p.texte}</textarea>
      ${p.coupe ? `<div class="im-seg-cut" id="im-cut-${i}">Retiré : "${p.texteSupprimé}"</div>` : ''}
    </div>`;
  }).join('');

  modal.innerHTML = `
    <div class="im-backdrop" onclick="closeImportModal()"></div>
    <div class="im-panel">

      <div class="im-header">
        <div class="im-header-left">
          <div class="im-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="17 8 12 3 7 8"/>
              <line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
          </div>
          <div>
            <div class="im-title">Prévisualisation de l'import</div>
            <div class="im-subtitle">${preview.length} segments analysés</div>
          </div>
        </div>
        <button class="im-close" onclick="closeImportModal()">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>

      ${statusHtml}
      ${warningsHtml}

      <div class="im-body">
        ${rowsHtml}
      </div>

      <div class="im-footer">
        <button class="im-btn-cancel" onclick="closeImportModal()">Annuler</button>
        <button class="im-btn-export" onclick="exportTemplate()">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
          Exporter le template
        </button>
        <button class="im-btn-confirm" onclick="confirmImport()">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="20 6 9 17 4 12"/>
          </svg>
          Confirmer l'import
        </button>
      </div>
    </div>`;

  modal.style.display = 'flex';

  // Event listeners sur les textareas éditables
  modal.querySelectorAll('.im-seg-textarea').forEach(ta => {
    ta.addEventListener('input', () => {
      const idx = parseInt(ta.dataset.idx);
      const maxMots = parseInt(ta.dataset.max);
      const words = ta.value.trim().split(/\s+/).filter(Boolean);
      const nbMots = words.length;

      // Couper automatiquement si dépasse
      if (nbMots > maxMots) {
        ta.value = words.slice(0, maxMots).join(' ');
      }

      // Mettre à jour preview en mémoire
      _importPreview[idx].texte = ta.value;

      // Mettre à jour barre, badge et budget
      const realMots = Math.min(nbMots, maxMots);
      const pct = Math.min(100, Math.round((realMots / maxMots) * 100));
      const barColor = pct > 95 ? '#f59e0b' : '#10b981';

      const bar = document.getElementById(`im-bar-${idx}`);
      const badge = document.getElementById(`im-badge-${idx}`);
      const budget = document.getElementById(`im-budget-${idx}`);

      if (bar) { bar.style.width = `${pct}%`; bar.style.background = barColor; }
      if (budget) budget.textContent = `${realMots} / ${maxMots} mots`;
      if (badge) {
        if (realMots === 0) {
          badge.className = 'im-badge im-badge-silence';
          badge.textContent = 'Silence';
        } else {
          badge.className = 'im-badge im-badge-ok';
          badge.textContent = 'OK';
        }
      }
    });
  });
}



function closeImportModal() {
  const modal = document.getElementById('modal-import');
  if (modal) modal.style.display = 'none';
  _importBlocs = [];
  _importPreview = [];
}

async function confirmImport() {
  const jobId = document.getElementById('current-job-id')?.value;
  const csrf = document.getElementById('csrf-token')?.value || '';
  if (!jobId || !_importPreview.length) return;

  const btn = document.querySelector('.im-btn-confirm');
  if (btn) { btn.disabled = true; btn.textContent = 'Import en cours...'; }

  const segs = transcribeState.segments;

  // Appliquer les textes depuis _importPreview
  _importPreview.forEach((p, i) => {
    const seg = segs[i];
    if (!seg) return;
    seg.text = p.texte;
    seg.speed_factor = calcSpeedFactor(p.texte, seg.end_ms - seg.start_ms, transcribeState.wpm);
    seg.speed_forced = false;
    markSegmentModified(i);
  });

  renderTimeline();
  closeImportModal();

  const nbCoupés = _importPreview.filter(p => p.coupe).length;
  const nbSilences = _importPreview.filter(p => p.silence).length;

  let msg = `Script importé sur ${_importPreview.length} segments.`;
  if (nbCoupés) msg += ` ${nbCoupés} coupé(s).`;
  if (nbSilences) msg += ` ${nbSilences} mis en silence.`;

  window.Toast?.success(msg);
  transcribeState.dirty = true;
}

window.closeImportModal = closeImportModal;
window.confirmImport = confirmImport;