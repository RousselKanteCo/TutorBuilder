"""apps/studio/views/tutorials.py — Gestion des tutoriels vidéo TechVallée."""

import logging
import json
import os
import uuid

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.parsers import MultiPartParser, FormParser

from django.shortcuts import render as django_render
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from django.core.files.storage import default_storage

from ..models import Tutorial

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
#  SERIALIZER
# ─────────────────────────────────────────

def serialize_tutorial(t):
    return {
        "id":        t.id,
        "title":     t.title,
        "desc":      t.desc,
        "cat":       t.cat,
        "lv":        t.lv,
        "src":       t.src,
        "img":       t.img,
        "tags":      t.tags,
        "order":     t.order,
        "downloads": t.downloads,
    }


# ─────────────────────────────────────────
#  UTILITAIRES
# ─────────────────────────────────────────

def _save_file(file, folder="tutorials"):
    ext      = os.path.splitext(file.name)[1].lower() or '.bin'
    filename = f"{folder}/{uuid.uuid4()}{ext}"
    path     = default_storage.save(filename, file)
    return default_storage.url(path)

def _collect_downloads(data, files):
    """
    Combine :
    - les liens externes JSON envoyés dans data['downloads']
    - les fichiers uploadés : dl_file_0, dl_file_1, ... + dl_label_0, dl_label_1, ...
    """
    downloads = []

    # Liens externes (URL)
    try:
        downloads = json.loads(data.get('downloads', '[]'))
    except Exception:
        downloads = []

    # Fichiers uploadés
    for key in files:
        if key.startswith('dl_file_'):
            idx      = key.replace('dl_file_', '')
            dl_file  = files[key]
            label    = data.get(f'dl_label_{idx}', dl_file.name)
            url      = _save_file(dl_file, folder="tutorials/downloads")
            downloads.append({"label": label, "url": url})

    return downloads


# ─────────────────────────────────────────
#  VUES PUBLIQUES
# ─────────────────────────────────────────

class TutorialPublicListView(APIView):
    """GET public — appelé par le fetch() du site HTML."""
    permission_classes = [AllowAny]

    def get(self, request):
        tutorials = Tutorial.objects.filter(active=True)
        return Response([serialize_tutorial(t) for t in tutorials])


@require_GET
def techvallee_view(request):
    return django_render(request, "studio/techvallee.html")


# ─────────────────────────────────────────
#  VUES ADMIN
# ─────────────────────────────────────────

class TutorialListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tutorials = Tutorial.objects.all()
        data = [serialize_tutorial(t) | {"active": t.active} for t in tutorials]
        return Response(data)


class TutorialCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser]

    def post(self, request):
        d = request.data
        required = ['title', 'desc', 'cat', 'lv', 'src']
        missing  = [f for f in required if not d.get(f)]
        if missing:
            return Response({"error": f"Champs manquants : {', '.join(missing)}"}, status=400)

        img_url = ''
        img_file = request.FILES.get('img')
        if img_file:
            img_url = _save_file(img_file, folder="tutorials")

        try:
            tags = json.loads(d.get('tags', '[]'))
        except Exception:
            tags = []

        downloads = _collect_downloads(d, request.FILES)

        try:
            t = Tutorial.objects.create(
                title     = d['title'],
                desc      = d['desc'],
                cat       = d['cat'],
                lv        = d['lv'],
                src       = d['src'],
                img       = img_url,
                tags      = tags,
                order     = int(d.get('order', 0) or 0),
                active    = d.get('active') in [True, 'true', 'True', '1'],
                downloads = downloads,
            )
            logger.info(f"Tutorial créé : {t.pk} — {t.title}")
            return Response(serialize_tutorial(t), status=201)
        except Exception as e:
            logger.error(f"Erreur création tutorial : {e}", exc_info=True)
            return Response({"error": "Erreur serveur."}, status=500)


class TutorialDetailView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser]

    def _get(self, pk):
        try:
            return Tutorial.objects.get(pk=pk)
        except Tutorial.DoesNotExist:
            return None

    def get(self, request, tutorial_id):
        t = self._get(tutorial_id)
        if not t:
            return Response({"error": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(serialize_tutorial(t) | {"active": t.active})

    def patch(self, request, tutorial_id):
        t = self._get(tutorial_id)
        if not t:
            return Response({"error": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)

        d = request.data

        if 'title'  in d: t.title  = d['title']
        if 'desc'   in d: t.desc   = d['desc']
        if 'cat'    in d: t.cat    = d['cat']
        if 'lv'     in d: t.lv     = d['lv']
        if 'src'    in d: t.src    = d['src']
        if 'order'  in d: t.order  = int(d.get('order', 0) or 0)
        if 'active' in d: t.active = d['active'] in [True, 'true', 'True', '1', 'on']

        if 'tags' in d:
            try:
                t.tags = json.loads(d['tags']) if isinstance(d['tags'], str) else d['tags']
            except Exception:
                t.tags = []

        img_file = request.FILES.get('img')
        if img_file:
            t.img = _save_file(img_file, folder="tutorials")

        # Downloads : on remplace toujours
        t.downloads = _collect_downloads(d, request.FILES)

        try:
            t.save()
            return Response(serialize_tutorial(t) | {"active": t.active})
        except Exception as e:
            logger.error(f"Erreur update tutorial {tutorial_id} : {e}", exc_info=True)
            return Response({"error": "Erreur serveur."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request, tutorial_id):
        t = self._get(tutorial_id)
        if not t:
            return Response({"error": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)
        t.delete()
        logger.info(f"Tutorial supprimé : {tutorial_id}")
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────
#  VUE TEMPLATE GESTION
# ─────────────────────────────────────────

class TechValleePageView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return django_render(request, "studio/techvallee.html")


@login_required
def tutorials_manage_view(request):
    return django_render(request, "studio/tutorials_manage.html")