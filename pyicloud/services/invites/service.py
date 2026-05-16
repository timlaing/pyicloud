"""High-level Invites service built on top of the iCloud Events CloudKit API.

Public API:

Read (Phase 1):
  - ``InvitesService.events()`` -> ``list[Event]``
  - ``InvitesService.event(event_id)`` -> ``Event``
  - ``InvitesService.rsvps(event)`` -> ``list[Rsvp]``
  - ``InvitesService.resolve(short_guid)`` -> ``ResolvedShare``
  - ``InvitesService.accept(short_guid)`` -> ``Event``
  - ``InvitesService.raw`` -> ``CloudKitInvitesClient``

Write (Phase 2):
  - ``InvitesService.rsvp(event, status, ...)`` -> ``Rsvp``

Write operations on ``EventDetails`` (create event, publish, cancel, invite
via link) arrive in later phases per the design doc.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from pyicloud.common.cloudkit import (
    CKModifyOperation,
    CKModifyResponse,
    CKQueryObject,
    CKQueryResponse,
    CKRecord,
    CKWriteRecord,
    CKZoneIDReq,
)
from pyicloud.common.cloudkit.base import CloudKitExtraMode
from pyicloud.services.base import BaseService

from ._constants import (
    CONTAINER,
    ENV,
    EVENT_DETAILS_RECORD_NAME_PREFIX,
    RSVP_RECORD_NAME_SUFFIX,
    SHARE_RECORD_NAME,
)
from .client import (
    CloudKitInvitesClient,
    InvitesApiError,
    InvitesError,
    ScopeLiteral,
)
from .codecs import decode_integrations, decode_json_bytes
from .models.constants import (
    EventDetailsField,
    InvitesRecordType,
    OneTimeLinkField,
    RsvpField,
)
from .models.dto import (
    AcceptanceStatus,
    Event,
    EventPlace,
    EventScope,
    EventShare,
    EventTime,
    OneTimeLinkGuest,
    Participant,
    ParticipantType,
    ResolvedShare,
    Rsvp,
    RsvpStatus,
)

LOGGER = logging.getLogger(__name__)


class EventNotFound(InvitesError):
    """Raised when a requested event ID is not present in any scope."""


class InvitesService(BaseService):
    """Typed, read-only Invites API.

    Phase 1 exposes event listing, single-event lookup, RSVP listing, and the
    public share resolution flow (``resolve`` / ``accept``). Mutating
    operations land in subsequent phases.
    """

    _CONTAINER = CONTAINER
    _ENV = ENV

    def __init__(
        self,
        service_root: str,
        session: Any,
        params: Dict[str, str],
        *,
        cloudkit_validation_extra: CloudKitExtraMode | None = None,
    ) -> None:
        super().__init__(service_root=service_root, session=session, params=params)
        env_base_url = f"{self.service_root}/database/1/{self._CONTAINER}/{self._ENV}"
        base_params = {
            "remapEnums": True,
            "getCurrentSyncToken": True,
            **(params or {}),
        }
        self._raw = CloudKitInvitesClient(
            env_base_url,
            session,
            base_params,
            validation_extra=cloudkit_validation_extra,
        )

    @property
    def raw(self) -> CloudKitInvitesClient:
        """Escape hatch for advanced/unsupported CloudKit workflows."""
        return self._raw

    # ------------------------------------------------------------------
    # Public reads
    # ------------------------------------------------------------------

    def events(self) -> List[Event]:
        """Return all events visible to the current user (private + shared).

        Each event is populated only from its ``EventDetails`` record; the
        share and RSVPs are ``None`` / empty here. Call :meth:`event` for a
        fully-hydrated view.
        """
        out: List[Event] = []
        seen: set[tuple[EventScope, str]] = set()
        for scope in (EventScope.PRIVATE, EventScope.SHARED):
            for record in self._iter_event_details(scope):
                event_id = self._event_id_from_record_name(record.recordName)
                key = (scope, event_id)
                if key in seen:
                    continue
                seen.add(key)
                out.append(self._event_from_record(record, scope=scope))
        return out

    def event(self, event_id: str) -> Event:
        """Return a fully-hydrated event by ID (zoneName / UUID).

        Looks up the event in the private scope first, then shared. Raises
        :class:`EventNotFound` if no scope has the event.
        """
        for scope in (EventScope.PRIVATE, EventScope.SHARED):
            event = self._fetch_event_full(event_id, scope)
            if event is not None:
                return event
        raise EventNotFound(f"Event not found: {event_id!r}")

    def rsvps(self, event: Event) -> List[Rsvp]:
        """Return the list of RSVPs in an event's zone."""
        zone_id = self._zone_id_req(event.event_id, event.scope)
        resp = self._raw.query(
            self._scope_str(event.scope),
            query=CKQueryObject(recordType=InvitesRecordType.Rsvp.value),
            zone_id=zone_id,
        )
        return [self._rsvp_from_record(r) for r in self._records_of(resp)]

    def resolve(self, short_guid: str) -> ResolvedShare:
        """Preview a share without joining it."""
        data = self._raw.resolve([short_guid])
        result = self._first_resolve_result(data)
        return self._resolved_share_from_result(result)

    def accept(self, short_guid: str) -> Event:
        """Accept a share invite. Returns the joined event (now in SHARED)."""
        data = self._raw.accept([short_guid])
        result = self._first_resolve_result(data)
        zone = result.get("zoneID") or {}
        event_id = zone.get("zoneName")
        if not isinstance(event_id, str):
            raise InvitesApiError(
                "Accept response missing zoneID.zoneName",
                payload=data,
            )
        scope = self._scope_from_db_scope(result.get("databaseScope"))
        event = self._fetch_event_full(event_id, scope)
        if event is None:
            # Fall back to the other scope in case of routing quirks.
            other = (
                EventScope.PRIVATE if scope == EventScope.SHARED else EventScope.SHARED
            )
            event = self._fetch_event_full(event_id, other)
        if event is None:
            raise EventNotFound(
                f"Accepted event {event_id!r} not visible in either scope"
            )
        return event

    # ------------------------------------------------------------------
    # Public writes
    # ------------------------------------------------------------------

    def rsvp(
        self,
        event: Event,
        status: RsvpStatus,
        *,
        name: Optional[str] = None,
        message: Optional[str] = None,
        plus_one_adults: int = 0,
        plus_one_kids: int = 0,
    ) -> Rsvp:
        """Submit or update the current user's RSVP for ``event``.

        Dispatches to ``private`` if the current user owns the event, or
        ``shared`` if they're a guest (per ``event.scope``). Creates the RSVP
        record on first response and updates it on subsequent calls.

        For :attr:`RsvpStatus.NOT_GOING`, plus-one counts are zeroed to mirror
        the iCloud web UI behavior; the ``plus_one_*`` kwargs are ignored in
        that case.

        Returns the freshly written :class:`Rsvp` (including the new
        ``record_change_tag``).
        """
        participant_id = self._current_participant_id(event)
        record_name = f"{participant_id}{RSVP_RECORD_NAME_SUFFIX}"
        existing = self._find_existing_rsvp(event, record_name)

        if status == RsvpStatus.NOT_GOING:
            plus_one_adults = 0
            plus_one_kids = 0

        fields: Dict[str, Any] = {
            RsvpField.STATUS.value: {
                "type": "INT64",
                "value": status.value,
                "isEncrypted": True,
            },
            RsvpField.NUM_ADDITIONAL_ADULTS.value: {
                "type": "INT64",
                "value": plus_one_adults,
                "isEncrypted": True,
            },
            RsvpField.NUM_ADDITIONAL_KIDS.value: {
                "type": "INT64",
                "value": plus_one_kids,
                "isEncrypted": True,
            },
            RsvpField.NUM_ADDITIONAL_GUESTS.value: {
                "type": "INT64",
                "value": plus_one_adults + plus_one_kids,
                "isEncrypted": True,
            },
        }
        if name is not None:
            fields[RsvpField.NAME.value] = {
                "type": "STRING",
                "value": name,
                "isEncrypted": True,
            }
        if message is not None:
            fields[RsvpField.MESSAGE.value] = {
                "type": "STRING",
                "value": message,
                "isEncrypted": True,
            }

        record_change_tag = existing.record_change_tag if existing else None
        op = CKModifyOperation(
            operationType="update" if existing else "create",
            record=CKWriteRecord(
                recordName=record_name,
                recordType=InvitesRecordType.Rsvp.value,
                recordChangeTag=record_change_tag,
                fields=fields,
            ),
        )

        scope_str = self._scope_str(event.scope)
        zone_id = self._zone_id_req(event.event_id, event.scope)
        response: CKModifyResponse = self._raw.modify(
            scope_str,
            operations=[op],
            zone_id=zone_id,
            atomic=True,
        )
        return self._rsvp_from_modify_response(response, record_name)

    # ------------------------------------------------------------------
    # Internal: write helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _current_participant_id(event: Event) -> str:
        """Return the current user's participant_id on ``event``'s share."""
        if event.share is None:
            raise InvitesApiError(
                f"Event {event.event_id!r} has no loaded share; "
                "fetch the event via InvitesService.event(...) first."
            )
        participant_id = event.share.current_user_participant_id
        if not participant_id:
            raise InvitesApiError(
                f"Event {event.event_id!r} share has no currentUserParticipant; "
                "the user may not be a participant on this event."
            )
        return participant_id

    @staticmethod
    def _find_existing_rsvp(event: Event, record_name: str) -> Optional[Rsvp]:
        """Find the user's RSVP in ``event.rsvps`` matching ``record_name``."""
        for rsvp in event.rsvps:
            if rsvp.record_name == record_name:
                return rsvp
        return None

    def _rsvp_from_modify_response(
        self, response: CKModifyResponse, record_name: str
    ) -> Rsvp:
        """Pick the RSVP record from a modify response and convert to DTO."""
        for record in response.records:
            if (
                isinstance(record, CKRecord)
                and record.recordName == record_name
                and record.recordType == InvitesRecordType.Rsvp.value
            ):
                return self._rsvp_from_record(record)
        raise InvitesApiError(
            f"RSVP modify response missing record {record_name!r}",
            payload=response.model_dump(mode="json", exclude_none=True),
        )

    # ------------------------------------------------------------------
    # Internal: zone-level helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _scope_str(scope: EventScope) -> ScopeLiteral:
        return "private" if scope == EventScope.PRIVATE else "shared"

    @staticmethod
    def _scope_from_db_scope(db_scope: Optional[str]) -> EventScope:
        if isinstance(db_scope, str) and db_scope.upper() == "SHARED":
            return EventScope.SHARED
        return EventScope.PRIVATE

    @staticmethod
    def _event_id_from_record_name(record_name: str) -> str:
        if record_name.startswith(EVENT_DETAILS_RECORD_NAME_PREFIX):
            return record_name[len(EVENT_DETAILS_RECORD_NAME_PREFIX) :]
        return record_name

    @staticmethod
    def _zone_id_req(event_id: str, scope: EventScope) -> CKZoneIDReq:
        # In the shared scope CloudKit needs the original owner record name,
        # but we don't always have it here. Most call sites work with just
        # zoneName + zoneType; expand if shared writes need ownerRecordName.
        return CKZoneIDReq(
            zoneName=event_id,
            zoneType="REGULAR_CUSTOM_ZONE",
        )

    @staticmethod
    def _records_of(resp: CKQueryResponse) -> List[CKRecord]:
        return [r for r in resp.records if isinstance(r, CKRecord)]

    def _iter_event_details(self, scope: EventScope) -> Iterable[CKRecord]:
        try:
            resp = self._raw.query(
                self._scope_str(scope),
                query=CKQueryObject(
                    recordType=InvitesRecordType.EventDetails.value,
                ),
                zone_wide=True,
            )
        except InvitesError:
            LOGGER.debug(
                "invites.events.query_failed scope=%s", scope.value, exc_info=True
            )
            return []
        return self._records_of(resp)

    def _fetch_event_full(self, event_id: str, scope: EventScope) -> Optional[Event]:
        zone_id = self._zone_id_req(event_id, scope)
        scope_str = self._scope_str(scope)
        try:
            resp = self._raw.lookup(
                scope_str,
                [
                    f"{EVENT_DETAILS_RECORD_NAME_PREFIX}{event_id}",
                    SHARE_RECORD_NAME,
                ],
                zone_id=zone_id,
            )
        except InvitesError:
            LOGGER.debug(
                "invites.event.lookup_failed event_id=%s scope=%s",
                event_id,
                scope.value,
                exc_info=True,
            )
            return None

        event_record: Optional[CKRecord] = None
        share_record: Optional[CKRecord] = None
        for record in self._records_of(resp):
            if record.recordType == InvitesRecordType.EventDetails.value:
                event_record = record
            elif record.recordType == InvitesRecordType.Share.value:
                share_record = record
        if event_record is None:
            return None

        share = self._share_from_record(share_record) if share_record else None

        rsvps: tuple[Rsvp, ...] = ()
        try:
            rsvp_resp = self._raw.query(
                scope_str,
                query=CKQueryObject(recordType=InvitesRecordType.Rsvp.value),
                zone_id=zone_id,
            )
            rsvps = tuple(
                self._rsvp_from_record(r) for r in self._records_of(rsvp_resp)
            )
        except InvitesError:
            LOGGER.debug(
                "invites.event.rsvp_query_failed event_id=%s",
                event_id,
                exc_info=True,
            )

        otl_guests: tuple[OneTimeLinkGuest, ...] = ()
        try:
            otl_resp = self._raw.query(
                scope_str,
                query=CKQueryObject(
                    recordType=InvitesRecordType.OneTimeLinkGuestInfo.value,
                ),
                zone_id=zone_id,
            )
            otl_guests = tuple(
                self._one_time_link_from_record(r) for r in self._records_of(otl_resp)
            )
        except InvitesError:
            LOGGER.debug(
                "invites.event.otl_query_failed event_id=%s",
                event_id,
                exc_info=True,
            )

        if share is not None and otl_guests:
            share = share.model_copy(update={"one_time_links": otl_guests})

        return self._event_from_record(
            event_record, scope=scope, share=share, rsvps=rsvps
        )

    # ------------------------------------------------------------------
    # Internal: record -> DTO mapping
    # ------------------------------------------------------------------

    def _event_from_record(
        self,
        record: CKRecord,
        *,
        scope: EventScope,
        share: Optional[EventShare] = None,
        rsvps: tuple[Rsvp, ...] = (),
    ) -> Event:
        event_id = self._event_id_from_record_name(record.recordName)
        fields = record.fields

        title = self._field_str(fields, EventDetailsField.TITLE.value, default="")
        notes = self._field_str(fields, EventDetailsField.NOTES.value, default="")
        host_display_name = self._field_str(
            fields, EventDetailsField.HOST_DISPLAY_NAME.value, default=""
        )

        is_published = self._field_bool(fields, EventDetailsField.IS_PUBLISHED.value)
        is_private = self._field_bool(fields, EventDetailsField.IS_PRIVATE.value)
        is_cancelled = self._field_bool(fields, EventDetailsField.IS_CANCELLED.value)
        block_new_rsvps = self._field_bool(
            fields, EventDetailsField.BLOCK_NEW_RSVPS.value
        )

        max_attendees = self._field_int(fields, EventDetailsField.MAX_ATTENDEES.value)
        max_additional = (
            self._field_int(
                fields, EventDetailsField.MAX_ADDITIONAL_GUESTS_PER_RSVP.value
            )
            or 0
        )

        time_obj = self._decode_time(fields)
        place_obj = self._decode_place(fields)
        background = self._decode_json(fields, EventDetailsField.BACKGROUND.value) or {}
        style = self._decode_json(fields, EventDetailsField.STYLE.value) or {}
        integrations_blob = self._decode_json(
            fields, EventDetailsField.INTEGRATIONS.value
        )
        integrations = decode_integrations(integrations_blob)

        created_ts = record.created.timestamp if record.created else None
        modified_ts = record.modified.timestamp if record.modified else None

        return Event(
            event_id=event_id,
            scope=scope,
            record_change_tag=record.recordChangeTag,
            title=title,
            notes=notes,
            host_display_name=host_display_name,
            is_published=is_published,
            is_private=is_private,
            is_cancelled=is_cancelled,
            block_new_rsvps=block_new_rsvps,
            max_attendees=max_attendees,
            max_additional_guests_per_rsvp=max_additional,
            time=time_obj,
            place=place_obj,
            background=background if isinstance(background, dict) else {},
            style=style if isinstance(style, dict) else {},
            integrations=integrations,
            created_timestamp=created_ts,
            modified_timestamp=modified_ts,
            share=share,
            rsvps=rsvps,
        )

    def _share_from_record(self, record: CKRecord) -> EventShare:
        short_guid = record.shortGUID or ""
        public_permission = record.publicPermission or "NONE"
        participants = tuple(
            self._participant_from_ck(p) for p in (record.participants or [])
        )
        current_user_participant_id: Optional[str] = None
        current = getattr(record, "currentUserParticipant", None)
        if current is not None:
            current_user_participant_id = getattr(current, "participantId", None)
        return EventShare(
            short_guid=short_guid,
            public_permission=public_permission,
            participants=participants,
            one_time_links=(),
            current_user_participant_id=current_user_participant_id,
        )

    @staticmethod
    def _participant_from_ck(p: Any) -> Participant:
        identity = getattr(p, "userIdentity", None)
        name_components = (
            getattr(identity, "nameComponents", None) if identity else None
        )
        lookup_info = getattr(identity, "lookupInfo", None) if identity else None
        return Participant(
            participant_id=getattr(p, "participantId", "") or "",
            user_record_name=(
                getattr(identity, "userRecordName", None) if identity else None
            ),
            given_name=(
                getattr(name_components, "givenName", None) if name_components else None
            ),
            family_name=(
                getattr(name_components, "familyName", None)
                if name_components
                else None
            ),
            email=(getattr(lookup_info, "emailAddress", None) if lookup_info else None),
            type=InvitesService._safe_participant_type(getattr(p, "type", None)),
            acceptance_status=InvitesService._safe_acceptance_status(
                getattr(p, "acceptanceStatus", None)
            ),
            permission=getattr(p, "permission", "READ_ONLY") or "READ_ONLY",
        )

    @staticmethod
    def _safe_participant_type(value: Any) -> ParticipantType:
        """Map a wire participant ``type`` to the enum, defaulting on unknowns."""
        try:
            return ParticipantType(str(value or "USER"))
        except ValueError:
            LOGGER.debug("invites.participant.unknown_type %r", value)
            return ParticipantType.USER

    @staticmethod
    def _safe_acceptance_status(value: Any) -> AcceptanceStatus:
        """Map a wire ``acceptanceStatus`` to the enum, defaulting on unknowns."""
        try:
            return AcceptanceStatus(str(value or "INVITED"))
        except ValueError:
            LOGGER.debug("invites.participant.unknown_acceptance %r", value)
            return AcceptanceStatus.INVITED

    def _rsvp_from_record(self, record: CKRecord) -> Rsvp:
        record_name = record.recordName
        suffix = "_rsvp"
        participant_id = (
            record_name[: -len(suffix)] if record_name.endswith(suffix) else record_name
        )
        fields = record.fields
        status_int = self._field_int(fields, RsvpField.STATUS.value) or 0
        try:
            status = RsvpStatus(status_int)
        except ValueError:
            status = RsvpStatus.NO_RESPONSE
        image_url = self._asset_download_url(fields, RsvpField.IMAGE.value)
        return Rsvp(
            record_name=record_name,
            participant_id=participant_id,
            name=self._field_str(fields, RsvpField.NAME.value, default=""),
            status=status,
            message=self._field_str(fields, RsvpField.MESSAGE.value, default=None),
            num_additional_adults=(
                self._field_int(fields, RsvpField.NUM_ADDITIONAL_ADULTS.value) or 0
            ),
            num_additional_kids=(
                self._field_int(fields, RsvpField.NUM_ADDITIONAL_KIDS.value) or 0
            ),
            image_download_url=image_url,
            record_change_tag=record.recordChangeTag,
        )

    def _one_time_link_from_record(self, record: CKRecord) -> OneTimeLinkGuest:
        record_name = record.recordName
        suffix = "_otl"
        participant_id = (
            record_name[: -len(suffix)] if record_name.endswith(suffix) else record_name
        )
        fields = record.fields
        emails_raw = self._field_value(fields, OneTimeLinkField.EMAILS.value)
        phones_raw = self._field_value(fields, OneTimeLinkField.PHONE_NUMBERS.value)
        return OneTimeLinkGuest(
            record_name=record_name,
            participant_id=participant_id,
            name=self._field_str(fields, OneTimeLinkField.NAME.value, default=""),
            emails=tuple(emails_raw) if isinstance(emails_raw, list) else (),
            phone_numbers=tuple(phones_raw) if isinstance(phones_raw, list) else (),
        )

    # ------------------------------------------------------------------
    # Internal: resolve/accept response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _first_resolve_result(data: Mapping[str, Any]) -> Dict[str, Any]:
        results = data.get("results")
        if not isinstance(results, list) or not results:
            raise InvitesApiError(
                "Resolve/accept response missing results",
                payload=data,
            )
        first = results[0]
        if not isinstance(first, dict):
            raise InvitesApiError(
                "Resolve/accept response result not a dict",
                payload=data,
            )
        return first

    def _resolved_share_from_result(self, result: Mapping[str, Any]) -> ResolvedShare:
        short = result.get("shortGUID") or {}
        short_guid = short.get("value") if isinstance(short, dict) else None
        if not isinstance(short_guid, str):
            raise InvitesApiError(
                "Resolve response missing shortGUID.value", payload=dict(result)
            )
        zone = result.get("zoneID") or {}
        event_id = zone.get("zoneName") if isinstance(zone, dict) else None
        if not isinstance(event_id, str):
            raise InvitesApiError(
                "Resolve response missing zoneID.zoneName", payload=dict(result)
            )

        owner_identity = result.get("ownerIdentity") or {}
        owner_name_components = (
            owner_identity.get("nameComponents")
            if isinstance(owner_identity, dict)
            else None
        )
        owner_lookup_info = (
            owner_identity.get("lookupInfo")
            if isinstance(owner_identity, dict)
            else None
        )

        share_raw = result.get("share") or {}
        if isinstance(share_raw, dict):
            try:
                share_record = CKRecord.model_validate(share_raw)
                share = self._share_from_record(share_record)
            except Exception:
                share = EventShare(
                    short_guid=short_guid,
                    public_permission="NONE",
                )
        else:
            share = EventShare(short_guid=short_guid, public_permission="NONE")

        return ResolvedShare(
            short_guid=short_guid,
            event_id=event_id,
            owner_record_name=(
                owner_identity.get("userRecordName")
                if isinstance(owner_identity, dict)
                else None
            ),
            owner_email=(
                owner_lookup_info.get("emailAddress")
                if isinstance(owner_lookup_info, dict)
                else None
            ),
            owner_given_name=(
                owner_name_components.get("givenName")
                if isinstance(owner_name_components, dict)
                else None
            ),
            owner_family_name=(
                owner_name_components.get("familyName")
                if isinstance(owner_name_components, dict)
                else None
            ),
            participant_status=str(result.get("participantStatus") or ""),
            participant_type=str(result.get("participantType") or ""),
            participant_permission=str(result.get("participantPermission") or ""),
            share=share,
        )

    # ------------------------------------------------------------------
    # Internal: field-extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _field_value(fields: Any, key: str) -> Any:
        if hasattr(fields, "get_value"):
            return fields.get_value(key)
        if hasattr(fields, "get"):
            wrapper = fields.get(key)
            return getattr(wrapper, "value", None)
        return None

    def _field_str(
        self, fields: Any, key: str, *, default: Optional[str]
    ) -> Optional[str]:
        value = self._field_value(fields, key)
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return default
        return default

    def _field_int(self, fields: Any, key: str) -> Optional[int]:
        value = self._field_value(fields, key)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        return None

    def _field_bool(self, fields: Any, key: str) -> bool:
        value = self._field_value(fields, key)
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        return False

    def _decode_json(self, fields: Any, key: str) -> Any:
        value = self._field_value(fields, key)
        return decode_json_bytes(value)

    def _decode_time(self, fields: Any) -> Optional[EventTime]:
        blob = self._decode_json(fields, EventDetailsField.TIME.value)
        if not isinstance(blob, Mapping):
            return None
        start_ms = blob.get("startSince1970")
        if not isinstance(start_ms, (int, float)):
            return None
        try:
            start = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
        end: Optional[datetime] = None
        end_ms = blob.get("endSince1970")
        if isinstance(end_ms, (int, float)):
            try:
                end = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc)
            except (OSError, ValueError, OverflowError):
                end = None
        return EventTime(
            start=start,
            end=end,
            is_all_day=bool(blob.get("isAllDay", False)),
            is_open_ended=bool(blob.get("isOpenEnded", False)),
        )

    def _decode_place(self, fields: Any) -> Optional[EventPlace]:
        blob = self._decode_json(fields, EventDetailsField.PLACE.value)
        if not isinstance(blob, Mapping):
            return None
        return EventPlace(
            title=blob.get("title") if isinstance(blob.get("title"), str) else None,
            subtitle=(
                blob.get("subtitle") if isinstance(blob.get("subtitle"), str) else None
            ),
            city=blob.get("city") if isinstance(blob.get("city"), str) else None,
            time_zone_identifier=(
                blob.get("timeZoneIdentifier")
                if isinstance(blob.get("timeZoneIdentifier"), str)
                else None
            ),
            latitude=(
                float(blob["latitude"])
                if isinstance(blob.get("latitude"), (int, float))
                else None
            ),
            longitude=(
                float(blob["longitude"])
                if isinstance(blob.get("longitude"), (int, float))
                else None
            ),
            url=blob.get("url") if isinstance(blob.get("url"), str) else None,
        )

    @staticmethod
    def _asset_download_url(fields: Any, key: str) -> Optional[str]:
        value = InvitesService._field_value(fields, key)
        if value is None:
            return None
        url = getattr(value, "downloadURL", None)
        if isinstance(url, str):
            return url
        if isinstance(value, Mapping):
            maybe = value.get("downloadURL")
            return maybe if isinstance(maybe, str) else None
        return None
