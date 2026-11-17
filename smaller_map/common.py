"""Errors and console progress shared by generation stages."""

class UpdateError(RuntimeError):
    pass


def log(message: str) -> None:
    print(message, flush=True)
