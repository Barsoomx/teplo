import warnings

from settings.asgi import silence_static_streaming_warning


def test_silence_static_streaming_warning_ignores_only_that_warning() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        silence_static_streaming_warning()
        warnings.warn(
            'StreamingHttpResponse must consume synchronous iterators in order to serve them asynchronously. '
            'Use an asynchronous iterator instead.',
            Warning,
            stacklevel=1,
        )
        warnings.warn('Another warning', Warning, stacklevel=1)

    assert [str(warning.message) for warning in caught] == ['Another warning']
