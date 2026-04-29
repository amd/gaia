class AuthRequiredError(Exception):
    def __init__(self, reason: str, message: str | None = None):
        self.reason = reason
        super().__init__(message or reason)


class ConnectionRevokedError(Exception):
    pass


class ScopeMismatchError(Exception):
    pass
