# cython: language_level=3, boundscheck=False, wraparound=False
"""
Cython-compiled prompt builder for Gemma instruct format.
Replaces the pure-Python build_prompt() in server.py.
"""

def build_prompt(list messages) -> str:
    """
    Convert a list of {role, content} dicts/objects into a Gemma instruct prompt.
    Accepts both dicts and objects with .role / .content attributes.
    """
    cdef list parts = []
    cdef str role, content

    for msg in messages:
        if hasattr(msg, "role"):
            role    = msg.role
            content = msg.content
        else:
            role    = msg["role"]
            content = msg["content"]

        if role == "user" or role == "system":
            parts.append(f"<start_of_turn>user\n{content}<end_of_turn>\n")
        elif role == "assistant":
            parts.append(f"<start_of_turn>model\n{content}<end_of_turn>\n")

    parts.append("<start_of_turn>model\n")
    return "".join(parts)


def word_stream(str text):
    """
    Yield space-separated words from text — used for fake streaming responses.
    """
    cdef list words = text.split(" ")
    cdef int i, n = len(words)
    for i in range(n):
        yield words[i] + " "


def strip_response(str text) -> str:
    """Fast strip of leading/trailing whitespace from a decoded response."""
    return text.strip()
