import os
import warnings

from django.core.asgi import get_asgi_application

STATIC_STREAMING_WARNING = 'StreamingHttpResponse must consume synchronous iterators'


def silence_static_streaming_warning() -> None:
    # whitenoise streams static files from sync iterators; Django buffers them under ASGI and would warn on every file
    warnings.filterwarnings('ignore', message=STATIC_STREAMING_WARNING, category=Warning)


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings.settings')
silence_static_streaming_warning()

application = get_asgi_application()
