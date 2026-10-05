"""Diagnostic command.

``icloud doctor`` answers the question a user actually has when a service stops
working: is this my session, my configuration, or Apple's side? It reports what
it can even when there is no usable session, because "you are not logged in" is
itself one of the answers.

It is strictly read-only. Nothing here writes to the account.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlparse

import typer

from pyicloud.base import PyiCloudService
from pyicloud.cli.context import CLIAbort, CLIState, get_state
from pyicloud.cli.options import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_OUTPUT_FORMAT,
    HttpProxyOption,
    HttpsProxyOption,
    LogLevelOption,
    NoVerifySslOption,
    OutputFormatOption,
    SessionDirOption,
    UsernameOption,
    store_command_options,
)
from pyicloud.cli.output import console_kv_table, console_table
from pyicloud.diagnostics import (
    SERVICE_PROBES,
    ProbeResult,
    ProbeStatus,
    WebserviceFinding,
    WebserviceStatus,
    diagnose_webservices,
    environment,
    probe_failures,
    probe_services,
    services_at_risk,
    webservice_problems,
)

ISSUE_TRACKER = "https://github.com/timlaing/pyicloud/issues"

STATUS_LABELS: dict[WebserviceStatus, str] = {
    WebserviceStatus.OK: "ok",
    WebserviceStatus.MISSING: "MISSING",
    WebserviceStatus.MALFORMED: "MALFORMED",
    WebserviceStatus.UNUSED: "unused",
}

PROBE_LABELS: dict[ProbeStatus, str] = {
    ProbeStatus.OK: "ok",
    ProbeStatus.UNAVAILABLE: "unavailable",
    ProbeStatus.AUTH: "auth",
    ProbeStatus.GONE: "GONE",
    ProbeStatus.ERROR: "ERROR",
    ProbeStatus.SKIPPED: "skipped",
}

ProbeOption = Annotated[
    bool,
    typer.Option(
        "--probe",
        help=(
            "Additionally call each service once with a read-only request. "
            "Catches an endpoint that has been withdrawn behind a host Apple "
            "still advertises, at the cost of one request per service."
        ),
        rich_help_panel="Output & Diagnostics",
    ),
]


def _session_report(state: CLIState) -> tuple[dict[str, Any], PyiCloudService | None]:
    """Describe the session, without failing when there is not a usable one."""

    report: dict[str, Any] = {
        "username": None,
        "authenticated": False,
        "trusted_session": None,
        "requires_2fa": None,
        "requires_2sa": None,
        "error": None,
    }

    try:
        api = state.get_api()
    except CLIAbort as err:
        # Not being logged in is a diagnosis, not a reason to stop diagnosing.
        report["error"] = str(err)
        _add_storage_paths(state, report)
        return report, None

    status = api.get_auth_status()
    report.update({
        "username": api.account_name,
        "authenticated": bool(status.get("authenticated")),
        "trusted_session": bool(status.get("trusted_session")),
        "requires_2fa": bool(status.get("requires_2fa")),
        "requires_2sa": bool(status.get("requires_2sa")),
    })
    _add_storage_paths(state, report, api)
    return report, api


def _add_storage_paths(
    state: CLIState, report: dict[str, Any], api: PyiCloudService | None = None
) -> None:
    """Add session file locations, which are absent when no account is resolvable."""

    try:
        report.update(state.auth_storage_info(api))
    except CLIAbort:
        report.update({
            "session_path": None,
            "cookiejar_path": None,
            "has_session_file": False,
            "has_cookiejar_file": False,
        })


def _finding_payload(finding: WebserviceFinding) -> dict[str, Any]:
    """Render one finding as JSON-friendly data."""

    return {
        "key": finding.key,
        "status": finding.status.value,
        "powers": list(finding.powers),
        "url": finding.url,
        "detail": finding.detail,
        "is_problem": finding.is_problem,
    }


def _probe_payload(result: ProbeResult) -> dict[str, Any]:
    """Render one probe result as JSON-friendly data."""

    return {
        "service": result.service,
        "status": result.status.value,
        "detail": result.detail,
        "elapsed_ms": result.elapsed_ms,
        "is_failure": result.is_failure,
    }


def _host(url: str | None) -> str:
    """Return the host of an advertised URL, which is the part worth reading."""

    if not url:
        return ""
    return urlparse(url).netloc or url


def _print_environment(state: CLIState, env: dict[str, str]) -> None:
    """Render the environment section."""

    state.console.print(
        console_kv_table(
            "Environment",
            [
                ("pyicloud", env["pyicloud"]),
                ("Python", env["python"]),
                ("Platform", env["platform"]),
            ],
        )
    )


def _print_session(state: CLIState, session: dict[str, Any]) -> None:
    """Render the session section."""

    rows: list[tuple[str, Any]] = [
        ("Account", session["username"] or "unresolved"),
        ("Authenticated", "yes" if session["authenticated"] else "no"),
    ]
    if session["authenticated"]:
        rows.append(("Trusted session", "yes" if session["trusted_session"] else "no"))
        if session["requires_2fa"]:
            rows.append(("Requires 2FA", "yes"))
        if session["requires_2sa"]:
            rows.append(("Requires 2SA", "yes"))
    rows.extend([
        ("Session file", _path_display(session, "session_path", "has_session_file")),
        (
            "Cookie jar",
            _path_display(session, "cookiejar_path", "has_cookiejar_file"),
        ),
    ])
    state.console.print(console_kv_table("Session", rows))

    if session["error"]:
        state.console.print(f"\n{session['error']}")


def _path_display(session: dict[str, Any], path_key: str, exists_key: str) -> str:
    """Render a session file path with an inline marker when it is absent."""

    path = session.get(path_key)
    if not path:
        return "unresolved"
    return str(path) if session.get(exists_key) else f"{path} (missing)"


def _print_webservices(
    state: CLIState, findings: tuple[WebserviceFinding, ...]
) -> None:
    """Render the webservices section.

    Only the keys pyicloud actually resolves get a table row. A live account
    advertises roughly twice as many services as this library wraps, and
    listing them all buries the rows that answer the question being asked.
    """

    needed = [finding for finding in findings if finding.needed]
    state.console.print(
        console_table(
            "Webservices",
            ["Status", "Key", "Used by", "Host"],
            [
                (
                    STATUS_LABELS[finding.status],
                    finding.key,
                    ", ".join(finding.powers),
                    _host(finding.url),
                )
                for finding in needed
            ],
        )
    )

    others = [finding for finding in findings if not finding.needed]
    if others:
        # Marked inline rather than left to the notes below: an entry Apple
        # sent without a url should be visible where the key is read.
        keys = ", ".join(
            f"{finding.key} (no url)"
            if finding.status is WebserviceStatus.MALFORMED
            else finding.key
            for finding in others
        )
        state.console.print(
            f"\nApple also advertises {len(others)} service(s) pyicloud does "
            f"not use: {keys}"
        )

    noteworthy = [finding for finding in findings if finding.detail]
    if noteworthy:
        state.console.print("\nNotes:")
        for finding in noteworthy:
            state.console.print(f"  {finding.key}: {finding.detail}")


def _print_probes(state: CLIState, results: tuple[ProbeResult, ...]) -> None:
    """Render the probe section."""

    state.console.print(
        console_table(
            "Service probes",
            ["Status", "Service", "Read", "ms"],
            [
                (
                    PROBE_LABELS[result.status],
                    result.service,
                    _probe_description(result.service),
                    result.elapsed_ms or "",
                )
                for result in results
            ],
        )
    )

    noteworthy = [result for result in results if result.detail]
    if noteworthy:
        state.console.print("\nProbe notes:")
        for result in noteworthy:
            state.console.print(f"  {result.service}: {result.detail}")


def _probe_description(service: str) -> str:
    """Return what the probe for a service actually did."""

    return next(
        (probe.describe for probe in SERVICE_PROBES if probe.service == service), ""
    )


def _print_verdict(
    state: CLIState,
    findings: tuple[WebserviceFinding, ...],
    session: dict[str, Any],
    probes: tuple[ProbeResult, ...],
) -> None:
    """Render the closing line, which is the part that tells the user what to do."""

    if not session["authenticated"]:
        # The how-to-log-in advice was already printed with the session table;
        # this only has to say what could not be checked as a result.
        state.console.print(
            "\nApple's service map cannot be checked without a session, "
            "so this report is incomplete."
        )
        return

    problems = webservice_problems(findings)
    failed_probes = probe_failures(probes)

    if not problems and not failed_probes:
        # The wording has to stay honest about what was actually checked: with
        # no probes, a healthy map is weaker evidence than it sounds.
        checked = (
            "Apple is advertising every service this version of pyicloud "
            "needs, and each one answered."
            if probes
            else "Apple is advertising every service this version of pyicloud "
            "needs. Nothing was called; add --probe to test the endpoints "
            "themselves."
        )
        state.console.print(
            f"\nNo problems found. {checked}\nIf something is still failing, "
            f"please report it at {ISSUE_TRACKER} and include this output."
        )
        return

    at_risk = sorted({
        *services_at_risk(findings),
        *(result.service for result in failed_probes),
    })
    count = len(problems) + len(failed_probes)
    state.console.print(
        f"\n{count} problem(s) found, affecting: {', '.join(at_risk) or 'none'}.\n"
        "This is Apple's side rather than your configuration, which usually "
        f"means pyicloud needs updating — please report it at {ISSUE_TRACKER} "
        "and include this output."
    )


def doctor(  # noqa: PLR0913
    ctx: typer.Context,
    username: UsernameOption = None,
    session_dir: SessionDirOption = None,
    http_proxy: HttpProxyOption = None,
    https_proxy: HttpsProxyOption = None,
    probe: ProbeOption = False,
    no_verify_ssl: NoVerifySslOption = False,
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT,
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL,
) -> None:
    """Check the local install, the session, and Apple's advertised services.

    Read-only: nothing here modifies the account. With --probe it additionally
    calls each service once, which is the only way to catch an endpoint that
    has been withdrawn behind a host Apple still advertises.

    Exits non-zero when a problem is found, and also when there is no session,
    since the checks that matter could not be run at all.
    """

    store_command_options(
        ctx,
        username=username,
        session_dir=session_dir,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_verify_ssl=no_verify_ssl,
        output_format=output_format,
        log_level=log_level,
    )
    state = get_state(ctx)

    env = environment()
    session, api = _session_report(state)
    # Gated on the session status rather than on `api` being non-null: the two
    # can disagree, because the status is re-read after get_api() returns, and
    # a report that lists findings while the verdict says the map could not be
    # checked contradicts itself.
    authenticated = bool(session["authenticated"])
    findings = (
        diagnose_webservices(api.webservices)
        if api is not None and authenticated
        else ()
    )
    problems = webservice_problems(findings)
    # Gated on the session too, for the same reason the findings are: calling
    # every service with a session that reports unauthenticated would produce
    # a page of failures that say nothing about the library.
    probes = (
        probe_services(api, findings)
        if (probe and api is not None and authenticated)
        else ()
    )
    # Not being logged in is not a defect, but it does mean nothing was
    # verified -- reporting that as success would mislead anything scripting
    # against the exit code.
    ok = authenticated and not problems and not probe_failures(probes)

    if state.json_output:
        state.write_json({
            "ok": ok,
            "environment": env,
            "session": session,
            "webservices": [_finding_payload(finding) for finding in findings],
            "probes": [_probe_payload(result) for result in probes],
            "services_at_risk": list(services_at_risk(findings)),
        })
    else:
        _print_environment(state, env)
        state.console.print()
        _print_session(state, session)
        if findings:
            state.console.print()
            _print_webservices(state, findings)
        if probes:
            state.console.print()
            _print_probes(state, probes)
        _print_verdict(state, findings, session, probes)

    if not ok:
        raise typer.Exit(code=1)
