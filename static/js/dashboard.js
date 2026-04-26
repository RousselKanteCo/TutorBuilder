/**
 * dashboard.js — Dashboard TutoBuilder
 * Structure 2 colonnes : projets à gauche, tutoriels à droite
 */
'use strict';

const CSRF = document.getElementById('csrf-token').value;
const JOBS_PER_PAGE = 5;

let currentProjectId   = null;
let currentProjectName = '';
let allJobs            = [];
let jobsPage           = 1;

/* ═══════════════════════════════════════════════════
   SÉLECTION PROJET
═══════════════════════════════════════════════════ */

function selectProject(projectId, projectName, jobsData) {
  currentProjectId   = projectId;
  currentProjectName = projectName;
  allJobs            = jobsData;
  jobsPage           = 1;

  // Mettre à jour la sélection visuelle
  document.querySelectorAll('.project-item').forEach(el => {
    el.classList.toggle('active', el.dataset.projectId === projectId);
  });

  renderJobs();
}

/* ═══════════════════════════════════════════════════
   RENDU TUTORIELS
═══════════════════════════════════════════════════ */

function renderJobs() {
  const colJobs = document.getElementById('col-jobs');
  if (!colJobs) return;

  // Afficher bouton nouveau tuto
  const btnNew = document.getElementById('btn-new-job');
  if (btnNew) btnNew.style.display = 'flex';

  const totalPages = Math.ceil(allJobs.length / JOBS_PER_PAGE);
  const start      = (jobsPage - 1) * JOBS_PER_PAGE;
  const pageJobs   = allJobs.slice(start, start + JOBS_PER_PAGE);

  // Header
  const header = colJobs.querySelector('.jobs-col-header');
  if (header) {
    header.querySelector('.jobs-col-title').textContent    = currentProjectName;
    header.querySelector('.jobs-col-subtitle').textContent = `${allJobs.length} tutoriel${allJobs.length > 1 ? 's' : ''}`;
  }

  // Grille
  const grid = colJobs.querySelector('.jobs-grid');
  grid.innerHTML = '';

  pageJobs.forEach(job => {
    const card = document.createElement('a');
    card.href      = `/cockpit/${job.id}/`;
    card.className = 'job-card';
    card.id        = `job-${job.id}`;
    card.innerHTML = `
      <div class="job-card-title">${escHtml(job.display_name)}</div>
      <div class="job-card-file">${escHtml(job.video_filename || '')}</div>
      <div class="job-card-footer">
        <span class="status-badge ${job.status}">
          <span class="status-dot"></span>${escHtml(job.status_display)}
        </span>
        <span class="job-card-date">${escHtml(job.created_at)}</span>
      </div>`;
    grid.appendChild(card);
  });

  // Carte nouveau tutoriel
  const newCard = document.createElement('div');
  newCard.className = 'job-card-new';
  newCard.onclick   = () => newJob();
  newCard.innerHTML = `
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
      <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
    </svg>
    Nouveau tutoriel`;
  grid.appendChild(newCard);

  // Pagination tutoriels
  renderJobsPagination(totalPages);
}

function renderJobsPagination(totalPages) {
  const pag = document.getElementById('jobs-pagination');
  if (!pag) return;
  pag.innerHTML = '';
  if (totalPages <= 1) return;

  // Précédent
  if (jobsPage > 1) {
    const btn = mkPageBtn('‹', () => { jobsPage--; renderJobs(); });
    pag.appendChild(btn);
  }

  // Numéros
  for (let i = 1; i <= totalPages; i++) {
    const btn = mkPageBtn(i, () => { jobsPage = i; renderJobs(); });
    if (i === jobsPage) btn.classList.add('active');
    pag.appendChild(btn);
  }

  // Suivant
  if (jobsPage < totalPages) {
    const btn = mkPageBtn('›', () => { jobsPage++; renderJobs(); });
    pag.appendChild(btn);
  }
}

function mkPageBtn(label, onClick) {
  const btn = document.createElement('button');
  btn.className   = 'page-btn';
  btn.textContent = label;
  btn.onclick     = onClick;
  return btn;
}

function newJob() {
  if (!currentProjectId) return;
  sessionStorage.setItem('new_job_project_id',   currentProjectId);
  sessionStorage.setItem('new_job_project_name', currentProjectName);
  window.location.href = '/cockpit/';
}

/* ═══════════════════════════════════════════════════
   MODALES
═══════════════════════════════════════════════════ */

function openModal(id) {
  document.getElementById(id).classList.add('open');
  const input = document.querySelector(`#${id} .field-input:not([disabled])`);
  if (input) setTimeout(() => input.focus(), 80);
}

function closeModal(id) {
  document.getElementById(id).classList.remove('open');
  const errEl = document.querySelector(`#${id} .modal-error`);
  if (errEl) { errEl.textContent = ''; errEl.style.display = 'none'; }
}

/* ═══════════════════════════════════════════════════
   CRÉER PROJET
═══════════════════════════════════════════════════ */

async function createProject() {
  const name  = document.getElementById('project-name').value.trim();
  const desc  = document.getElementById('project-desc').value.trim();
  const errEl = document.getElementById('project-error');

  if (!name) {
    errEl.textContent = 'Le nom est requis.';
    errEl.style.display = 'block';
    return;
  }

  try {
    const res  = await fetch('/api/projects/create/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ name, description: desc }),
    });
    const data = await res.json();

    if (!res.ok) {
      errEl.textContent = data.error || 'Erreur.';
      errEl.style.display = 'block';
      return;
    }

    closeModal('modal-project');
    showToast('Projet créé !', 'success');
    setTimeout(() => location.reload(), 400);
  } catch (e) {
    errEl.textContent = `Erreur : ${e.message}`;
    errEl.style.display = 'block';
  }
}

/* ═══════════════════════════════════════════════════
   SUPPRIMER PROJET
═══════════════════════════════════════════════════ */

async function deleteProject(event, projectId, name) {
  event.stopPropagation();
  if (!confirm(`Supprimer "${name}" et tous ses tutoriels ?`)) return;

  try {
    const res = await fetch(`/api/projects/${projectId}/delete/`, {
      method: 'DELETE', headers: { 'X-CSRFToken': CSRF },
    });
    if (res.ok) {
      showToast('Projet supprimé.', 'success');
      setTimeout(() => location.reload(), 400);
    }
  } catch (e) {
    showToast(`Erreur : ${e.message}`, 'error');
  }
}

/* ═══════════════════════════════════════════════════
   UTILS
═══════════════════════════════════════════════════ */

function escHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function showToast(msg, type = 'success') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}

/* ═══════════════════════════════════════════════════
   INIT
═══════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', () => {
  // Fermer modale overlay
  document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', e => {
      if (e.target === overlay) overlay.classList.remove('open');
    });
  });

  // Enter pour valider
  document.getElementById('project-name')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') createProject();
  });

  // Echap ferme
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape')
      document.querySelectorAll('.modal-overlay.open').forEach(m => m.classList.remove('open'));
  });

  // Sélectionner le premier projet par défaut
  const first = document.querySelector('.project-item');
  if (first) first.click();
});