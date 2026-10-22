# =============================================================================
# IE2iPTV Backend
# Provider Store
# =============================================================================

from .store_base import StoreBase
from .playlist_reader import load_providers
from .provider_reader import ProviderReader
from .provider_settings import ProviderSettings


class ProviderStore(StoreBase):

    def __init__(self):

        super(ProviderStore, self).__init__()

        self.reader = ProviderReader()
        self.settings = ProviderSettings()

    # -------------------------------------------------------------

    def load(self, path=None):

        self.clear()
        self.settings.load()

        for provider in load_providers(path):

            provider.epg_offset = self.settings.get_epg_offset(
                provider
            )

            self.add(provider)

        return self.count()

    # -------------------------------------------------------------

    def connect(self, provider):

        self.reader.read(provider)

        return provider

    # -------------------------------------------------------------

    def save_epg_offset(self, provider):

        self.settings.set_epg_offset(
            provider,
            provider.epg_offset
        )
