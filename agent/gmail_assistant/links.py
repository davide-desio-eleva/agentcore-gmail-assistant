"""Links in what the automatic agents write.

The digest and the reminder agents read the raw text of emails, and a stranger chose that text. A link that comes
from an email, in a message that looks like it comes from me, is how a phishing attempt would reach me. So the only
link that is left is one to Gmail itself, which the reminder prompt builds from the id of the email.

A domain written without a scheme is not a link for a regex, but Telegram makes it one, so its dots are bracketed.
The domains of a few well-known senders are left as they are, so that "Amazon.it" in a digest stays readable.
"""

import re

# Only the path the reminder builds. Other Gmail paths, such as /url?q=..., can send the reader somewhere else.
GMAIL_PREFIX = "https://mail.google.com/mail/"
REMOVED = "[link removed]"

_MARKDOWN_LINK = re.compile(r"\[([^\]\n]*)\]\(((?:https?://|www\.)[^)\s]*)\)", re.IGNORECASE)
_URL = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]]+", re.IGNORECASE)
# Only real top-level domains, so file names (report.pdf) and numbers (v1.2) stay as they are. A name after an @ is an
# address, which cannot be opened as a link.
_TLDS = (
    "com|net|org|info|biz|edu|gov|io|co|me|us|uk|it|de|fr|es|nl|be|ch|at|eu|ru|cn|jp|in|br|au|ca|pl|se|no|dk|fi|pt|"
    "xyz|top|online|site|shop|store|app|dev|cloud|link|click|live|club|icu|vip|work|tech|page|ly|cc|tk|ml|ga|cf|gq|su"
)
_DOMAIN = re.compile(rf"(?<![@\w.\-/])(?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+(?:{_TLDS})\b", re.IGNORECASE)
_LINK_OR_DOMAIN = re.compile(rf"{_URL.pattern}|{_DOMAIN.pattern}", re.IGNORECASE)


# Exact matches only: amazon-pagamenti.it or amazon.it.evil.com are not in it, and are bracketed like any other domain.
SAFE_DOMAINS = {
    "amazon.it", "amazon.com", "amazon.de", "amazon.fr", "amazon.es", "amazon.co.uk",
    "google.com", "gmail.com", "apple.com", "microsoft.com", "linkedin.com", "paypal.com", "netflix.com",
    "spotify.com", "github.com", "poste.it", "inps.it", "agenziaentrate.gov.it", "enel.it", "tim.it",
}


def _keep(url: str) -> bool:
    return url.startswith(GMAIL_PREFIX)


def strip_links(text: str) -> str:
    """Remove every link that does not point to Gmail. A link written as Markdown keeps its words."""
    text = _MARKDOWN_LINK.sub(lambda m: m.group(0) if _keep(m.group(2)) else m.group(1), text)

    def replace(match: re.Match) -> str:
        found = match.group(0)
        if _URL.fullmatch(found):
            return found if _keep(found) else REMOVED
        return found if found.lower() in SAFE_DOMAINS else found.replace(".", "[.]")

    return _LINK_OR_DOMAIN.sub(replace, text)
