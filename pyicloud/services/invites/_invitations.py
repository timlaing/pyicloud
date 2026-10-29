"""Capture-shaped one-time-link writes and conservative result verification."""

from collections.abc import Sequence
from dataclasses import dataclass
import re
from typing import Any

from pyicloud.common.cloudkit import (
    CKLookupResponse,
    CKModifyOperation,
    CKModifyResponse,
    CKOneTimeLink,
    CKOneTimeStableUrlInfo,
    CKRecord,
    CKWriteRecord,
)
from pyicloud.common.cloudkit.models import CKParticipant

from ._constants import SHARE_RECORD_NAME
from .exceptions import InvitesApiError
from .models.constants import InvitesRecordType
from .models.dto import OneTimeLinkGuest

_EMAIL_PART = r"[^@\s<>\x00-\x1f\x7f-\x9f]+"


@dataclass(frozen=True)
class InvitationContacts:
    """Validated contact metadata, not CloudKit account lookup identities."""

    name: str
    emails: tuple[str, ...]
    phone_numbers: tuple[str, ...]


def _contacts(values: Sequence[str], pattern: str, label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{label} must be a sequence of strings")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or re.fullmatch(pattern, value.strip()) is None:
            raise ValueError(f"Invalid {label} entry")
        cleaned = value.strip()
        if cleaned not in result:
            result.append(cleaned)
    return tuple(result)


def invitation_contacts(
    name: str, emails: Sequence[str], phone_numbers: Sequence[str]
) -> InvitationContacts:
    """Validate before network I/O; phone numbers use international E.164 form."""
    if not isinstance(name, str) or any(
        ord(char) < 32 or 127 <= ord(char) < 160 for char in name
    ):
        raise ValueError("name must be a string without control characters")
    contacts = InvitationContacts(
        name=name,
        emails=_contacts(
            emails,
            rf"{_EMAIL_PART}@{_EMAIL_PART}\.{_EMAIL_PART}",
            "emails",
        ),
        phone_numbers=_contacts(phone_numbers, r"\+[1-9][0-9]{6,14}", "phone_numbers"),
    )
    if not contacts.emails and not contacts.phone_numbers:
        raise ValueError("At least one email address or phone number is required")
    return contacts


def owner_share(response: CKLookupResponse) -> CKRecord:
    """Require a fresh, complete owner share rather than a lossy EventShare DTO."""
    shares = [
        record
        for record in response.records
        if isinstance(record, CKRecord)
        and record.recordName == SHARE_RECORD_NAME
        and record.recordType == InvitesRecordType.Share.value
    ]
    if len(shares) != 1:
        raise InvitesApiError("The current event share could not be read unambiguously")
    share = shares[0]
    current = share.currentUserParticipant
    if not share.recordChangeTag or not share.participants:
        raise InvitesApiError("The current share has no change tag or participants")
    if (
        current is None
        or current.type != "OWNER"
        or not current.participantId
        or current.acceptanceStatus != "ACCEPTED"
        or current.permission != "READ_WRITE"
    ):
        raise InvitesApiError("Only the current event owner can create invitations")
    if not any(
        p.participantId == current.participantId and p.type == "OWNER"
        for p in share.participants
    ):
        raise InvitesApiError("The share does not contain the current owner")
    if share.oneTimeStableUrlInfo and any(
        not link.participantId for link in share.oneTimeStableUrlInfo.oneTimeLinks
    ):
        raise InvitesApiError("The share contains a one-time link without participants")
    links = (
        share.oneTimeStableUrlInfo.oneTimeLinks if share.oneTimeStableUrlInfo else []
    )
    if any(
        not link.shortTokenHash
        or not link.protectedFullToken
        or not link.encryptedFullTokenInfo
        for link in links
    ):
        raise InvitesApiError(
            "Existing one-time links lack preservation metadata; no write was sent"
        )
    _verify_participants(share.participants, links)
    return share


def _verify_participants(
    participants: Sequence[CKParticipant], links: Sequence[CKOneTimeLink]
) -> None:
    ids = [p.participantId for p in participants]
    if len(ids) != len(set(ids)):
        raise InvitesApiError("The share participant list is ambiguous")
    linked_ids = {pid for link in links for pid in link.participantId}
    for participant in participants:
        identity = participant.userIdentity
        if (
            not participant.participantId
            or not participant.type
            or not participant.permission
            or not participant.acceptanceStatus
            or identity is None
        ):
            raise InvitesApiError("The share participant list is incomplete")
        lookup = identity.lookupInfo
        if (
            not identity.userRecordName
            and not (lookup and (lookup.emailAddress or lookup.phoneNumber))
            and participant.participantId not in linked_ids
        ):
            raise InvitesApiError(
                "Anonymous participants require their current one-time-link metadata"
            )


def invitation_operations(
    share: CKRecord, guest: OneTimeLinkGuest
) -> list[CKModifyOperation]:
    """Append a participant, requesting a link for that participant only.

    The browser projects existing participants to their writable identity and
    permission fields, not their server-generated PCS or key metadata. Existing
    link metadata is retained with its tokens; only the new entry is an empty
    generation request. Omitting existing link entries makes Apple treat their
    anonymous participants as ordinary contacts and reject the update.
    Participants and link tokens are verified against the returned share after
    the write; the remaining preservation metadata is required before
    submission and resent unchanged.
    """
    participants: list[dict[str, Any]] = []
    for participant in share.participants or []:
        data = participant.model_dump(
            mode="json",
            exclude_none=True,
            include={
                "participantId",
                "userIdentity",
                "permission",
                "type",
                "acceptanceStatus",
                "customRole",
            },
        )
        data["userIdentity"].pop("nameComponents", None)
        participants.append(data)
    existing_ids = {p["participantId"] for p in participants}
    if share.oneTimeStableUrlInfo:
        existing_ids.update(
            pid
            for link in share.oneTimeStableUrlInfo.oneTimeLinks
            for pid in link.participantId
        )
    if guest.participant_id in existing_ids:
        raise InvitesApiError(
            "Generated participant ID already exists; no write was sent"
        )
    participants.append({
        "participantId": guest.participant_id,
        "userIdentity": {"lookupInfo": {}},
        "permission": "READ_WRITE",
        "type": "USER",
        "acceptanceStatus": "INVITED",
    })
    return [
        CKModifyOperation(
            operationType="create",
            record=CKWriteRecord(
                recordType=InvitesRecordType.OneTimeLinkGuestInfo.value,
                recordName=guest.record_name,
                fields={
                    "name": {
                        "value": guest.name,
                        "type": "STRING",
                        "isEncrypted": True,
                    },
                    "emails": {
                        "value": list(guest.emails),
                        "type": "STRING_LIST",
                        "isEncrypted": True,
                    },
                    "phoneNumbers": {
                        "value": list(guest.phone_numbers),
                        "type": "STRING_LIST",
                        "isEncrypted": True,
                    },
                },
            ),
        ),
        CKModifyOperation(
            operationType="update",
            record=CKWriteRecord.model_validate({
                "recordType": InvitesRecordType.Share.value,
                "recordName": SHARE_RECORD_NAME,
                "recordChangeTag": share.recordChangeTag,
                "fields": {},
                "participants": participants,
                "oneTimeStableUrlInfo": CKOneTimeStableUrlInfo(
                    oneTimeLinks=[
                        *(
                            share.oneTimeStableUrlInfo.oneTimeLinks
                            if share.oneTimeStableUrlInfo
                            else []
                        ),
                        CKOneTimeLink(participantId=[guest.participant_id]),
                    ]
                ),
            }),
        ),
    ]


def _record(response: CKModifyResponse, name: str, kind: str) -> CKRecord:
    records = [
        r
        for r in response.records
        if isinstance(r, CKRecord) and r.recordName == name and r.recordType == kind
    ]
    if len(records) != 1 or not records[0].recordChangeTag:
        raise InvitesApiError(
            "Invitation response is missing an unambiguous record and change tag"
        )
    return records[0]


def _verify_participants_preserved(
    before: Sequence[CKParticipant],
    after: Sequence[CKParticipant],
    participant_id: str,
) -> None:
    current = {p.participantId: p for p in after}
    for old in before:
        new = current.get(old.participantId)
        if new is None or (
            old.type,
            old.permission,
            old.acceptanceStatus,
            old.customRole,
        ) != (new.type, new.permission, new.acceptanceStatus, new.customRole):
            raise InvitesApiError("Existing share participants were not preserved")
    added = current.get(participant_id)
    if added is None or (added.type, added.permission, added.acceptanceStatus) != (
        "USER",
        "READ_WRITE",
        "INVITED",
    ):
        raise InvitesApiError("Invitation response is missing the invited participant")


def _verify_links_preserved(
    before: Sequence[CKOneTimeLink], after: Sequence[CKOneTimeLink]
) -> None:
    for old_link in before:
        matches = [
            link
            for link in after
            if set(link.participantId) == set(old_link.participantId)
        ]
        if len(matches) != 1:
            raise InvitesApiError("Existing one-time links could not be verified")
        new_link = matches[0]
        if (old_link.sharingLink and new_link.sharingLink != old_link.sharingLink) or (
            old_link.shortTokenHash
            and new_link.shortTokenHash != old_link.shortTokenHash
        ):
            raise InvitesApiError("An existing one-time link changed unexpectedly")


def _verify_preserved(before: CKRecord, after: CKRecord, participant_id: str) -> None:
    participants = after.participants or []
    ids = [p.participantId for p in participants]
    if len(set(ids)) != len(ids):
        raise InvitesApiError("Invitation response has duplicate participants")
    _verify_participants_preserved(
        before.participants or [], participants, participant_id
    )
    _verify_links_preserved(
        (
            before.oneTimeStableUrlInfo.oneTimeLinks
            if before.oneTimeStableUrlInfo
            else []
        ),
        after.oneTimeStableUrlInfo.oneTimeLinks if after.oneTimeStableUrlInfo else [],
    )


def invitation_records(
    response: CKModifyResponse, before: CKRecord, guest: OneTimeLinkGuest
) -> tuple[CKRecord, CKRecord, str]:
    """Validate both halves without retrying a possibly successful creation."""
    record = _record(
        response, guest.record_name, InvitesRecordType.OneTimeLinkGuestInfo.value
    )
    share = _record(response, SHARE_RECORD_NAME, InvitesRecordType.Share.value)
    _verify_preserved(before, share, guest.participant_id)
    if any(
        record.fields.get_value(key) != expected
        for key, expected in (
            ("name", guest.name),
            ("emails", list(guest.emails)),
            ("phoneNumbers", list(guest.phone_numbers)),
        )
    ):
        raise InvitesApiError(
            "Invitation response did not return the requested guest identity"
        )
    links = (
        share.oneTimeStableUrlInfo.oneTimeLinks if share.oneTimeStableUrlInfo else []
    )
    matches = [link for link in links if link.participantId == [guest.participant_id]]
    if len(matches) != 1 or not matches[0].sharingLink:
        raise InvitesApiError(
            "Invitation response did not return an unambiguous one-time link"
        )
    token = matches[0].sharingLink
    if re.fullmatch(r"[A-Za-z0-9_-]+", token) is None or token == share.shortGUID:
        raise InvitesApiError(
            "Invitation response did not return a valid distinct invitation token"
        )
    return record, share, token
