"""Text utilities that are not neural-analysis steps."""


def count_words(text: str) -> int:
    """Count words in a string of prose."""
    return len(text.split())
