"""Stop-and-summarize message for graceful agent termination.

When an agent loop hits max steps, encounters an error, or is
interrupted, sending a final "stop" message prompts the model to
summarize its progress rather than returning nothing.
"""

from __future__ import annotations


def format_stop_and_summarize(task: str) -> str:
    """Build a stop-and-summarize message for the given task.

    Intended to be sent as a user message after the last tool response
    (with a screenshot) so the model produces a final text summary.

    Example::

        >>> format_stop_and_summarize("Find the cheapest flight to Tokyo")
        'Stop here. Summarize your current progress and list in detail ...'
    """
    return (
        f"Stop here. "
        f"Summarize your current progress and list in detail all the findings "
        f"relevant to the given task:\n{task}\n"
        f"Provide URLs for all relevant results you find and return them in your response. "
        f"If there is no specific URL for a result, "
        f"cite the page URL that the information was found on."
    )
