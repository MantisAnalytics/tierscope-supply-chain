class LLMError(Exception):
    """Base class for gateway errors."""


class UnsupportedFeatureError(LLMError, ValueError):
    """The request uses something outside the portable subset."""


class MissingCredentialsError(LLMError):
    pass


class PriceMissingError(LLMError):
    pass


class BudgetExceededError(LLMError):
    pass


class CacheMissError(LLMError):
    pass


class ProviderError(LLMError):
    """The provider call failed (auth, network, bad request, ...)."""


class PolicyError(LLMError):
    """The provider refused the request under its content policy, or restricted the key for policy violations.
    Never retried: sending the same request again counts as another violation."""


class BillingError(LLMError):
    """The provider refused the call because the account has no credit left. Stops a benchmark like a budget."""
