"""apps/studio/views/tutorials.py — Gestion des tutoriels vidéo TechVallée."""

import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny

from ..models import Tutorial

logger = logging.getLogger(__name__)


def serialize_tutorial(t):
    return {
        "id":    t.id,
        "title": t.title,
        "desc":  t.desc,
        "cat":   t.cat,
        "lv":    t.lv,
        "src":   t.src,
        "img":   t.img,
        "tags":  t.tags,
        "order": t.order,
    }


class TutorialPublicListView(APIView):
    """GET public — appelé par le fetch() du site HTML."""
    permission_classes = [AllowAny]

    def get(self, request):
        tutorials = Tutorial.objects.filter(active=True)
        return Response([serialize_tutorial(t) for t in tutorials])


class TutorialListView(APIView):
    """GET tous (actifs + inactifs) — espace admin."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tutorials = Tutorial.objects.all()
        data = [serialize_tutorial(t) | {"active": t.active} for t in tutorials]
        return Response(data)


from rest_framework.parsers import MultiPartParser, FormParser
import json, os, uuid
from django.core.files.storage import default_storage

class TutorialCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser]

    def post(self, request):
        d = request.data
        required = ['title', 'desc', 'cat', 'lv', 'src']
        missing = [f for f in required if not d.get(f)]
        if missing:
            return Response({"error": f"Champs manquants : {', '.join(missing)}"}, status=400)

        img_url = ''
        img_file = request.FILES.get('img')
        if img_file:
            ext      = os.path.splitext(img_file.name)[1].lower() or '.jpg'
            filename = f"tutorials/{uuid.uuid4()}{ext}"
            path     = default_storage.save(filename, img_file)
            img_url  = default_storage.url(path)

        try:
            tags = json.loads(d.get('tags', '[]'))
        except Exception:
            tags = []

        try:
            t = Tutorial.objects.create(
                title  = d['title'],
                desc   = d['desc'],
                cat    = d['cat'],
                lv     = d['lv'],
                src    = d['src'],
                img    = img_url,
                tags   = tags,
                order  = int(d.get('order', 0)),
                active = d.get('active') in [True, 'true', 'True', '1'],
            )
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
            ext      = os.path.splitext(img_file.name)[1].lower() or '.jpg'
            filename = f"tutorials/{uuid.uuid4()}{ext}"
            path     = default_storage.save(filename, img_file)
            t.img    = default_storage.url(path)

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
    
from django.shortcuts import render
from django.contrib.auth.decorators import login_required

@login_required
def tutorials_manage_view(request):
    fields = [
        ("Titre",       "fTitle", "text",        True),
        ("Description", "fDesc",  "textarea",     True),
        ("Catégorie",   "fCat",   "select_cat",   True),
        ("Niveau",      "fLv",    "select_lv",    False),
        ("URL vidéo",   "fSrc",   "url",          True),
        ("URL miniature","fImg",  "url",          False),
        ("Tags",        "fTags",  "text",         False),
        ("Ordre",       "fOrder", "number",       False),
    ]
    return render(request, "studio/tutorials_manage.html", {"fields": fields})

class TechValleePageView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        from django.shortcuts import render
        return render(request, "studio/techvallee.html")
    
    
from django.views.decorators.http import require_GET

@require_GET
def techvallee_view(request):
    return render(request, "studio/techvallee.html")