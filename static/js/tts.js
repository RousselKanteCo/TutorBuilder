/**
 * tts.js — Module génération voix TTS
 * Responsabilité :
 *   - Étape 3 : affichage + lancement synthèse
 *   - Vérification sauvegarde avant lancement
 *   - Polling statut
 *   - Mise à jour étape 3
 */

'use strict';

const POLL_TTS_INTERVAL = 2500;

const ttsState = {
  pollTimer:   null,
  isRunning:   false,
  doneHandled: false,
};

/* ═══════════════════════════════════════════════════
   LANCER LA SYNTHÈSE
═══════════════════════════════════════════════════ */

async function startSynthesis() {
  const jobId = document.getElementById('current-job-id')?.value;
  if (!jobId) { window.Toast?.error('Aucun job actif.'); return; }

  // ── 1. Vérifier sauvegarde ────────────────────────────────────────────
  const unsavedCount = window.transcribeState?.unsavedSegments?.size || 0;
  if (unsavedCount > 0) {
    window.Toast?.error(
      `${unsavedCount} segment(s) non sauvegardé(s). ` +
      'Cliquez sur "Sauvegarder" avant de générer la voix.'
    );
    const btnSave = document.getElementById('btn-save-all');
    if (btnSave) {
      btnSave.style.animation = 'none';
      setTimeout(() => btnSave.classList.add('btn-save-pulse'), 10);
    }
    return;
  }

  // ── 2. Déterminer les segments modifiés ──────────────────────────────
  const modifiedIds = window.getModifiedSegmentIds ? window.getModifiedSegmentIds() : [];
  // Le back se charge de fusionner avec les segments sans audio

  const ttsEngine = document.getElementById('select-tts-engine')?.value || 'elevenlabs';
  const voice = window.transcribeState?.selectedVoice?.id
    || document.querySelector('.voice-card.selected')?.dataset.voiceId
    || 'narrateur_pro';
  const langue = document.getElementById('select-language')?.value || 'fr';
  const csrf   = document.getElementById('csrf-token')?.value || '';

  const btn = document.getElementById('btn-synthesize');
  if (btn) { btn.disabled = true; }

  const progressMsg = modifiedIds.length > 0
    ? `Regénération de ${modifiedIds.length} segment(s) modifié(s) + segments sans audio...`
    : 'Génération de la voix en cours...';

  setTtsProgress(5, progressMsg);
  showTtsProgress();
  window.Toast?.info(progressMsg);

  try {
    const body = { tts_engine: ttsEngine, voice, language: langue };
    if (modifiedIds.length > 0) body.segment_ids = modifiedIds;

    const res = await fetch(`/api/jobs/${jobId}/synthesize/`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify(body),
    });

    const data = await res.json();

    if (!res.ok) {
      if (btn) btn.disabled = false;
      hideTtsProgress();
      window.Toast?.error(data.error || 'Erreur lors du lancement.');
      return;
    }

    startTtsPolling(jobId);

  } catch (e) {
    if (btn) btn.disabled = false;
    hideTtsProgress();
    window.Toast?.error(`Erreur : ${e.message}`);
  }
}

/* ═══════════════════════════════════════════════════
   POLLING
═══════════════════════════════════════════════════ */

function startTtsPolling(jobId) {
  if (ttsState.pollTimer) clearInterval(ttsState.pollTimer);
  ttsState.isRunning   = true;
  ttsState.doneHandled = false;

  ttsState.pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}/`);
      const job = await res.json();

      if (job.status === 'done' && !ttsState.doneHandled) {
        ttsState.doneHandled = true;
        clearInterval(ttsState.pollTimer);
        ttsState.isRunning = false;
        setTtsProgress(100, 'Voix générée avec succès !');
        setTimeout(() => hideTtsProgress(), 2000);
        markStep3Done();
        unlockStep4();

        // Vider les segments modifiés — une seule fois
        if (typeof window.clearModifiedSegments === 'function') {
          window.clearModifiedSegments();
        }

        // Changer texte bouton
        const btn = document.getElementById('btn-synthesize');
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
              <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
            </svg>
            Regénérer la voix off`;
          btn.style.opacity = '0.75';
          btn.title = 'Relancer uniquement les segments modifiés ou manquants';
        }

        // Recharger segments avec has_audio
        const jobId2 = document.getElementById('current-job-id')?.value;
        if (jobId2 && typeof window.loadSegments === 'function') {
          window.loadSegments(jobId2);
        }

        window.Toast?.success('Voix off générée ! Passez à l\'export.');
      }

      if (job.status === 'synthesizing') {
        setTtsProgress(60, 'Synthèse en cours…');
      }

      if (job.status === 'error') {
        clearInterval(ttsState.pollTimer);
        ttsState.isRunning = false;
        hideTtsProgress();
        const btn = document.getElementById('btn-synthesize');
        if (btn) { btn.disabled = false; btn.textContent = 'Générer la voix off'; }
        window.Toast?.error(`Erreur : ${job.error_message || 'Synthèse échouée.'}`);
      }

    } catch (_) {}
  }, POLL_TTS_INTERVAL);
}

/* ═══════════════════════════════════════════════════
   PROGRESSION
═══════════════════════════════════════════════════ */

function showTtsProgress() {
  const el = document.getElementById('tts-progress-wrap');
  if (el) el.style.display = 'block';
}

function hideTtsProgress() {
  const el = document.getElementById('tts-progress-wrap');
  if (el) el.style.display = 'none';
}

function setTtsProgress(pct, label = '') {
  const fill  = document.getElementById('tts-progress-fill');
  const lbl   = document.getElementById('tts-progress-label');
  const pctEl = document.getElementById('tts-progress-pct');
  if (fill)  fill.style.width    = `${pct}%`;
  if (lbl && label)  lbl.textContent  = label;
  if (pctEl) pctEl.textContent   = `${Math.round(pct)}%`;
}

/* ═══════════════════════════════════════════════════
   MARQUER ÉTAPE 3 DONE
═══════════════════════════════════════════════════ */

function markStep3Done() {
  const wsec3 = document.getElementById('wsec-3');
  if (!wsec3) return;
  wsec3.classList.remove('active', 'locked');
  wsec3.classList.add('done');

  const badge = wsec3.querySelector('.wsec-badge-num');
  if (badge) {
    badge.classList.remove('active');
    badge.classList.add('done');
    badge.innerHTML = `
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <polyline points="20 6 9 17 4 12"/>
      </svg>`;
  }

  const status = document.getElementById('wsec-3-status');
  if (status) status.innerHTML = `<span class="wsec-done-badge">✓ Fait</span>`;

  const pill3 = document.getElementById('pill-3');
  if (pill3) { pill3.classList.remove('active'); pill3.classList.add('done'); }

  // Garder le bouton actif pour relancer si besoin
  // mais changer son apparence
  const btn = document.getElementById('btn-synthesize');
  if (btn) {
    btn.style.opacity = '0.7';
    btn.title = 'Relancer la synthèse vocale';
  }
}

/* ═══════════════════════════════════════════════════
   DÉVERROUILLER ÉTAPE 4
═══════════════════════════════════════════════════ */

function unlockStep4() {
  const wsec4 = document.getElementById('wsec-4');
  if (!wsec4) return;
  wsec4.classList.remove('locked');
  wsec4.classList.add('active');

  const badge = wsec4.querySelector('.wsec-badge-num');
  if (badge) badge.classList.add('active');

  const pill4 = document.getElementById('pill-4');
  if (pill4) pill4.classList.add('active');

  // Activer le bouton export
  const btn = document.getElementById('btn-export');
  if (btn) btn.disabled = false;
}

/* ═══════════════════════════════════════════════════
   DÉVERROUILLER ÉTAPE 3 (appelé depuis transcribe.js)
═══════════════════════════════════════════════════ */

function unlockStep3() {
  const wsec3 = document.getElementById('wsec-3');
  if (!wsec3) return;
  wsec3.classList.remove('locked');
  wsec3.classList.add('active');

  const badge = wsec3.querySelector('.wsec-badge-num');
  if (badge) badge.classList.add('active');

  const btn = document.getElementById('btn-synthesize');
  if (btn) btn.disabled = false;

  const pill3 = document.getElementById('pill-3');
  if (pill3) pill3.classList.add('active');
}

/* ═══════════════════════════════════════════════════
   INIT
═══════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('btn-synthesize');
  if (btn) btn.addEventListener('click', startSynthesis);

  // Appliquer le statut job au reload
  setTimeout(() => {
    if (window._pendingMarkStep3Done) {
      window._pendingMarkStep3Done = false;
      markStep3Done();
    }
    if (window._pendingUnlockStep4) {
      window._pendingUnlockStep4 = false;
      unlockStep4();
    }
  }, 800);
});

// CSS animation pulse pour bouton sauvegarder
const TTS_STYLE = document.createElement('style');
TTS_STYLE.textContent = `
@keyframes pulse-save {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.08); box-shadow: 0 0 0 4px rgba(34,197,94,.3); }
}`;
document.head.appendChild(TTS_STYLE);

// Exposer globalement
window.unlockStep3  = unlockStep3;
window.markStep3Done = markStep3Done;