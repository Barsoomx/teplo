import warnings

from settings import asgi

STATIC_STREAMING_WARNING = (
    'StreamingHttpResponse must consume synchronous iterators in order to serve them asynchronously. '
    'Use an asynchronous iterator instead.'
)


def test_asgi_application_silences_the_static_streaming_warning() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.warn(STATIC_STREAMING_WARNING, Warning, stacklevel=1)

    assert (asgi.application is not None, caught) == (True, [])
