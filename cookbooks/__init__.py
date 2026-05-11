"""Personal Finance Helper cookbooks."""
from __future__ import annotations

import warnings

# LangGraph 1.x prints a noisy LangChainPendingDeprecationWarning at import
# time for JsonPlusSerializer's `allowed_objects` default. We don't construct
# the serializer ourselves — LangGraph does — so the warning is purely
# upstream churn. Silence it here at the package root so every CLI / API
# entry point inherits the filter.
#
# We pre-import langchain_core so its module-level filter registration runs
# first (otherwise our filter gets overridden) and then drop our own ignore
# filter at the front of the chain.
try:
    import langchain_core._api.deprecation as _lc_deprecation  # noqa: F401
    warnings.filterwarnings(
        "ignore",
        message=r"The default value of `allowed_objects` will change",
        category=_lc_deprecation.LangChainPendingDeprecationWarning,
    )
except ImportError:
    # langchain_core not installed — fall back to the generic base class.
    warnings.filterwarnings(
        "ignore",
        message=r"The default value of `allowed_objects` will change",
        category=PendingDeprecationWarning,
    )
