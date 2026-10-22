# =============================================================================
# IE2iPTV Backend
# Utility Functions
# =============================================================================


def as_int(value, default=0):

    if value is None:
        return default

    if value == "":
        return default

    try:
        return int(value)
    except:
        return default


def as_str(value, default=""):

    if value is None:
        return default

    return str(value)


def as_bool(value):

    return as_int(value) != 0
