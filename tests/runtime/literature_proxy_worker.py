"""Trust only the local fixture CA while exercising the real proxy worker."""


def fetch(payload):
    import ssl
    from hypoloom.grounding_web import _fetch_in_process
    certificate = payload.pop("fixture_ca", None)
    if certificate is not None:
        ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certificate)
    return _fetch_in_process(payload)
