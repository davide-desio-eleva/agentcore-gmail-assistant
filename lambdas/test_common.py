from common import split_message, to_telegram_html


def test_short_text_is_one_chunk():
    assert split_message("hello\nworld") == ["hello\nworld"]


def test_long_text_is_split_under_the_limit():
    text = "\n".join(f"line {i} " + "x" * 80 for i in range(200))
    chunks = split_message(text, limit=1000)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_single_line_longer_than_limit_is_sliced():
    chunks = split_message("a" * 2500, limit=1000)
    assert [len(chunk) for chunk in chunks] == [1000, 1000, 500]


def test_empty_text_gets_placeholder():
    assert split_message("   ") == ["(empty reply)"]


def test_bold_and_italic_become_html():
    assert to_telegram_html("**Fattura** da *Enel*") == "<b>Fattura</b> da <i>Enel</i>"


def test_headings_become_bold():
    assert to_telegram_html("## 🏥 Sanità\nVisita") == "<b>🏥 Sanità</b>\nVisita"


def test_links_and_code():
    out = to_telegram_html("Vedi [offerta](https://shop.it/x?a=1&b=2) e `ID 42`")
    assert out == 'Vedi <a href="https://shop.it/x?a=1&amp;b=2">offerta</a> e <code>ID 42</code>'


def test_code_block_is_not_reformatted():
    out = to_telegram_html("```\n**non grassetto**\n```")
    assert out == "<pre>**non grassetto**\n</pre>"


def test_special_characters_are_escaped():
    assert to_telegram_html("Tag <script> & sconto") == "Tag &lt;script&gt; &amp; sconto"


def test_bullets_are_not_italics():
    assert to_telegram_html("* primo\n* secondo") == "* primo\n* secondo"


def test_only_the_owner_private_chat_is_accepted():
    from common import is_owner_message

    owner = {"from": {"id": 42, "is_bot": False}, "chat": {"id": 42, "type": "private"}, "text": "ciao"}
    assert is_owner_message(owner, "42")
    group = {"from": {"id": 42, "is_bot": False}, "chat": {"id": -100, "type": "group"}}
    assert not is_owner_message(group, "42")
    other = {"from": {"id": 7, "is_bot": False}, "chat": {"id": 7, "type": "private"}}
    assert not is_owner_message(other, "42")
    bot = {"from": {"id": 42, "is_bot": True}, "chat": {"id": 42, "type": "private"}}
    assert not is_owner_message(bot, "42")


def test_rejection_log_leaves_out_the_text():
    import json
    from common import rejection_log

    line = rejection_log({"from": {"id": 7}, "chat": {"id": 7, "type": "private"}, "text": "segreto"})
    assert "segreto" not in line
    assert json.loads(line)["from_id"] == 7


class FakeTable:
    def __init__(self):
        self.items = {}
        self.calls = []

    def put_item(self, Item, ConditionExpression):
        from botocore.exceptions import ClientError

        self.calls.append((Item, ConditionExpression))
        key = (Item["owner"], Item["ts"])
        if key in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}}, "PutItem")
        self.items[key] = Item


def test_an_update_is_claimed_once():
    from common import claim_update

    table = FakeTable()
    assert claim_update(1001, table) is True
    assert claim_update(1001, table) is False
    assert claim_update(1002, table) is True


def test_the_claim_expires_by_itself_and_is_conditional():
    import time

    from common import DEDUPE_TTL_SECONDS, claim_update

    table = FakeTable()
    claim_update(7, table)
    item, condition = table.calls[0]
    assert condition == "attribute_not_exists(ts)"
    assert item["owner"] == "update" and item["ts"] == "7"
    assert abs(item["expires"] - (time.time() + DEDUPE_TTL_SECONDS)) < 5


def test_other_store_errors_are_not_hidden():
    import pytest
    from botocore.exceptions import ClientError

    from common import claim_update

    class Broken:
        def put_item(self, **kwargs):
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow"}}, "PutItem")

    with pytest.raises(ClientError):
        claim_update(1, Broken())
