"""
Middleware pour supporter les byte-range requests sur les fichiers media.
Permet le seek dans les vidéos en développement.
"""
import os
import re
from django.http import StreamingHttpResponse, HttpResponse
from django.conf import settings


class RangeFileResponse(StreamingHttpResponse):
    pass


class ByteRangeMiddleware:
    """Supporte les requêtes Range pour les fichiers media (seek vidéo)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Seulement pour les fichiers media vidéo
        if not request.path.startswith('/media/') and not request.path.startswith('/outputs/'):
            return response

        if response.status_code != 200:
            return response

        range_header = request.META.get('HTTP_RANGE', '')
        if not range_header:
            # Ajouter Accept-Ranges pour indiquer le support
            response['Accept-Ranges'] = 'bytes'
            return response

        # Parser Range: bytes=start-end
        match = re.match(r'bytes=(\d*)-(\d*)', range_header)
        if not match:
            return response

        # Trouver le fichier sur le disque
        if request.path.startswith('/media/'):
            file_path = os.path.join(settings.MEDIA_ROOT, request.path[7:])
        else:
            rel = request.path.split('/outputs/', 1)[-1].split('?')[0]
            file_path = os.path.join(str(settings.OUTPUTS_ROOT), rel)

        if not os.path.exists(file_path):
            return response

        file_size = os.path.getsize(file_path)
        start_str, end_str = match.groups()

        start = int(start_str) if start_str else 0
        end   = int(end_str) if end_str else file_size - 1
        end   = min(end, file_size - 1)
        length = end - start + 1

        # Extension → content type
        ext = os.path.splitext(file_path)[1].lower()
        content_type = {
            '.mp4': 'video/mp4', '.webm': 'video/webm',
            '.mp3': 'audio/mpeg', '.wav': 'audio/wav',
        }.get(ext, 'application/octet-stream')

        def file_iterator(path, offset, length, chunk=8192):
            with open(path, 'rb') as f:
                f.seek(offset)
                remaining = length
                while remaining > 0:
                    data = f.read(min(chunk, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        resp = StreamingHttpResponse(
            file_iterator(file_path, start, length),
            status=206,
            content_type=content_type,
        )
        resp['Content-Range']  = f'bytes {start}-{end}/{file_size}'
        resp['Content-Length'] = str(length)
        resp['Accept-Ranges']  = 'bytes'
        return resp