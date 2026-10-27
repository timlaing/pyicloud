"""Account-verified sender identities, never inferred from the Apple login."""

from email.headerregistry import Address

from pydantic import Field, StrictBool, StrictInt, StrictStr

from .exceptions import MailError
from .models import MailModel, MailSender


class SenderDomain(MailModel):
    """Only an explicitly enabled domain is eligible for a write sender."""

    domain: StrictStr
    allow_send_from: StrictBool = Field(alias="allowSendFrom")


class SenderAlias(MailModel):
    """An inactive alias is not an authorized sender."""

    email_id: StrictStr = Field(alias="emailId")
    full_name: StrictStr = Field(default="", alias="fullName")
    is_active: StrictBool = Field(alias="isActive")
    domains: list[SenderDomain] = Field(alias="supportedDomains", max_length=100)


class CustomSender(MailModel):
    """Custom domains advertise their send permission separately."""

    email_id: StrictStr = Field(alias="emailId")
    domain: StrictStr
    full_name: StrictStr = Field(default="", alias="fullName")
    allow_send_from: StrictBool = Field(alias="allowSendFrom")


class SenderAccount(MailModel):
    """Minimal projection of the observed web-preference account object."""

    account_id: StrictStr | StrictInt = Field(alias="dsId")
    email_id: StrictStr = Field(alias="emailId")
    full_name: StrictStr = Field(default="", alias="fullName")
    domains: list[SenderDomain] = Field(alias="supportedDomains", max_length=100)
    aliases: list[SenderAlias] = Field(max_length=100)
    custom_domains: list[CustomSender] = Field(alias="customDomains", max_length=1000)


class SenderPreferences(MailModel):
    """Other preference groups are deliberately not exposed or persisted."""

    account: SenderAccount


def _sender_candidates(account: SenderAccount) -> list[tuple[str, str, str]]:
    """Collect only explicitly enabled primary, alias, and custom-domain entries."""
    candidates = [
        (account.email_id, domain.domain, account.full_name)
        for domain in account.domains
        if domain.allow_send_from
    ]
    for alias in account.aliases:
        if alias.is_active:
            candidates.extend(
                (alias.email_id, domain.domain, alias.full_name or account.full_name)
                for domain in alias.domains
                if domain.allow_send_from
            )
    candidates.extend(
        (custom.email_id, custom.domain, custom.full_name or account.full_name)
        for custom in account.custom_domains
        if custom.allow_send_from
    )
    return candidates


def sender_identities(data: SenderPreferences, account_id: str) -> list[MailSender]:
    """Match the discovered account and reject malformed or ambiguous addresses."""
    account = data.account
    if str(account.account_id) != account_id:
        raise MailError("Mail sender preferences belong to a different account")
    result = []
    seen: set[str] = set()
    for local, domain, name in _sender_candidates(account):
        if (
            not local
            or not domain
            or "@" in local
            or "@" in domain
            or any(ord(c) < 32 or ord(c) == 127 for c in local + domain + name)
        ):
            raise MailError("Mail returned an invalid sender identity")
        address = local + "@" + domain
        try:
            parsed = Address(addr_spec=address)
        except ValueError as exc:
            raise MailError("Mail returned an invalid sender address") from exc
        if parsed.addr_spec != address or address.casefold() in seen:
            raise MailError("Mail returned ambiguous sender identities")
        seen.add(address.casefold())
        result.append(MailSender(address=address, display_name=name))
    return result
