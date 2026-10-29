import os
import warnings

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings.settings')

# whitenoise streams static files from sync iterators; Django buffers them under ASGI and would warn on every file
warnings.filterwarnings('ignore', message='StreamingHttpResponse must consume synchronous iterators', category=Warning)

application = get_asgi_application()
