from gmail_assistant.links import REMOVED, strip_links


def test_a_gmail_link_is_kept():
    text = "Link: https://mail.google.com/mail/u/0/#all/18c4a1b2d3e4f567"
    assert strip_links(text) == text


def test_other_links_are_removed():
    assert strip_links("Paga qui: https://evil.example/pay?id=1 subito") == f"Paga qui: {REMOVED} subito"
    assert strip_links("vedi www.evil.example/x oggi") == f"vedi {REMOVED} oggi"


def test_a_markdown_link_keeps_its_words_and_loses_the_address():
    assert strip_links("[Fattura](https://evil.example/f)") == "Fattura"
    gmail = "[Apri](https://mail.google.com/mail/u/0/#all/18c4a1b2d3e4f567)"
    assert strip_links(gmail) == gmail


def test_text_without_links_is_unchanged():
    text = "Bolletta luce, 84,20 euro, scadenza 10 novembre."
    assert strip_links(text) == text


def test_a_gmail_redirect_is_removed():
    assert strip_links("Vai qui https://mail.google.com/url?q=https://evil.example") == "Vai qui [link removed]"


def test_a_bare_domain_is_made_inert():
    assert strip_links("Paga su evil.com/pay oggi") == "Paga su evil[.]com/pay oggi"
    assert strip_links("vai a Login.Banca-Falsa.it ora") == "vai a Login[.]Banca-Falsa[.]it ora"


def test_ordinary_text_with_dots_is_unchanged():
    for text in ("Allegato report.pdf, versione v1.2", "Alle 10.30 di oggi, circa 84.20 euro", "Es. e.g. fine."):
        assert strip_links(text) == text


def test_an_email_address_is_not_a_link():
    assert strip_links("scrivi a mario@azienda.com") == "scrivi a mario@azienda.com"


def test_a_gmail_link_keeps_its_dots():
    text = "[Apri](https://mail.google.com/mail/u/0/#all/18c4a1b2d3e4f567) e https://mail.google.com/mail/u/0/#inbox"
    assert strip_links(text) == text


def test_the_domain_of_a_well_known_sender_stays_readable():
    assert strip_links("Conferma da Amazon.it e da poste.it") == "Conferma da Amazon.it e da poste.it"


def test_a_lookalike_of_a_known_domain_is_still_bracketed():
    assert strip_links("scrivi a amazon-pagamenti.it") == "scrivi a amazon-pagamenti[.]it"
    assert strip_links("vai su amazon.it.evil.com") == "vai su amazon[.]it[.]evil[.]com"
