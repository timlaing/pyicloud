"""Bundle shared leaf-command options without changing Typer's visible signature."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from functools import wraps
import inspect
from typing import Any, get_type_hints

import typer

from .options import (
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


@dataclass(frozen=True)
class CommandOptions:
    """Shared authentication, transport, and output choices for one command."""

    username: UsernameOption = None
    session_dir: SessionDirOption = None
    http_proxy: HttpProxyOption = None
    https_proxy: HttpsProxyOption = None
    no_verify_ssl: NoVerifySslOption = False
    output_format: OutputFormatOption = DEFAULT_OUTPUT_FORMAT
    log_level: LogLevelOption = DEFAULT_LOG_LEVEL

    def store(self, ctx: typer.Context) -> None:
        """Store choices only when the command has completed local validation."""
        store_command_options(ctx, **asdict(self))


def with_command_options(function: Callable[..., None]) -> Callable[..., None]:
    """Expose the usual seven CLI flags while passing one typed options bundle."""
    return with_option_bundle(
        function, bundle_type=CommandOptions, parameter_name="common"
    )


def with_option_bundle(
    function: Callable[..., None], *, bundle_type: type[Any], parameter_name: str
) -> Callable[..., None]:
    """Expose a dataclass's option fields while passing the bundle to a handler."""
    option_fields = fields(bundle_type)
    annotations = get_type_hints(function, include_extras=True)
    option_annotations = get_type_hints(bundle_type, include_extras=True)
    parameters = [
        parameter.replace(annotation=annotations[parameter.name])
        for parameter in inspect.signature(function).parameters.values()
        if parameter.name != parameter_name
    ]
    parameters.extend(
        inspect.Parameter(
            field.name,
            inspect.Parameter.KEYWORD_ONLY,
            default=field.default,
            annotation=option_annotations[field.name],
        )
        for field in option_fields
    )

    @wraps(function)
    def invoke(*args: Any, **kwargs: Any) -> None:
        bundle = bundle_type(**{
            field.name: kwargs.pop(field.name, field.default) for field in option_fields
        })
        function(*args, **{parameter_name: bundle}, **kwargs)

    invoke.__dict__["__signature__"] = inspect.Signature(
        parameters, return_annotation=None
    )
    invoke.__annotations__ = {**annotations, **option_annotations}
    return invoke
