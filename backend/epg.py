# =============================================================================
# E2iPTV Backend
# EPG Entry Record
# =============================================================================

import time


class Epg(object):

    def __init__(self):

        self.stream_id  = ""
        self.title      = ""
        self.description = ""
        self.start      = 0    # unix timestamp
        self.end        = 0    # unix timestamp

    def __str__(self):
        return "[%s-%s] %s" % (
            time.strftime("%H:%M", time.localtime(self.start)),
            time.strftime("%H:%M", time.localtime(self.end)),
            self.title
        )
