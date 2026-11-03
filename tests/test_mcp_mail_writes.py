"""Synthetic exact-content checks, not evidence of live server transformations."""

import pytest

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.mail_writes import canonical_draft_html, verify_draft_body
from pyicloud.services.mail.models import MailBody, MailBodyPart


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ""),
        ("a\r\nb\rc\nd", "<div>a<br>b<br>c<br>d</div>"),
        ('<script>&"', '<div>&lt;script&gt;&amp;"</div>'),
        (" ", "<div> </div>"),
    ],
)
def test_canonical_body_matches_plain_only_save(text: str, expected: str) -> None:
    """Escape markup exactly while preserving ordinary text and whitespace."""
    assert canonical_draft_html(text) == expected


@pytest.mark.parametrize(
    "case", ["exact", "identity", "part", "mime", "multiple", "empty", "content"]
)
def test_postsave_body_requires_exact_identity_and_content(case: str) -> None:
    """Generic acknowledgements and equivalent-looking HTML are not proof."""
    body = MailBody(
        guid="message:Drafts/101",
        longHeader="untrusted",
        contentType="text/html",
        parts=[
            MailBodyPart(guid="messagepart:Drafts/101-1", content="<div>Approved</div>")
        ],
    )
    if case == "identity":
        body = body.model_copy(update={"guid": "message:Drafts/102"})
    elif case == "part":
        body = body.model_copy(
            update={
                "parts": [
                    body.parts[0].model_copy(
                        update={"guid": "messagepart:Drafts/102-1"}
                    )
                ]
            }
        )
    elif case == "mime":
        body = body.model_copy(update={"content_type": "text/plain"})
    elif case == "multiple":
        body.parts.append(body.parts[0])
    elif case == "empty":
        body = body.model_copy(update={"parts": []})
    elif case == "content":
        body = body.model_copy(
            update={
                "parts": [
                    body.parts[0].model_copy(
                        update={"content": "<div>Unapproved</div>"}
                    )
                ]
            }
        )
    if case == "exact":
        verify_draft_body(body, 101, "Approved")
    else:
        with pytest.raises(WriteDenied, match="mutation_unverified"):
            verify_draft_body(body, 101, "Approved")
