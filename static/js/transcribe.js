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
  { id: 'narrateur_pro',  name: 'Narrateur Pro',  wpm: 145 },
  { id: 'narratrice_pro', name: 'Narratrice Pro', wpm: 140 },
  { id: 'expert',         name: 'Expert',         wpm: 138 },
  { id: 'experte',        name: 'Experte',        wpm: 142 },
  { id: 'guide',          name: 'Guide',          wpm: 148 },
  { id: 'pedagogique',    name: 'Pédagogique',    wpm: 135 },
];

const DEFAULT_WPM    = 145;
const MIN_SPEED      = 0.25;
const MAX_SPEED      = 4.0;
const POLL_INTERVAL  = 2500;

/* ═══════════════════════════════════════════════════
   ÉTAT
═══════════════════════════════════════════════════ */

const transcribeState = {
  segments:         [],
  selectedIdx:      null,
  undoStack:        [],
  redoStack:        [],
  dirty:            false,
  pollTimer:        null,
  zoomLevel:        4.0,
  selectedVoice:    VOICES[0],
  wpm:              DEFAULT_WPM,
  videoDuration:    0,
  waveformData:     [],
  modifiedSegments: new Set(), // IDs des segments modifiés depuis dernière synthèse
};

/* ═══════════════════════════════════════════════════
   DOM
═══════════════════════════════════════════════════ */

const TDOM = {
  wsec2:             document.getElementById('wsec-2'),
  btnTranscribe:     document.getElementById('btn-transcribe'),
  transcribeError:   document.getElementById('transcribe-error'),
  progressWrap:      document.getElementById('transcribe-progress-wrap'),
  progressLabel:     document.getElementById('transcribe-progress-label'),
  progressFill:      document.getElementById('transcribe-progress-fill'),
  voiceGrid:         document.getElementById('voice-grid'),
  selectEngine:      document.getElementById('select-tts-engine'),
  selectLanguage:    document.getElementById('select-language'),
  timelineWrap:      document.getElementById('timeline-wrap'),
  timelineSegments:  document.getElementById('timeline-segments'),
  timelineEmpty:     document.getElementById('timeline-empty'),
  timelineRuler:     document.getElementById('timeline-ruler'),
  waveformCanvas:    document.getElementById('waveform-canvas'),
  timelineCursor:    document.getElementById('timeline-cursor'),
  zoomLabel:         document.getElementById('tl-zoom-label'),
  segmentEditor:     document.getElementById('segment-editor'),
  segEditorIndex:    document.getElementById('seg-editor-index'),
  segEditorTc:       document.getElementById('seg-editor-tc'),
  segThumb:          document.getElementById('seg-thumb'),
  segThumbPlaceholder: document.getElementById('seg-thumb-placeholder'),
  segTextInput:      document.getElementById('seg-text-input'),
  speedValue:        document.getElementById('speed-value'),
  speedBar:          document.getElementById('speed-bar'),
  speedHint:         document.getElementById('speed-hint'),
  speedSlider:       document.getElementById('speed-slider'),
  speedSliderVal:    document.getElementById('speed-slider-val'),
};

/* ═══════════════════════════════════════════════════
   INIT VOIX
═══════════════════════════════════════════════════ */

function buildVoiceGrid() {
  if (!TDOM.voiceGrid) return;
  TDOM.voiceGrid.innerHTML = '';

  VOICES.forEach((v, i) => {
    const card = document.createElement('div');
    card.className = 'voice-card' + (i === 0 ? ' selected' : '');
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
  transcribeState.wpm           = voice.wpm;
  document.querySelectorAll('.voice-card').forEach(c => c.classList.remove('selected'));
  cardEl.classList.add('selected');

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

  const csrf     = document.getElementById('csrf-token')?.value || '';
  const language = TDOM.selectLanguage?.value || 'fr';

  TDOM.btnTranscribe.disabled = true;
  TDOM.progressWrap.style.display = 'block';
  setTranscribeProgress(10, 'Extraction audio…');
  window.Toast?.info('Transcription lancée…');

  try {
    const res = await fetch(`/api/jobs/${jobId}/transcribe/`, {
      method:  'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken':  csrf,
      },
      body: JSON.stringify({
        language:   language,
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

    } catch (_) {}
  }, POLL_INTERVAL);
}

/* ═══════════════════════════════════════════════════
   CHARGER LES SEGMENTS
═══════════════════════════════════════════════════ */

async function loadSegments(jobId) {
  try {
    const res  = await fetch(`/api/jobs/${jobId}/segments/`);
    const data = await res.json();
    const segs = Array.isArray(data) ? data : (data.results || []);

    transcribeState.segments = segs.map(s => ({
      id:           s.id,
      index:        s.index,
      start_ms:     s.start_ms,
      end_ms:       s.end_ms,
      text:         s.text || '',
      speed_factor: s.speed_factor || 1.0,
      speed_forced: s.speed_forced || false,
      thumb_url:    s.thumb_url || '',
      has_audio:    s.has_audio || false,
      deleted:      false,
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
    btn.style.cursor  = 'not-allowed';
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

function markSegmentModified(idx) {
  const seg = transcribeState.segments[idx];
  if (seg?.id) {
    transcribeState.modifiedSegments.add(String(seg.id));
    // Persister dans sessionStorage pour survivre au reload
    const jobId = document.getElementById('current-job-id')?.value;
    if (jobId) {
      const key = `modified_segs_${jobId}`;
      sessionStorage.setItem(key, JSON.stringify([...transcribeState.modifiedSegments]));
    }
  }
}

function clearModifiedSegments() {
  transcribeState.modifiedSegments.clear();
  const jobId = document.getElementById('current-job-id')?.value;
  if (jobId) sessionStorage.removeItem(`modified_segs_${jobId}`);
}

function restoreModifiedSegments() {
  const jobId = document.getElementById('current-job-id')?.value;
  if (!jobId) return;
  const key     = `modified_segs_${jobId}`;
  const stored  = sessionStorage.getItem(key);
  if (stored) {
    try {
      const ids = JSON.parse(stored);
      ids.forEach(id => transcribeState.modifiedSegments.add(id));
    } catch (_) {}
  }
}

window.getModifiedSegmentIds  = () => [...transcribeState.modifiedSegments];
window.clearModifiedSegments  = clearModifiedSegments;
window.restoreModifiedSegments = restoreModifiedSegments;

/* ═══════════════════════════════════════════════════
   CALCUL VITESSE
═══════════════════════════════════════════════════ */

function calcSpeedFactor(text, durationMs, wpm) {
  if (!text || !text.trim()) {
    // Silence — accélérer selon la durée
    const s = durationMs / 1000;
    if (s < 2)  return 1.0;
    if (s < 5)  return 2.0;
    if (s < 15) return 4.0;
    return 4.0;
  }
  const nbMots = text.trim().split(/\s+/).filter(Boolean).length;
  const voixS  = (nbMots / wpm) * 60;
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
  if (factor < 0.8)  return 'warn';  // ralenti significatif
  if (factor < 1.0)  return 'slow';  // légèrement ralenti
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
  if (!segs.length) {
    container.innerHTML = `<div class="timeline-empty"><p>La timeline apparaît après la transcription</p></div>`;
    return;
  }

  const totalMs = segs[segs.length - 1].end_ms;
  const zoom    = transcribeState.zoomLevel;

  const inner = document.createElement('div');
  inner.style.cssText = `
    position: relative;
    width: ${100 * zoom}%;
    min-width: ${100 * zoom}%;
    height: 100%;
  `;

  segs.forEach((seg, i) => {
    const durMs    = seg.end_ms - seg.start_ms;
    const leftPct  = (seg.start_ms / totalMs) * 100;
    const widthPct = Math.max(0.5, (durMs / totalMs) * 100);
    const sc       = speedClass(seg.speed_factor);
    const isSilence = !seg.text?.trim();
    const durS     = (durMs / 1000).toFixed(1);
    const tcStart  = msToTC(seg.start_ms);
    const tcEnd    = msToTC(seg.end_ms);

    const block = document.createElement('div');
    block.className  = 'tl-segment';
    block.dataset.idx = i;
    block.style.cssText = `
      position: absolute;
      left: ${leftPct}%;
      width: ${widthPct}%;
      top: 4px;
      bottom: 4px;
    `;

    if (seg.deleted)    block.classList.add('deleted');
    else if (isSilence) block.classList.add('silence');
    if (i === transcribeState.selectedIdx) block.classList.add('selected');

    block.innerHTML = `
      ${seg.thumb_url ? `<img class="tl-seg-thumb" src="${seg.thumb_url}" alt="" loading="lazy">` : ''}
      <div class="tl-seg-tc">${tcStart} → ${tcEnd}</div>
      <div class="tl-seg-text">${seg.text || '<em>Silence</em>'}</div>
      <span class="tl-seg-speed ${sc}">x${seg.speed_factor.toFixed(2)} · ${durS}s</span>
      <div class="tl-seg-actions">
        <button class="tl-seg-btn cut" title="Couper" onclick="cutSegmentAt(${i}, event)">
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="2" x2="12" y2="22"/></svg>
        </button>
        <button class="tl-seg-btn merge" title="Fusionner" onclick="mergeWithNext(${i})">
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 9l7 7 7-7"/></svg>
        </button>
        <button class="tl-seg-btn del" title="Supprimer" onclick="confirmDeleteSegment(${i})">
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
      <div class="tl-resize-handle tl-resize-left"  data-seg="${i}" data-side="left"></div>
      <div class="tl-resize-handle tl-resize-right" data-seg="${i}" data-side="right"></div>`;

    block.addEventListener('click', (e) => {
      if (e.target.closest('.tl-seg-btn') || e.target.closest('.tl-resize-handle')) return;
      selectSegment(i);
    });

    block.addEventListener('dblclick', (e) => {
      if (e.target.closest('.tl-seg-btn') || e.target.closest('.tl-resize-handle')) return;
      cutSegmentAt(i, e);
    });

    inner.appendChild(block);
  });

  container.innerHTML = '';
  container.appendChild(inner);

  // Poignées drag
  inner.querySelectorAll('.tl-resize-handle').forEach(handle => {
    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const idx  = parseInt(handle.dataset.seg);
      const side = handle.dataset.side;
      const totalMs2 = transcribeState.segments[transcribeState.segments.length - 1].end_ms;
      const innerW   = inner.offsetWidth || container.offsetWidth || 900;
      startResize(e, idx, side, innerW, totalMs2);
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

  const seg   = transcribeState.segments[idx];
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
  ruler.style.width    = `${widthPx}px`;

  const stepMs = totalMs > 60000 ? 10000 : totalMs > 30000 ? 5000 : 2000;
  for (let ms = 0; ms <= totalMs; ms += stepMs) {
    const pct  = ms / totalMs;
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
  return `${String(Math.floor(s / 60)).padStart(2,'0')}:${String(s % 60).padStart(2,'0')}`;
}

/* ═══════════════════════════════════════════════════
   WAVEFORM
═══════════════════════════════════════════════════ */

function drawWaveform(data) {
  const canvas = TDOM.waveformCanvas;
  if (!canvas) return;
  const W = canvas.offsetWidth || 800;
  const H = canvas.offsetHeight || 40;
  canvas.width  = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#0A0A0B';
  ctx.fillRect(0, 0, W, H);
  if (!data.length) return;

  const g = ctx.createLinearGradient(0, 0, W, 0);
  g.addColorStop(0,   'rgba(59,130,246,.5)');
  g.addColorStop(0.5, 'rgba(96,165,250,.7)');
  g.addColorStop(1,   'rgba(59,130,246,.5)');
  ctx.strokeStyle = g;
  ctx.lineWidth   = 1;

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
  if (vid && seg) vid.currentTime = seg.start_ms / 1000;
}

function loadSegmentEditor(idx) {
  const seg = transcribeState.segments[idx];
  if (!seg || !TDOM.segmentEditor) return;

  TDOM.segmentEditor.style.display = 'block';
  TDOM.segEditorIndex.textContent  = `Segment ${idx + 1}`;

  // Timecodes éditables
  renderEditableTc(idx);

  // Miniature
  if (seg.thumb_url) {
    TDOM.segThumb.src = seg.thumb_url;
    TDOM.segThumb.style.display = 'block';
    TDOM.segThumbPlaceholder.style.display = 'none';
  } else {
    TDOM.segThumb.style.display = 'none';
    TDOM.segThumbPlaceholder.style.display = 'flex';
  }

  // Bouton lecture audio
  const audioBtn = document.getElementById('seg-audio-btn');
  if (audioBtn) {
    audioBtn.style.display = seg.has_audio ? 'flex' : 'none';
  }

  const autoFactor = calcSpeedFactor(seg.text, seg.end_ms - seg.start_ms, transcribeState.wpm);

  // Budget mots — toujours calculé sur x0.25 (max possible) pour ne jamais bloquer
  // Le vrai ajustement se fait à l'export
  const maxMots = calcMaxMots(seg.end_ms - seg.start_ms, transcribeState.wpm, 0.25);

  // Texte
  TDOM.segTextInput.value = seg.text || '';
  TDOM.segTextInput.removeAttribute('maxLength');
  updateWordBudget(seg.text, maxMots);

  // Vitesse
  updateSpeedUI(seg.speed_factor, seg.text, seg.end_ms - seg.start_ms, seg.speed_forced, autoFactor);

  // Slider — min x0.25, max = speed_factor auto
  TDOM.speedSlider.min   = '0.25';
  TDOM.speedSlider.max   = Math.max(autoFactor, 0.25).toFixed(2);
  TDOM.speedSlider.value = seg.speed_forced
    ? Math.min(seg.speed_factor, autoFactor).toFixed(2)
    : autoFactor.toFixed(2);
  TDOM.speedSliderVal.textContent = `x${(+TDOM.speedSlider.value).toFixed(2)}`;
}

function calcMaxMots(durationMs, wpm, speedFactor) {
  const speed      = Math.max(0.25, Math.min(4.0, speedFactor || 1.0));
  const dureeVoixS = (durationMs / 1000) / speed;
  return Math.max(1, Math.floor(dureeVoixS * wpm / 60));
}

function updateWordBudget(text, maxMots) {
  const nbMots  = (text || '').trim().split(/\s+/).filter(Boolean).length;
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

  const seg     = transcribeState.segments[idx];
  const origStart = seg._origStart ?? seg.start_ms;
  const origEnd   = seg._origEnd   ?? seg.end_ms;

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
  const inputEnd   = document.getElementById('tc-end');

  inputStart.addEventListener('change', () => applyTcEdit(idx, 'start', inputStart.value));
  inputEnd.addEventListener('change',   () => applyTcEdit(idx, 'end',   inputEnd.value));

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
  const seg     = transcribeState.segments[idx];
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
  seg.end_ms   = seg._origEnd;
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
  TDOM.speedValue.className   = `speed-value ${sc}`;

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
    const durMs   = seg.end_ms - seg.start_ms;

    // Max absolu = budget à x0.25
    const maxMots = calcMaxMots(durMs, transcribeState.wpm, 0.25);
    const words   = newText.trim().split(/\s+/).filter(Boolean);

    // Si dépasse le max → tronquer automatiquement
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

    // Recalculer le speed
    const newSpeed = calcSpeedFactor(seg.text, durMs, transcribeState.wpm);
    if (!seg.speed_forced) {
      seg.speed_factor = newSpeed;
      TDOM.speedSlider.max   = Math.max(newSpeed, 0.25).toFixed(2);
      TDOM.speedSlider.value = newSpeed.toFixed(2);
      TDOM.speedSliderVal.textContent = `x${newSpeed.toFixed(2)}`;
      updateSpeedUI(newSpeed, seg.text, durMs, false, newSpeed);
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

    const val        = parseFloat(TDOM.speedSlider.value);
    const autoFactor = calcSpeedFactor(seg.text, seg.end_ms - seg.start_ms, transcribeState.wpm);

    seg.speed_factor = val;
    seg.speed_forced = true;
    TDOM.speedSliderVal.textContent = `x${val.toFixed(2)}`;
    updateSpeedUI(val, seg.text, seg.end_ms - seg.start_ms, true, autoFactor);

    // Recalculer le budget de mots avec le nouveau speed
    const maxMots = calcMaxMots(seg.end_ms - seg.start_ms, transcribeState.wpm, val);
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
  TDOM.speedSlider.max   = seg.speed_factor.toFixed(2);
  TDOM.speedSlider.value = seg.speed_factor;
  TDOM.speedSliderVal.textContent = `x${seg.speed_factor.toFixed(2)}`;
  updateSpeedUI(seg.speed_factor, seg.text, seg.end_ms - seg.start_ms, false, seg.speed_factor);
  renderTimeline();
  window.Toast?.info('Vitesse recalculée automatiquement.');
}

/* ═══════════════════════════════════════════════════
   COUPER UN SEGMENT
═══════════════════════════════════════════════════ */

function cutSegmentAt(idx, event) {
  const seg       = transcribeState.segments[idx];
  const container = TDOM.timelineSegments;
  if (!seg || !container) return;

  // Calculer le timing au clic
  const rect    = container.getBoundingClientRect();
  const clickX  = event.clientX - rect.left + container.scrollLeft;
  const innerW  = container.firstChild?.offsetWidth || container.offsetWidth;
  const totalMs = transcribeState.segments[transcribeState.segments.length - 1].end_ms;
  const clickMs = Math.round((clickX / innerW) * totalMs);

  // Vérifier que le clic est dans le segment
  if (clickMs <= seg.start_ms || clickMs >= seg.end_ms) {
    window.Toast?.warn('Cliquez à l\'intérieur du segment pour le couper.');
    return;
  }

  pushUndo('Couper segment');

  // Répartir le texte proportionnellement
  const ratio   = (clickMs - seg.start_ms) / (seg.end_ms - seg.start_ms);
  const words   = (seg.text || '').trim().split(/\s+/).filter(Boolean);
  const split   = Math.max(1, Math.round(words.length * ratio));
  const text1   = words.slice(0, split).join(' ');
  const text2   = words.slice(split).join(' ');

  const seg1 = { ...seg, end_ms: clickMs, text: text1 };
  const seg2 = {
    id:           `new_${Date.now()}`,
    index:        seg.index + 0.5,
    start_ms:     clickMs,
    end_ms:       seg.end_ms,
    text:         text2,
    speed_forced: false,
    thumb_url:    seg.thumb_url,
    deleted:      false,
    speed_factor: 1.0,
  };

  seg1.speed_factor = calcSpeedFactor(text1, seg1.end_ms - seg1.start_ms, transcribeState.wpm);
  seg2.speed_factor = calcSpeedFactor(text2, seg2.end_ms - seg2.start_ms, transcribeState.wpm);

  transcribeState.segments.splice(idx, 1, seg1, seg2);
  reindexSegments();
  renderTimeline();
  selectSegment(idx);
  window.Toast?.success('Segment coupé en deux.');
  transcribeState.dirty = true;
}

/* ═══════════════════════════════════════════════════
   FUSIONNER AVEC LE SUIVANT
═══════════════════════════════════════════════════ */

function mergeWithNext(idx) {
  if (idx === undefined) idx = transcribeState.selectedIdx;
  if (idx === null || idx >= transcribeState.segments.length - 1) {
    window.Toast?.warn('Pas de segment suivant à fusionner.');
    return;
  }

  pushUndo('Fusionner segments');

  const s1  = transcribeState.segments[idx];
  const s2  = transcribeState.segments[idx + 1];
  const merged = {
    ...s1,
    end_ms:       s2.end_ms,
    text:         [s1.text, s2.text].filter(Boolean).join(' '),
    speed_forced: false,
  };
  merged.speed_factor = calcSpeedFactor(merged.text, merged.end_ms - merged.start_ms, transcribeState.wpm);

  transcribeState.segments.splice(idx, 2, merged);
  reindexSegments();
  renderTimeline();
  selectSegment(idx);
  window.Toast?.success('Segments fusionnés.');
  transcribeState.dirty = true;
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

  pushUndo('Supprimer segment');
  transcribeState.segments[idx].deleted = true;
  transcribeState.segments[idx].text    = '';
  renderTimeline();
  transcribeState.selectedIdx = null;
  window.Toast?.warn('Segment supprimé — il sera muet dans la vidéo finale.');
  transcribeState.dirty = true;
}

/* ═══════════════════════════════════════════════════
   UNDO / REDO
═══════════════════════════════════════════════════ */

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
  const cur    = levels.indexOf(transcribeState.zoomLevel);
  const idx    = cur === -1 ? 2 : cur;
  const next   = Math.max(0, Math.min(levels.length - 1, idx + dir));
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
window.undoSegment          = undoSegment;
window.redoSegment          = redoSegment;
window.zoomTimeline         = zoomTimeline;
window.mergeWithNext        = mergeWithNext;
window.cutSegmentAt         = cutSegmentAt;
window.confirmDeleteSegment = confirmDeleteSegment;
window.deleteSegment        = deleteSegment;
window.splitSegmentAtCursor = cutSegmentAt;
window.resetSpeedFactor     = resetSpeedFactor;
window.saveAllSegments      = saveAllSegments;
window.saveCurrentSegment   = saveCurrentSegment;
window.importScript         = importScript;
window.loadSegments         = loadSegments;
window.markStep2Done        = markStep2Done;
window.drawWaveform         = drawWaveform;

/* ═══════════════════════════════════════════════════
   DRAG RESIZE — REDIMENSIONNEMENT DES SEGMENTS
═══════════════════════════════════════════════════ */

const MIN_SEG_MS = 300;
let _dragState   = null;

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
  document.addEventListener('mouseup',   onResizeEnd);
  document.body.style.cursor     = 'ew-resize';
  document.body.style.userSelect = 'none';
}

function onResizeMove(e) {
  if (!_dragState) return;
  const { idx, side, innerW, totalMs, startX, origStart, origEnd } = _dragState;
  const dx      = e.clientX - startX;
  const dMs     = Math.round((dx / innerW) * totalMs);
  const seg     = transcribeState.segments[idx];
  const prevSeg = transcribeState.segments[idx - 1];
  const nextSeg = transcribeState.segments[idx + 1];
  if (!seg) return;

  // Durée minimale basée sur le texte du segment
  function minDurMs(s) {
    if (!s) return MIN_SEG_MS;
    const nbMots = (s.text || '').trim().split(/\s+/).filter(Boolean).length;
    if (nbMots === 0) return MIN_SEG_MS;
    // Durée minimale = durée estimée de la voix
    return Math.max(MIN_SEG_MS, Math.round((nbMots / transcribeState.wpm) * 60 * 1000));
  }

  if (side === 'right') {
    // Le segment actuel doit garder sa durée minimale (son texte)
    const minEnd = seg.start_ms + minDurMs(seg);
    // Le segment suivant doit garder SA durée minimale (son texte)
    const maxEnd = nextSeg
      ? nextSeg.end_ms - minDurMs(nextSeg)
      : origEnd + 60000;
    const cappedEnd = Math.max(minEnd, Math.min(maxEnd, origEnd + dMs));

    seg.end_ms = cappedEnd;
    if (nextSeg) nextSeg.start_ms = cappedEnd;

  } else {
    // Le segment actuel doit garder sa durée minimale
    const maxStart = seg.end_ms - minDurMs(seg);
    // Le segment précédent doit garder SA durée minimale
    const minStart = prevSeg
      ? prevSeg.start_ms + minDurMs(prevSeg)
      : 0;
    const cappedStart = Math.max(minStart, Math.min(maxStart, origStart + dMs));

    seg.start_ms = cappedStart;
    if (prevSeg) prevSeg.end_ms = cappedStart;
  }

  // Recalculer speed_factor
  [seg, prevSeg, nextSeg].forEach(s => {
    if (s && !s.speed_forced) {
      s.speed_factor = calcSpeedFactor(s.text, s.end_ms - s.start_ms, transcribeState.wpm);
    }
  });

  clearTimeout(_dragState?._timer);
  if (_dragState) {
    _dragState._timer = setTimeout(() => {
      renderTimeline();
      if (transcribeState.selectedIdx === idx) loadSegmentEditor(idx);
    }, 30);
  }
}

function onResizeEnd() {
  if (_dragState) {
    clearTimeout(_dragState._timer);
    renderTimeline();
    if (transcribeState.selectedIdx !== null) loadSegmentEditor(transcribeState.selectedIdx);
  }
  _dragState = null;
  document.removeEventListener('mousemove', onResizeMove);
  document.removeEventListener('mouseup',   onResizeEnd);
  document.body.style.cursor     = '';
  document.body.style.userSelect = '';
  transcribeState.dirty = true;
}

/* ═══════════════════════════════════════════════════
   SAUVEGARDE
═══════════════════════════════════════════════════ */

async function saveCurrentSegment() {
  const idx = transcribeState.selectedIdx;
  if (idx === null) { window.Toast?.warn('Aucun segment sélectionné.'); return; }

  const seg  = transcribeState.segments[idx];
  if (!seg?.id) { window.Toast?.warn('Ce segment n\'a pas encore d\'ID — sauvegardez tout.'); return; }

  const jobId = document.getElementById('current-job-id')?.value;
  const csrf  = document.getElementById('csrf-token')?.value || '';

  try {
    const res = await fetch(`/api/jobs/${jobId}/segments/${seg.id}/save/`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({
        text:         seg.text,
        speed_factor: seg.speed_factor,
        speed_forced: seg.speed_forced,
        start_ms:     seg.start_ms,
        end_ms:       seg.end_ms,
      }),
    });
    if (res.ok) {
      window.Toast?.success(`Segment ${idx + 1} sauvegardé.`);
      markSegmentSaved(idx);
    } else {
      const d = await res.json();
      window.Toast?.error(d.error || 'Erreur sauvegarde.');
    }
  } catch (e) {
    window.Toast?.error('Erreur réseau lors de la sauvegarde.');
  }
}

async function saveAllSegments() {
  const jobId = document.getElementById('current-job-id')?.value;
  const csrf  = document.getElementById('csrf-token')?.value || '';

  if (!jobId || !transcribeState.segments.length) {
    window.Toast?.warn('Aucun segment à sauvegarder.');
    return;
  }

  const segsToSave = transcribeState.segments
    .filter(s => s.id && !s.deleted)
    .map(s => ({
      id:           s.id,
      index:        s.index,
      text:         s.text,
      speed_factor: s.speed_factor,
      speed_forced: s.speed_forced,
      start_ms:     s.start_ms,
      end_ms:       s.end_ms,
    }));

  console.log('save-all: envoi de', segsToSave.length, 'segments, IDs:', segsToSave.map(s => s.id));

  try {
    const res = await fetch(`/api/jobs/${jobId}/segments/save-all/`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({ segments: segsToSave }),
    });
    const data = await res.json();
    if (res.ok) {
      transcribeState.dirty = false;
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
    const blocs   = parseScriptFile(content);

    if (!blocs.length) {
      window.Toast?.error('Aucun texte détecté dans ce fichier.');
      input.value = '';
      return;
    }

    const nbSegs = transcribeState.segments.length;
    if (blocs.length !== nbSegs) {
      window.Toast?.error(
        `Le fichier contient ${blocs.length} blocs mais le projet a ${nbSegs} segments. ` +
        `Le nombre doit être identique.`
      );
      input.value = '';
      return;
    }

    _importBlocs = blocs;
    openImportModal(blocs);
    input.value = '';
  };
  reader.readAsText(file, 'utf-8');
}

function parseScriptFile(content) {
  content = content.trim();
  if ('-->' in content || content.includes('-->')) {
    // SRT
    const blocs = [];
    for (const bloc of content.split(/\n\n+/)) {
      const lignes = bloc.trim().split('\n').filter(l => l.trim());
      const texte  = lignes.filter(l => !l.match(/^\d+$/) && !l.includes('-->')).join(' ').trim();
      if (texte) blocs.push(texte);
    }
    return blocs;
  }
  // Paragraphes
  const blocs = content.split(/\n\n+/).map(b => b.trim()).filter(Boolean);
  if (blocs.length > 1) return blocs;
  // Lignes
  return content.split('\n').map(l => l.trim()).filter(Boolean);
}

function openImportModal(blocs) {
  const segs    = transcribeState.segments;
  const modal   = document.getElementById('modal-import');
  const table   = document.getElementById('import-preview-table');
  const subtitle = document.getElementById('import-preview-subtitle');
  const btnOk   = document.getElementById('btn-confirm-import');
  if (!modal || !table) return;

  let allOk = true;
  let html  = `
    <table class="import-preview-table">
      <thead>
        <tr>
          <th>#</th>
          <th>Timing</th>
          <th>Texte importé</th>
          <th>Mots</th>
          <th>Max</th>
          <th>Statut</th>
        </tr>
      </thead>
      <tbody>`;

  blocs.forEach((texte, i) => {
    const seg     = segs[i];
    const maxMots = calcMaxMots(seg.end_ms - seg.start_ms, transcribeState.wpm);
    const nbMots  = texte.trim().split(/\s+/).filter(Boolean).length;
    const ok      = nbMots <= maxMots;
    if (!ok) allOk = false;

    html += `
      <tr class="${ok ? '' : 'import-row-error'}" id="import-row-${i}">
        <td class="import-cell-num">${i + 1}</td>
        <td class="import-cell-tc">${msToTC(seg.start_ms)}→${msToTC(seg.end_ms)}</td>
        <td class="import-cell-text">
          <textarea class="import-text-edit" data-idx="${i}"
            data-max="${maxMots}"
            rows="2">${texte}</textarea>
        </td>
        <td class="import-cell-count" id="import-count-${i}">${nbMots}</td>
        <td class="import-cell-max">${maxMots}</td>
        <td class="import-cell-status" id="import-status-${i}">
          ${ok
            ? `<span class="import-ok">✓</span>`
            : `<span class="import-err">Trop long</span>`}
        </td>
      </tr>`;
  });

  html += '</tbody></table>';
  table.innerHTML = html;

  subtitle.textContent = allOk
    ? `${blocs.length} segments — tout est OK`
    : `${blocs.filter((t,i) => {
        const maxMots = calcMaxMots(segs[i].end_ms - segs[i].start_ms, transcribeState.wpm);
        return t.trim().split(/\s+/).filter(Boolean).length > maxMots;
      }).length} segment(s) trop long(s) — corrigez avant d'importer`;

  btnOk.disabled = !allOk;

  // Écouter les modifs dans le tableau
  table.querySelectorAll('.import-text-edit').forEach(ta => {
    ta.addEventListener('input', () => onImportTextEdit(ta, btnOk, subtitle, segs));
  });

  modal.style.display = 'flex';
}

function onImportTextEdit(ta, btnOk, subtitle, segs) {
  const idx     = parseInt(ta.dataset.idx);
  const maxMots = parseInt(ta.dataset.max);
  const nbMots  = ta.value.trim().split(/\s+/).filter(Boolean).length;
  const ok      = nbMots <= maxMots;

  // Mettre à jour le blob en mémoire
  _importBlocs[idx] = ta.value;

  // Mettre à jour la ligne
  const row    = document.getElementById(`import-row-${idx}`);
  const count  = document.getElementById(`import-count-${idx}`);
  const status = document.getElementById(`import-status-${idx}`);
  if (row)    row.className    = ok ? '' : 'import-row-error';
  if (count)  count.textContent = nbMots;
  if (status) status.innerHTML  = ok
    ? `<span class="import-ok">✓</span>`
    : `<span class="import-err">Trop long</span>`;

  // Vérifier si tout est OK
  const allOk = _importBlocs.every((texte, i) => {
    const seg     = segs[i];
    const max     = calcMaxMots(seg.end_ms - seg.start_ms, transcribeState.wpm);
    return texte.trim().split(/\s+/).filter(Boolean).length <= max;
  });

  btnOk.disabled = !allOk;
  const nbErr = _importBlocs.filter((texte, i) => {
    const seg = segs[i];
    const max = calcMaxMots(seg.end_ms - seg.start_ms, transcribeState.wpm);
    return texte.trim().split(/\s+/).filter(Boolean).length > max;
  }).length;
  subtitle.textContent = allOk
    ? `${_importBlocs.length} segments — tout est OK`
    : `${nbErr} segment(s) trop long(s) — corrigez avant d'importer`;
}

function closeImportModal() {
  const modal = document.getElementById('modal-import');
  if (modal) modal.style.display = 'none';
  _importBlocs = [];
}

async function confirmImport() {
  const jobId = document.getElementById('current-job-id')?.value;
  const csrf  = document.getElementById('csrf-token')?.value || '';
  if (!jobId || !_importBlocs.length) return;

  const btnOk = document.getElementById('btn-confirm-import');
  if (btnOk) btnOk.disabled = true;

  // Envoyer via FormData avec le texte corrigé
  const content  = _importBlocs.join('\n\n');
  const blob     = new Blob([content], { type: 'text/plain' });
  const formData = new FormData();
  formData.append('script_file', blob, 'script.txt');

  try {
    const res  = await fetch(`/api/jobs/${jobId}/segments/import-script/`, {
      method:  'POST',
      headers: { 'X-CSRFToken': csrf },
      body:    formData,
    });
    const data = await res.json();

    if (data.status === 'ok') {
      data.segments.forEach(s => {
        const seg = transcribeState.segments.find(x => x.id == s.id || x.index === s.index);
        if (seg) { seg.text = s.text; seg.speed_factor = s.speed_factor; }
      });
      renderTimeline();
      closeImportModal();
      window.Toast?.success(`Script importé — ${data.updated} segments mis à jour.`);
      transcribeState.dirty = true;
    } else {
      window.Toast?.error(data.error || 'Import refusé.');
      if (btnOk) btnOk.disabled = false;
    }
  } catch {
    window.Toast?.error('Erreur réseau lors de l\'import.');
    if (btnOk) btnOk.disabled = false;
  }
}

window.closeImportModal = closeImportModal;
window.confirmImport    = confirmImport;