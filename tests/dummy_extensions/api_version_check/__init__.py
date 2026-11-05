"""A dummy extension that registers only on an extension API it knows.

Used by tests/test_ext_api_version.py. It shows the check every extension
outside ACP should make before it touches anything else.
"""
import logging

KNOWN_APIS = {1}
log = logging.getLogger("acp.extensions.api_version_check")


def register(app):
    api = getattr(app, "acp_extension_api", None)
    if api not in KNOWN_APIS:
        log.warning("api_version_check: extension API %r is not one I know (%s); not registering",
                    api, sorted(KNOWN_APIS))
        return
    app.config["API_VERSION_CHECK_REGISTERED"] = True
