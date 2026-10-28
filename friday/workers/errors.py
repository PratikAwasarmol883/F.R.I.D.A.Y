class TaskCancelled(Exception):
    """The task was cancelled; stop work and let the runner record the outcome."""


class LeaseLostError(Exception):
    """This worker no longer holds the task's lease; discard all work and write nothing."""
