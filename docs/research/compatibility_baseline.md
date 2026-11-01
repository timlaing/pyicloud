# Consumer compatibility baseline

Release `2026.10.1`, commit `2d3d137e17cdd06e4e58a4d0e0e400dccb31b08f`, is
the recorded baseline in `tests/fixtures/compatibility/2026.10.1.json`.
Expectations were captured from a detached checkout of that release, not from
the code under test. The manifest deliberately records representative contracts,
not every private method or every Apple response field.

It covers root/service facade imports, constructor and selected mutation/read
signatures (parameter order, kinds and defaults), original class module metadata,
exception catch relationships, reminder/list fields and defaults, CLI list JSON
and usage exit codes, session JSON filtering and LWP cookie round trips.
Optional keyword-only parameters and additional result fields are allowed;
removed symbols, shifted positional parameters, changed defaults or removed fields
are not. Mail and Invites are additive post-baseline services checked by installed
smokes. The Photos module move in #472 is explicitly recorded as an already-made
compatibility decision, not silently inferred from the current implementation.

Run `python scripts/check/compatibility.py tests/fixtures/compatibility/2026.10.1.json`
and `pytest tests/test_public_compatibility.py`. Installed checks use `python -I`
from outside the checkout and runtime-only/CLI environments; they load the same
manifest without installing an old release or reaching Apple. Both generated
protobuf modules and the Notes `.pyi` package data must be present.

There is intentionally no automatic update/bless command. Changes to expectations
require human review, an explicit reason/decision in the manifest, release notes
and migration guidance where appropriate. Expand coverage with frozen examples
when a new supported contract is added; do not derive expected values from the
current implementation during a test.

The existing `test (3.x)` check names are unchanged. The minimum-runtime job is a
new check, not a replacement; maintainers can add it to branch rules after rollout.
`requirements_min.txt` pins direct runtime lower bounds, with transitive versions
chosen normally by the resolver. The `typing-extensions` lower bound is aligned
with Pydantic 2.13.4's actual requirement rather than pretending 4.5.0 can satisfy
that environment. Keep those pins synchronized with declared runtime bounds.
