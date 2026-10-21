"""Narrow wire models for the observed single-message flag mutation."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

MailFlag = Literal["seen", "flagged"]
FlagIntent = Literal["setMessageFlag", "clearMessageFlag"]
FlagCounter = Annotated[int, Field(strict=True, gt=0)]


class FlagModel(BaseModel):
    """Reject unknown mutation fields instead of assuming their semantics."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, strict=True
    )


class FlagDelta(FlagModel):
    """Exactly one named bit is changed by internally constructed requests."""

    set: tuple[MailFlag] | None = None
    clear: tuple[MailFlag] | None = None


class FlagPatch(FlagModel):
    """A flags-only patch, never a move or replacement numeric bitmask."""

    flags: FlagDelta


class FlagOperation(FlagModel):
    """Only one exact message identifier is submitted per request."""

    ids: tuple[str]
    patch: FlagPatch


class FlagRequest(FlagModel):
    """The browser-observed email/set request, without session counter guesses."""

    domain: Literal["email"] = "email"
    batch_update: tuple[FlagOperation] = Field(alias="batchUpdate")


class FlagAcknowledgement(FlagModel):
    """A server acknowledgement counter, not a reusable synchronization token."""

    modseq: FlagCounter


class FlagResponse(FlagModel):
    """Require all observed outcome fields; absent fields are not success."""

    account_id: Annotated[str, Field(strict=True, min_length=1)] = Field(
        alias="accountId"
    )
    created: dict[str, JsonValue] | None
    destroyed: list[str] | None
    new_state: JsonValue = Field(alias="newState")
    old_state: JsonValue = Field(alias="oldState")
    not_created: dict[str, JsonValue] | None = Field(alias="notCreated")
    not_destroyed: dict[str, JsonValue] | None = Field(alias="notDestroyed")
    not_updated: dict[str, JsonValue] | None = Field(alias="notUpdated")
    updated: dict[str, FlagAcknowledgement] | None
