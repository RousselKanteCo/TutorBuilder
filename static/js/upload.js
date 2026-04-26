/**
 * upload.js — Module upload vidéo
 * Responsabilité : sélection, validation, preview, envoi, progression
 */

'use strict';

/* ═══════════════════════════════════════════════════
   CONSTANTES
═══════════════════════════════════════════════════ */

const UPLOAD_CONFIG = {
  MAX_SIZE_MB:   200,
  MAX_SIZE_BYTES: 200 * 1024 * 1024,
  ACCEPTED_MIME: [
    'video/mp4',
    'video/x-msvideo',
    'video/quicktime',
    'video/x-matroska',
    'video/webm',
    'video/x-ms-wmv',
    'video/mpeg',
  ],
  ACCEPTED_EXT: /\.(mp4|mkv|avi|mov|wmv|webm|mpeg|mpg)$/i,
};

/* ═══════════════════════════════════════════════════
   ÉTAT LOCAL
═══════════════════════════════════════════════════ */

const uploadState = {
  file:          null,
  projectId:     null,
  pendingTitle:  null,
  isUploading:   false,
  isDone:        false,
  videoMeta: {
    duration: null,
    width:    null,
    height:   null,
  },
  duplicateJobId: null,
};

/* ═══════════════════════════════════════════════════
   ÉLÉMENTS DOM
═══════════════════════════════════════════════════ */

const DOM = {
  dropZone:      document.getElementById('drop-zone'),
  fileInput:     document.getElementById('file-input'),
  selectProject: document.getElementById('select-project'),
  btnUpload:     document.getElementById('btn-upload'),
  btnCancel:     document.getElementById('btn-cancel'),
  uploadError:   document.getElementById('upload-error'),
  progressWrap:  document.getElementById('upload-progress-wrap'),
  progressLabel: document.getElementById('upload-progress-label'),
  progressPct:   document.getElementById('upload-progress-pct'),
  progressFill:  document.getElementById('upload-progress-fill'),
  dropText:      document.getElementById('drop-text'),
  dropSubtext:   document.getElementById('drop-subtext'),
  dropFilename:  document.getElementById('drop-filename'),
  dropIcon:      document.getElementById('drop-icon'),
  videoPlayer:   document.getElementById('video-player'),
  placeholder:   document.getElementById('player-placeholder'),
  playerHud:     document.getElementById('player-hud'),
  playerTransport: document.getElementById('player-transport'),
  hudFilename:   document.getElementById('hud-filename'),
  hudSize:       document.getElementById('hud-size'),
  topbarProject: document.getElementById('topbar-project-name'),
  wsec1Status:   document.getElementById('wsec-1-status'),
  logArea:       document.getElementById('log-area'),
  // Modaux
  modalTitle:        document.getElementById('modal-title'),
  inputJobTitle:     document.getElementById('input-job-title'),
  modalTitleError:   document.getElementById('modal-title-error'),
  modalCharCount:    document.getElementById('modal-char-count'),
  modalTitleCancel:  document.getElementById('modal-title-cancel'),
  modalTitleConfirm: document.getElementById('modal-title-confirm'),
  modalDuplicate:    document.getElementById('modal-duplicate'),
  duplicateInfo:     document.getElementById('duplicate-info'),
  modalDupNew:       document.getElementById('modal-dup-new'),
  modalDupReuse:     document.getElementById('modal-dup-reuse'),
};

/* ═══════════════════════════════════════════════════
   LOGS — délégués à toast.js
═══════════════════════════════════════════════════ */

function log(msg, type = '') { window.Toast?.log(msg, type); }

/* ═══════════════════════════════════════════════════
   AFFICHAGE ERREUR
═══════════════════════════════════════════════════ */

function showError(msg) {
  window.Toast?.error(msg);
  DOM.uploadError.textContent = msg;
  DOM.uploadError.classList.add('show');
  DOM.dropZone.classList.add('error');
  DOM.dropZone.classList.remove('has-file');
}

function clearError() {
  DOM.uploadError.classList.remove('show');
  DOM.uploadError.textContent = '';
  DOM.dropZone.classList.remove('error');
}

/* ═══════════════════════════════════════════════════
   VALIDATION
═══════════════════════════════════════════════════ */

function validateFile(file) {
  const E = window.Toast?.ERRORS || {};

  if (!file) {
    return { valid: false, error: E.no_file || 'Aucun fichier sélectionné.' };
  }

  const mimeOk = UPLOAD_CONFIG.ACCEPTED_MIME.includes(file.type);
  const extOk  = UPLOAD_CONFIG.ACCEPTED_EXT.test(file.name);

  if (!mimeOk && !extOk) {
    return { valid: false, error: typeof E.bad_format === 'function' ? E.bad_format(file.name) : `Format non supporté : "${file.name}".` };
  }

  if (file.size > UPLOAD_CONFIG.MAX_SIZE_BYTES) {
    const mb = (file.size / (1024 * 1024)).toFixed(0);
    return { valid: false, error: typeof E.too_large === 'function' ? E.too_large(mb) : `Fichier trop volumineux : ${mb} Mo.` };
  }

  if (file.size < 1024) {
    return { valid: false, error: E.corrupted || 'Fichier corrompu ou vide.' };
  }

  return { valid: true };
}

/* ═══════════════════════════════════════════════════
   SÉLECTION FICHIER
═══════════════════════════════════════════════════ */

function onFileSelected(file) {
  clearError();

  const result = validateFile(file);
  if (!result.valid) {
    showError(result.error);
    resetDropZone();
    return;
  }

  uploadState.file = file;

  const sizeMb = (file.size / (1024 * 1024)).toFixed(1);

  // Mettre à jour la drop zone
  DOM.dropZone.classList.add('has-file');
  DOM.dropZone.classList.remove('error');
  DOM.dropFilename.textContent = `${file.name} — ${sizeMb} Mo`;

  // Icône check
  DOM.dropIcon.innerHTML = `
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="20 6 9 17 4 12"/>
    </svg>`;

  DOM.dropText.textContent    = 'Vidéo sélectionnée';
  DOM.dropSubtext.textContent = 'Prête à être envoyée';

  // Preview dans le player
  showVideoPreview(file);

  // Activer bouton upload si projet sélectionné
  checkReadyToUpload();

  // Bouton annuler
  DOM.btnCancel.classList.add('show');

  log(`Fichier sélectionné : ${file.name} (${sizeMb} Mo)`, 'ok');
}

/* ═══════════════════════════════════════════════════
   PREVIEW VIDÉO
═══════════════════════════════════════════════════ */

function showVideoPreview(file) {
  const url = URL.createObjectURL(file);
  DOM.videoPlayer.src = url;
  DOM.videoPlayer.style.display = 'block';
  DOM.placeholder.style.display = 'none';
  DOM.playerHud.style.display   = 'flex';
  DOM.playerTransport.style.display = 'flex';

  const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
  DOM.hudFilename.textContent = file.name;
  DOM.hudSize.textContent     = `${sizeMb} Mo`;

  // Capturer durée + résolution via l'élément video HTML5
  DOM.videoPlayer.addEventListener('loadedmetadata', () => {
    uploadState.videoMeta.duration = Math.round(DOM.videoPlayer.duration);
    uploadState.videoMeta.width    = DOM.videoPlayer.videoWidth;
    uploadState.videoMeta.height   = DOM.videoPlayer.videoHeight;
    log(`Vidéo : ${DOM.videoPlayer.videoWidth}×${DOM.videoPlayer.videoHeight} — ${Math.round(DOM.videoPlayer.duration)}s`, 'info');
  }, { once: true });
}

function hideVideoPreview() {
  DOM.videoPlayer.pause();
  DOM.videoPlayer.src = '';
  DOM.videoPlayer.style.display = 'none';
  DOM.placeholder.style.display = 'flex';
  DOM.playerHud.style.display   = 'none';
  DOM.playerTransport.style.display = 'none';
}

/* ═══════════════════════════════════════════════════
   RESET
═══════════════════════════════════════════════════ */

function resetDropZone() {
  uploadState.file = null;
  DOM.dropZone.classList.remove('has-file', 'error');
  DOM.dropFilename.textContent = '';

  DOM.dropIcon.innerHTML = `
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
      <polyline points="16 16 12 12 8 16"/>
      <line x1="12" y1="12" x2="12" y2="21"/>
      <path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3"/>
    </svg>`;

  DOM.dropText.textContent    = 'Déposez votre vidéo ici';
  DOM.dropSubtext.textContent = 'ou cliquez pour parcourir';
  DOM.fileInput.value         = '';
  DOM.btnCancel.classList.remove('show');
  DOM.btnUpload.disabled = true;
}

function resetAll() {
  resetDropZone();
  clearError();
  hideVideoPreview();
  hideProgress();
  log('Sélection annulée.', 'info');
}

/* ═══════════════════════════════════════════════════
   VÉRIF PRÊT À UPLOADER
═══════════════════════════════════════════════════ */

function checkReadyToUpload() {
  const hasFile    = !!uploadState.file;
  const hasProject = !!DOM.selectProject.value;
  DOM.btnUpload.disabled = !(hasFile && hasProject);
}

/* ═══════════════════════════════════════════════════
   PROGRESSION
═══════════════════════════════════════════════════ */

function showProgress() {
  DOM.progressWrap.style.display = 'block';
}

function hideProgress() {
  DOM.progressWrap.style.display = 'none';
  setProgress(0, 'Upload en cours…');
}

function setProgress(pct, label = '') {
  DOM.progressFill.style.width = `${pct}%`;
  DOM.progressPct.textContent  = `${Math.round(pct)}%`;
  if (label) DOM.progressLabel.textContent = label;
}

/* ═══════════════════════════════════════════════════
   MODAL TITRE
═══════════════════════════════════════════════════ */

function openModalTitle() {
  DOM.modalTitle.style.display = 'flex';
  DOM.inputJobTitle.value      = '';
  DOM.modalTitleError.textContent = '';
  DOM.modalCharCount.textContent  = '0 / 255';
  DOM.inputJobTitle.classList.remove('error');
  setTimeout(() => DOM.inputJobTitle.focus(), 50);
}

function closeModalTitle() {
  DOM.modalTitle.style.display = 'none';
}

function validateTitle(title) {
  if (!title.trim()) {
    return 'Le titre est obligatoire.';
  }
  if (title.trim().length < 3) {
    return 'Le titre doit contenir au moins 3 caractères.';
  }
  if (title.trim().length > 255) {
    return 'Le titre ne peut pas dépasser 255 caractères.';
  }
  // Caractères dangereux basiques
  if (/<|>|script/i.test(title)) {
    return 'Le titre contient des caractères non autorisés.';
  }
  return null;
}

// Compteur caractères
DOM.inputJobTitle.addEventListener('input', () => {
  const len = DOM.inputJobTitle.value.length;
  DOM.modalCharCount.textContent = `${len} / 255`;
  if (len > 230) {
    DOM.modalCharCount.style.color = 'var(--amber)';
  } else {
    DOM.modalCharCount.style.color = 'var(--text-3)';
  }
  // Effacer l'erreur en cours de frappe
  DOM.modalTitleError.textContent = '';
  DOM.inputJobTitle.classList.remove('error');
});

// Entrée clavier dans le champ titre
DOM.inputJobTitle.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') onModalTitleConfirm();
  if (e.key === 'Escape') closeModalTitle();
});

function onModalTitleConfirm() {
  const title = DOM.inputJobTitle.value;
  const error = validateTitle(title);

  if (error) {
    DOM.modalTitleError.textContent = error;
    DOM.inputJobTitle.classList.add('error');
    DOM.inputJobTitle.focus();
    return;
  }

  closeModalTitle();
  checkDuplicateThenUpload(title.trim());
}

DOM.modalTitleCancel.addEventListener('click',  closeModalTitle);
DOM.modalTitleConfirm.addEventListener('click', onModalTitleConfirm);

// Fermer en cliquant l'overlay
DOM.modalTitle.addEventListener('click', (e) => {
  if (e.target === DOM.modalTitle) closeModalTitle();
});

/* ═══════════════════════════════════════════════════
   DÉTECTION DOUBLON
═══════════════════════════════════════════════════ */

async function checkDuplicateThenUpload(title) {
  const csrf      = document.getElementById('csrf-token')?.value || '';
  const projectId = DOM.selectProject.value;
  const file      = uploadState.file;

  // Sauvegarder pour usage ultérieur (reuse, upload)
  uploadState.projectId    = projectId;
  uploadState.pendingTitle = title;

  try {
    const res = await fetch('/api/jobs/check-duplicate/', {
      method:  'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken':  csrf,
      },
      body: JSON.stringify({
        project_id: projectId,
        filename:   file.name,
        size:       file.size,
        duration:   uploadState.videoMeta.duration,
        width:      uploadState.videoMeta.width,
        height:     uploadState.videoMeta.height,
      }),
    });

    if (res.ok) {
      const data = await res.json();
      if (data.duplicate && data.job_id) {
        // Doublon trouvé → afficher le modal
        uploadState.duplicateJobId = data.job_id;
        openModalDuplicate(data);
        return;
      }
    }
  } catch (_) {
    // En cas d'erreur réseau sur la vérification → on continue l'upload normalement
    log('Vérification doublon impossible — upload normal.', 'info');
  }

  // Pas de doublon → upload direct
  startUpload(title);
}

/* ═══════════════════════════════════════════════════
   MODAL DOUBLON
═══════════════════════════════════════════════════ */

function openModalDuplicate(data) {
  DOM.duplicateInfo.innerHTML = `
    <strong>${data.existing_title || data.filename}</strong><br>
    Taille : ${(data.size / (1024*1024)).toFixed(1)} Mo &nbsp;·&nbsp;
    Durée : ${data.duration}s &nbsp;·&nbsp;
    Résolution : ${data.width}×${data.height}
  `;
  DOM.modalDuplicate.style.display = 'flex';
}

function closeModalDuplicate() {
  DOM.modalDuplicate.style.display = 'none';
  uploadState.duplicateJobId = null;
}

DOM.modalDupNew.addEventListener('click', () => {
  closeModalDuplicate();
  // Titre déjà saisi → upload direct sans re-ouvrir le modal
  startUpload(uploadState.pendingTitle);
});

DOM.modalDupReuse.addEventListener('click', () => {
  const jobId = uploadState.duplicateJobId; // sauvegarder AVANT closeModalDuplicate
  closeModalDuplicate();
  reuseExistingVideo(jobId);
});

DOM.modalDuplicate.addEventListener('click', (e) => {
  if (e.target === DOM.modalDuplicate) closeModalDuplicate();
});

async function reuseExistingVideo(sourceJobId) {
  const csrf      = document.getElementById('csrf-token')?.value || '';
  const projectId = uploadState.projectId || DOM.selectProject.value;
  const title     = uploadState.pendingTitle || DOM.inputJobTitle.value.trim() || uploadState.file?.name || '';

  if (!projectId) {
    window.Toast?.error(window.Toast?.ERRORS.no_project || 'Veuillez sélectionner un projet.');
    return;
  }

  log('Réutilisation de la vidéo existante…', 'info');
  showProgress();
  setProgress(50, 'Création du job…');

  try {
    const res = await fetch('/api/jobs/reuse/', {
      method:  'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken':  csrf,
      },
      body: JSON.stringify({
        source_job_id: sourceJobId,
        project_id:    projectId,
        title:         title,
      }),
    });

    const data = await res.json();

    if (res.ok && data.id) {
      setProgress(100, 'Job créé !');
      onUploadSuccess(data);
    } else {
      onUploadError(window.Toast?.ERRORS.reuse_error || 'Erreur lors de la réutilisation.');
    }
  } catch (e) {
    onUploadError(window.Toast?.ERRORS.network_error || 'Erreur réseau.');
  }
}

/* ═══════════════════════════════════════════════════
   UPLOAD
═══════════════════════════════════════════════════ */

async function startUpload(title) {
  if (uploadState.isUploading || uploadState.isDone) return;

  const projectId = DOM.selectProject.value;
  if (!projectId) {
    showError(window.Toast?.ERRORS.no_project || 'Veuillez sélectionner un projet.');
    return;
  }

  if (!uploadState.file) {
    showError(window.Toast?.ERRORS.no_file || 'Aucun fichier sélectionné.');
    return;
  }

  uploadState.isUploading = true;
  uploadState.projectId   = projectId;

  // Désactiver les contrôles
  DOM.btnUpload.disabled  = true;
  DOM.btnUpload.classList.add('uploading');
  DOM.btnUpload.textContent = 'Envoi en cours…';
  DOM.btnCancel.classList.remove('show');
  DOM.fileInput.disabled  = true;

  showProgress();
  setProgress(0, 'Préparation…');
  log('Envoi de la vidéo en cours…');

  try {
    const csrf = document.getElementById('csrf-token')?.value || '';

    const formData = new FormData();
    formData.append('video_file',  uploadState.file);
    formData.append('project_id',  projectId);
    formData.append('title',       title || '');

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/jobs/', true);
    xhr.setRequestHeader('X-CSRFToken', csrf);

    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        const pct = (e.loaded / e.total) * 100;
        setProgress(pct, 'Upload en cours…');
      }
    });

    xhr.onload = () => {
      if (xhr.status === 401) {
        onUploadError(window.Toast?.ERRORS.session_expired || 'Session expirée.');
        setTimeout(() => { window.location.href = '/login/'; }, 2000);
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        let response;
        try { response = JSON.parse(xhr.responseText); } catch { response = {}; }
        onUploadSuccess(response);
      } else {
        let msg = window.Toast?.ERRORS.server_error || 'Erreur serveur.';
        try {
          const err = JSON.parse(xhr.responseText);
          if (err.errors) {
            const firstKey = Object.keys(err.errors)[0];
            const raw = err.errors[firstKey]?.[0] || msg;
            if (raw.includes('UUID'))           msg = window.Toast?.ERRORS.project_not_found || 'Projet introuvable.';
            else if (raw.includes('too large')) msg = window.Toast?.ERRORS.too_large('?') || raw;
            else                                msg = raw;
          } else {
            msg = err.detail || err.error || err.message || msg;
          }
        } catch {}
        onUploadError(msg);
      }
    };

    xhr.onerror = () => {
      onUploadError(window.Toast?.ERRORS.network_error || 'Erreur réseau.');
    };

    xhr.ontimeout = () => {
      onUploadError(window.Toast?.ERRORS.timeout || 'Délai dépassé.');
    };

    xhr.timeout = 300000; // 5 minutes max
    xhr.send(formData);

  } catch (err) {
    onUploadError(`Erreur inattendue : ${err.message}`);
  }
}

function onUploadSuccess(response) {
  uploadState.isUploading = false;
  uploadState.isDone      = true;

  setProgress(100, 'Vidéo envoyée avec succès !');

  DOM.btnUpload.classList.remove('uploading');
  DOM.btnUpload.classList.add('success');
  DOM.btnUpload.innerHTML = `
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="20 6 9 17 4 12"/>
    </svg>
    Vidéo envoyée
  `;

  markStep1Done();

  const projectName = DOM.selectProject.options[DOM.selectProject.selectedIndex]?.text || '';
  DOM.topbarProject.textContent = projectName;

  if (response.id) {
    document.getElementById('current-job-id').value = response.id;
    history.pushState({ jobId: response.id }, '', `/cockpit/${response.id}/`);
  }

  if (response.title) {
    DOM.topbarProject.textContent = `${projectName} — ${response.title}`;
  }

  // Désactiver la drop zone — plus possible de changer la vidéo
  DOM.dropZone.style.pointerEvents = 'none';
  DOM.dropZone.style.opacity       = '0.7';
  DOM.fileInput.disabled           = true;
  DOM.btnCancel.classList.remove('show');

  // Ajouter lien "Nouvelle vidéo"
  const newJobLink = document.createElement('a');
  newJobLink.href      = '/cockpit/';
  newJobLink.className = 'btn-new-job';
  newJobLink.innerHTML = `
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
    </svg>
    Créer une nouvelle vidéo`;
  DOM.btnUpload.parentNode.insertBefore(newJobLink, DOM.btnUpload.nextSibling);

  // Déverrouiller l'étape 2
  if (typeof window.unlockTranscription === 'function') {
    window.unlockTranscription();
  }
}

function onUploadError(msg) {
  uploadState.isUploading = false;

  // Reset bouton
  DOM.btnUpload.classList.remove('uploading');
  DOM.btnUpload.disabled = false;
  DOM.btnUpload.textContent = 'Envoyer la vidéo';
  DOM.btnCancel.classList.add('show');
  DOM.fileInput.disabled = false;

  hideProgress();
  showError(msg);
}

/* ═══════════════════════════════════════════════════
   WORKFLOW — MARQUER ÉTAPE 1 DONE
═══════════════════════════════════════════════════ */

function markStep1Done() {
  // Section étape 1
  const wsec1 = document.getElementById('wsec-1');
  if (wsec1) {
    wsec1.classList.remove('active');
    wsec1.classList.add('done');
  }

  // Badge numéro
  const badge = wsec1?.querySelector('.wsec-badge-num');
  if (badge) {
    badge.classList.remove('active');
    badge.classList.add('done');
    badge.innerHTML = `
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <polyline points="20 6 9 17 4 12"/>
      </svg>`;
  }

  // Statut
  if (DOM.wsec1Status) {
    DOM.wsec1Status.innerHTML = `
      <span class="wsec-done-badge">
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <polyline points="20 6 9 17 4 12"/>
        </svg>
        Fait
      </span>`;
  }

  // Barre de progression globale → 100% (étape 1 terminée = 25%)
  const fill = document.getElementById('workflow-progress-fill');
  if (fill) fill.style.width = '25%';

  // Pills topbar
  const pill1 = document.getElementById('pill-1');
  if (pill1) {
    pill1.classList.remove('active');
    pill1.classList.add('done');
  }

  // Progression text
  const progressText = document.getElementById('workflow-progress-text');
  if (progressText) progressText.textContent = 'Étape 1 terminée';
}

/* ═══════════════════════════════════════════════════
   PLAYER — TRANSPORT
═══════════════════════════════════════════════════ */

function togglePlay() {
  const vid = DOM.videoPlayer;
  if (!vid || !vid.src) return;

  const icon = document.getElementById('play-icon');

  if (vid.paused) {
    vid.play();
    if (icon) icon.innerHTML = `
      <rect x="6" y="4" width="4" height="16"/>
      <rect x="14" y="4" width="4" height="16"/>`;
  } else {
    vid.pause();
    if (icon) icon.innerHTML = `<polygon points="5 3 19 12 5 21"/>`;
  }
}

// Sync seek bar
if (DOM.videoPlayer) {
  DOM.videoPlayer.addEventListener('timeupdate', () => {
    const vid = DOM.videoPlayer;
    const sb  = document.getElementById('seek-bar');
    const td  = document.getElementById('time-display');
    if (!vid.duration) return;
    if (sb) sb.value = (vid.currentTime / vid.duration) * 100;
    if (td) {
      const fmt = s => `${String(Math.floor(s/60)).padStart(2,'0')}:${String(Math.floor(s%60)).padStart(2,'0')}`;
      td.textContent = `${fmt(vid.currentTime)} / ${fmt(vid.duration)}`;
    }
  });

  DOM.videoPlayer.addEventListener('ended', () => {
    const icon = document.getElementById('play-icon');
    if (icon) icon.innerHTML = `<polygon points="5 3 19 12 5 21"/>`;
  });

  // Synchroniser l'icône avec l'état réel de la vidéo
  DOM.videoPlayer.addEventListener('play', () => {
    const icon = document.getElementById('play-icon');
    if (icon) icon.innerHTML = `
      <rect x="6" y="4" width="4" height="16"/>
      <rect x="14" y="4" width="4" height="16"/>`;
  });

  DOM.videoPlayer.addEventListener('pause', () => {
    const icon = document.getElementById('play-icon');
    if (icon) icon.innerHTML = `<polygon points="5 3 19 12 5 21"/>`;
  });
}

const seekBar = document.getElementById('seek-bar');
if (seekBar) {
  seekBar.addEventListener('input', () => {
    const vid = DOM.videoPlayer;
    if (vid && vid.duration) {
      vid.currentTime = (seekBar.value / 100) * vid.duration;
    }
  });
}

/* ═══════════════════════════════════════════════════
   ÉVÉNEMENTS
═══════════════════════════════════════════════════ */

// Drag & Drop
DOM.dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  if (!uploadState.isDone) DOM.dropZone.classList.add('dragover');
});

DOM.dropZone.addEventListener('dragleave', () => {
  DOM.dropZone.classList.remove('dragover');
});

DOM.dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  DOM.dropZone.classList.remove('dragover');
  if (uploadState.isDone || uploadState.isUploading) return;
  const file = e.dataTransfer.files[0];
  if (file) onFileSelected(file);
});

// Input file
DOM.fileInput.addEventListener('change', () => {
  if (uploadState.isDone || uploadState.isUploading) return;
  const file = DOM.fileInput.files[0];
  if (file) onFileSelected(file);
});

// Sélection projet
DOM.selectProject.addEventListener('change', () => {
  checkReadyToUpload();
});

// Bouton upload → ouvre le modal titre
DOM.btnUpload.addEventListener('click', () => {
  if (!uploadState.file) {
    showError('Aucun fichier sélectionné.');
    return;
  }
  if (!DOM.selectProject.value) {
    showError('Veuillez sélectionner un projet.');
    return;
  }
  openModalTitle();
});

// Bouton annuler
DOM.btnCancel.addEventListener('click', () => {
  resetAll();
});

// Exposer togglePlay globalement (appelé depuis le HTML)
window.togglePlay = togglePlay;

/* ═══════════════════════════════════════════════════
   RELOAD — Restaurer l'état depuis job_data
═══════════════════════════════════════════════════ */

function restoreJobFromData() {
  const job = window.JOB_DATA;
  if (!job || !job.id) return;

  log(`Reprise du job : "${job.display_name}"`, 'info');

  // Mettre à jour l'état
  uploadState.isDone  = true;
  uploadState.isDone  = true;

  // Sélectionner le bon projet
  if (job.project_id && DOM.selectProject) {
    DOM.selectProject.value = job.project_id;
  }

  // Topbar
  if (DOM.topbarProject) {
    DOM.topbarProject.textContent = job.project_name
      ? `${job.project_name} — ${job.title || job.display_name}`
      : job.display_name;
  }

  // Charger la vidéo dans le player
  if (job.video_url) {
    DOM.videoPlayer.src = job.video_url;
    DOM.videoPlayer.style.display = 'block';
    DOM.placeholder.style.display = 'none';
    DOM.playerHud.style.display   = 'flex';
    DOM.playerTransport.style.display = 'flex';
    DOM.hudFilename.textContent = job.video_filename || job.display_name;
  }

  // Drop zone — afficher le nom du fichier
  DOM.dropZone.classList.add('has-file');
  DOM.dropFilename.textContent = job.video_filename || job.display_name;
  DOM.dropIcon.innerHTML = `
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="20 6 9 17 4 12"/>
    </svg>`;
  DOM.dropText.textContent    = 'Vidéo importée';
  DOM.dropSubtext.textContent = job.video_filename || '';

  // Progression
  setProgress('Vidéo importée — prête pour la transcription', 100, 'done');

  // Marquer étape 1 done
  markStep1Done();

  // Bouton upload → succès
  DOM.btnUpload.classList.add('success');
  DOM.btnUpload.innerHTML = `
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="20 6 9 17 4 12"/>
    </svg>
    Vidéo importée
  `;
  DOM.btnUpload.disabled = true;

  // Mettre à jour le header workflow selon le statut
  const statusLabels = {
    'pending':     'Étape 1 sur 4',
    'uploading':   'Étape 1 sur 4',
    'extracting':  'Étape 2 sur 4',
    'transcribing':'Étape 2 sur 4',
    'transcribed': 'Étape 2 terminée',
    'synthesizing':'Étape 3 sur 4',
    'done':        'Terminé !',
    'error':       'Erreur — relancez',
  };
  const progressPct = {
    'pending': 25, 'uploading': 25,
    'extracting': 50, 'transcribing': 50, 'transcribed': 50,
    'synthesizing': 75, 'done': 100, 'error': 50,
  };
  const label = statusLabels[job.status] || 'Étape 1 sur 4';
  const pct   = progressPct[job.status] || 25;
  const progText = document.getElementById('workflow-progress-text');
  const progFill = document.getElementById('workflow-progress-fill');
  if (progText) progText.textContent = label;
  if (progFill) progFill.style.width = `${pct}%`;

  // Déverrouiller étape 2 seulement si pas encore transcrit
  const doneStatuses = ['transcribed', 'synthesizing', 'done'];
  if (!doneStatuses.includes(job.status)) {
    if (typeof window.unlockTranscription === 'function') {
      window.unlockTranscription();
    }
  }

  // Restaurer waveform si disponible
  if (job.waveform_data?.length && typeof window.drawWaveform === 'function') {
    setTimeout(() => window.drawWaveform(job.waveform_data), 200);
  }
}

// Lancer la restauration au chargement si job_id présent
document.addEventListener('DOMContentLoaded', () => {
  restoreJobFromData();

  // Pré-remplir depuis le dashboard si nouveau job
  const newProjectId   = sessionStorage.getItem('new_job_project_id');
  const newProjectName = sessionStorage.getItem('new_job_project_name');
  if (newProjectId && !window.JOB_DATA?.id) {
    sessionStorage.removeItem('new_job_project_id');
    sessionStorage.removeItem('new_job_project_name');
    window._preselectedProjectId   = newProjectId;
    window._preselectedProjectName = newProjectName;

    // Pré-sélectionner dans le <select>
    if (DOM.selectProject) {
      DOM.selectProject.value = newProjectId;
    }

    // Afficher le nom du projet dans la topbar
    const topbarProject = document.getElementById('topbar-project-name');
    if (topbarProject && newProjectName) topbarProject.textContent = newProjectName;
  }
});