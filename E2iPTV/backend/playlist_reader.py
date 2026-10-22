# =============================================================================
# IE2iPTV Backend
# Playlist Reader
# =============================================================================

import re

from .provider import Provider


def load_providers(path):
    # path is required, deliberately — no fallback default here or
    # anywhere else in this codebase.

    providers  = []
    provider_id = 1
    provider_idx = 0

    with open(path, "r") as f:

        for raw_line in f:

            try:
                line = raw_line.strip()

                # Support disabled providers prefixed with #
                disabled = False
                if line.startswith("#http"):
                    line     = line[1:]
                    disabled = True
                elif not line.startswith("http"):
                    continue

                p = Provider()

                # Existing numeric ID retained for now.
                p.provider_id = provider_id

                # Playlist position is explicitly separate from identity.
                p.provider_idx = provider_idx

                p.enabled = not disabled
                p.disabled_in_file = disabled     # "#http" in playlists.txt

                m = re.search(r'#(.+)$', line)
                if m:
                    p.name = m.group(1).strip()
                else:
                    p.name = "Provider %03d" % provider_id

                # Stalker/Ministra
                m = re.search(r'mac=([0-9A-Fa-f:]{17})', line)
                if m:
                    p.source_type = "STALKER"
                    p.mac         = m.group(1).upper()
                    p.base_url    = line.split("?")[0].rstrip("/")

                else:
                    # Xtream Codes
                    m = re.search(r'username=([^&\s#]+)', line)
                    if m:
                        p.username = m.group(1)

                    m = re.search(r'password=([^&\s#]+)', line)
                    if m:
                        p.password = m.group(1)

                    p.base_url = line.split("/get.php")[0]

                # Stable identity is based on the actual provider
                # connection details, never on playlist position.
                p.build_provider_key()

                providers.append(p)

                provider_id += 1
                provider_idx += 1

            except Exception:
                # Skip this one line only.
                continue

    return providers
