/**
 * toast.js — Notifications Toastify centralisées
 * Tous les messages sont en français.
 * Chaque toast est aussi envoyé au journal (log-area).
 */

'use strict';

/* ═══════════════════════════════════════════════════
   CONFIG
═══════════════════════════════════════════════════ */

const TOAST_DURATION = {
  error:   7000,
  success: 3500,
  warn:    5000,
  info:    3000,
};

const TOAST_COLORS = {
  error:   { bg: '#FEF2F2', border: '#FCA5A5', text: '#991B1B' },
  success: { bg: '#F0FDF4', border: '#86EFAC', text: '#166534' },
  warn:    { bg: '#FFFBEB', border: '#FCD34D', text: '#92400E' },
  info:    { bg: '#EFF6FF', border: '#93C5FD', text: '#1E40AF' },
};

const TOAST_ICONS = {
  error:   '✕',
  success: '✓',
  warn:    '⚠',
  info:    'ℹ',
};

/* ═══════════════════════════════════════════════════
   FONCTION PRINCIPALE
═══════════════════════════════════════════════════ */

function showToast(message, type = 'info', duration = null) {
  const colors = TOAST_COLORS[type] || TOAST_COLORS.info;
  const icon   = TOAST_ICONS[type]  || 'ℹ';
  const dur    = duration || TOAST_DURATION[type] || 3000;

  Toastify({
    text:      `${icon}  ${message}`,
    duration:  dur,
    gravity:   'top',
    position:  'right',
    stopOnFocus: true,
    style: {
      background:   colors.bg,
      border:       `1px solid ${colors.border}`,
      color:        colors.text,
      borderRadius: '8px',
      padding:      '10px 16px',
      fontSize:     '12.5px',
      fontFamily:   "'DM Sans', sans-serif",
      fontWeight:   '500',
      boxShadow:    '0 4px 20px rgba(0,0,0,.08)',
      maxWidth:     '380px',
      lineHeight:   '1.5',
    },
  }).showToast();

  // Envoyer aussi au journal
  logToJournal(message, type);
}

/* ═══════════════════════════════════════════════════
   JOURNAL
═══════════════════════════════════════════════════ */

function logToJournal(msg, type = '') {
  const logArea = document.getElementById('log-area');
  if (!logArea) return;

  const empty = logArea.querySelector('.log-empty');
  if (empty) empty.remove();

  const entry = document.createElement('div');
  entry.className = `log-entry${type ? ' ' + type : ''}`;

  const now = new Date();
  const ts  = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}`;

  entry.innerHTML = `
    <span class="log-time">${ts}</span>
    <span class="log-msg">${msg}</span>
  `;

  logArea.appendChild(entry);
  logArea.scrollTop = logArea.scrollHeight;
}

/* ═══════════════════════════════════════════════════
   MESSAGES D'ERREUR UPLOAD — EN FRANÇAIS
═══════════════════════════════════════════════════ */

const UPLOAD_ERRORS = {
  // Fichier
  no_file:          'Aucun fichier sélectionné. Déposez une vidéo dans la zone d\'import.',
  bad_format:       (name) => `Format non supporté : "${name}". Formats acceptés : MP4, MKV, AVI, MOV, WMV, WebM.`,
  too_large:        (mb) => `Fichier trop volumineux : ${mb} Mo. La limite est de 200 Mo.`,
  corrupted:        'Ce fichier semble corrompu ou vide. Vérifiez que la vidéo est lisible.',
  no_video_track:   'Ce fichier ne contient pas de piste vidéo. Vérifiez qu\'il s\'agit bien d\'une vidéo.',

  // Formulaire
  no_project:       'Veuillez sélectionner un projet avant d\'envoyer la vidéo.',
  title_empty:      'Le titre est obligatoire. Donnez un nom à ce tutoriel.',
  title_too_short:  'Le titre est trop court (minimum 3 caractères).',
  title_too_long:   'Le titre est trop long (maximum 255 caractères).',
  title_invalid:    'Le titre contient des caractères non autorisés.',

  // Réseau
  network_error:    'Impossible de joindre le serveur. Vérifiez votre connexion internet.',
  timeout:          'L\'envoi a pris trop de temps. Vérifiez votre connexion et réessayez.',
  session_expired:  'Votre session a expiré. Vous allez être redirigé vers la page de connexion.',

  // Serveur
  server_error:     'Une erreur est survenue sur le serveur. Réessayez dans quelques instants.',
  project_not_found:'Projet introuvable ou vous n\'y avez pas accès.',
  disk_full:        'Espace disque insuffisant sur le serveur. Contactez l\'administrateur.',

  // Doublon
  duplicate_found:  'Cette vidéo a déjà été importée. Voulez-vous la réutiliser ?',
  reuse_error:      'Impossible de réutiliser la vidéo existante. Réessayez.',
};

/* ═══════════════════════════════════════════════════
   EXPORTS GLOBAUX
═══════════════════════════════════════════════════ */

window.Toast = {
  error:   (msg, dur)  => showToast(msg, 'error',   dur),
  success: (msg, dur)  => showToast(msg, 'success', dur),
  warn:    (msg, dur)  => showToast(msg, 'warn',    dur),
  info:    (msg, dur)  => showToast(msg, 'info',    dur),
  log:     logToJournal,
  ERRORS:  UPLOAD_ERRORS,
};