"""No flag in the application log (found by the end-to-end test of 15 November).

CTFd writes one line to its "submissions" log for every flag attempt, with the text the player sent:

    [11/15/2026 10:02:11] Ravi submitted b'L3m0n{...}' on 7 with kpm 1 [CORRECT]

The line goes to the container's output (and so to whoever reads the logs: the log store, a crew member with the dashboard) and to
a rotating file. For a correct attempt that line IS the flag, and for a wrong one it is often another studio's flag or a flag that is
one character off. The flag stays where the platform needs it (the submissions table of the database, which the crew reads in the
admin panel); the line keeps who, which programme, how fast and what came of it.

The filter sits on the logger itself, so it applies to every handler (the output, the file, anything added later).
"""
import logging
import re

# `submitted b'...'` (CTFd prints the bytes of the answer), then ` on <id> with kpm`; the text between may hold quotes and the word "on",
# so the pattern reaches for the LAST ` on <digits> with kpm` of the line
_SUBMITTED = re.compile(r" submitted b(?P<q>['\"])(?P<text>.*)(?P=q) on (?P<rest>\d+ with kpm )", re.DOTALL)


def redact(message: str) -> str:
    def replace(found):
        return f" submitted [not logged] on {found.group('rest')}"

    return _SUBMITTED.sub(replace, message)


class FlagRedactor(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a record that cannot be formatted is left to the handlers' own error handling
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install() -> None:
    logger = logging.getLogger("submissions")
    if not any(isinstance(existing, FlagRedactor) for existing in logger.filters):
        logger.addFilter(FlagRedactor())
