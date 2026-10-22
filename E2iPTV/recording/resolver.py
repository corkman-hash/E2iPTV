# =============================================================================
# E2iPTV
# Recording Resolver
# =============================================================================



from ..paths import LOG  # data folder / debug logs
class RecordingResolver(object):

    def __init__(self, manager):

        self.manager = manager

    # -------------------------------------------------------------------------

    def _log(self, text):

        try:

            with open(
                LOG("e2iptv_resolver.log"),
                "a"
            ) as f:

                f.write(text + "\n")

        except Exception:

            pass

    # -------------------------------------------------------------------------

    def resolve_provider(self, provider_id):

        from ..state import state

        for name, provider in state.providers:

            if provider.provider_id == provider_id:

                return provider

        return None

    # -------------------------------------------------------------------------

    def resolve(self, rule):

        from ..state import state

        self._log(
            "Resolving rule %s" %
            rule.rule_id
        )

        #
        # Providers are normally loaded when the E2iPTV app screen is
        # opened (screen1.py), not automatically at box boot. If a
        # recording is due while the app hasn't been opened since
        # startup, the provider store is empty and resolution would
        # fail every time. Load it here on demand so scheduled
        # recordings work whether or not the app is open.
        #

        if not state.backend.providers():

            self._log(
                "Provider store empty, loading providers"
            )

            try:

                state.load_providers()

            except Exception as e:

                self._log(
                    "load_providers() failed: %s" %
                    str(e)
                )

        try:
            self.manager.sync_rule_providers()
        except Exception:
            pass

        provider = state.backend.find_provider(
            rule.provider_id
        )

        if provider is None:

            self._log(
                "Provider %s not found" %
                rule.provider_id
            )

            return (
                None,
                None,
                None
            )

        channel = state.backend.find_live_channel(
            provider,
            rule.stream_id
        )

        if channel is None:

            self._log(
                "Channel %s not found" %
                rule.stream_id
            )

            return (
                provider,
                None,
                None
            )

        event = state.backend.find_epg_event(
            provider,
            channel,
            rule.event_start,
            rule.event_end
        )

        if event is None:

            self._log(
                "EPG event not found"
            )

        else:

            self._log(
                "Resolved OK"
            )

        return (
            provider,
            channel,
            event
        )
