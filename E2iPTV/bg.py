# =============================================================================
# E2iPTV — run a job the user is waiting for, straight away
#
# Twisted's shared worker pool (reactor.callInThread) is about 10 threads,
# and the guide downloads use it heavily (6 at a time from Live TV, one per
# visible channel from the grid). With a slow provider they can all sit
# waiting on the network, and anything else queued behind them — like
# opening a category — waits too. Jobs the user is actively waiting for go
# through run_now() instead, which gives them a thread of their own.
# =============================================================================

import threading


def run_now(fn, *args, **kwargs):
    t = threading.Thread(target=fn, args=args, kwargs=kwargs)
    t.daemon = True
    t.start()
    return t
