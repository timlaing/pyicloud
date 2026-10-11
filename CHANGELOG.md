# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog].

Releases up to `2.7.0` follow [Semantic Versioning](https://semver.org/). From that release onward, versions follow [CalVer](https://calver.org/) format (`YYYY.M.PATCH`).

## [Unreleased]

### Fixed

- Reject advertised service endpoints that do not use HTTPS (#401)
- Refuse Reminder updates after a text document fails to decode (#400)
- Correct Calendar end-date minutes remaining until midnight (#399)
- List nested Notes folders from the zone change feed (#398)
- Preserve Find My authentication cookies when loading a session (#397)
- Address shared-folder downloads using the document owner (#396)
- Preserve duplicated Photos assets that share a master (#395)
- Keep GPS fresh via locating monitor refreshes in Find My iPhone (#390)
- Accept SMS codes Apple validates with a 409 (#389)
- Parse Date alarm triggers in Reminders instead of dropping them (#392)
- Do not report an unreachable iCloud as a failed login (#357)
- Make rich optional so the core install imports cleanly (#369)

### Refactored

- Reduce cognitive complexity and fix code smells (#409)
- Resolve SonarQube complexity and exception-test findings (#410)
- Resolve open SonarQube findings (S5713, S8513, S3776, S107, S2083) (#411)

### Changed

- Update package name to pyicloud and add allowScripts configuration

### Features

- Update @opencode/cli version and clean up setup script

### Tests

- Raise branch coverage above 90% for all source files (#394, #406)
- Drop coverage pragmas and cover the exposed branches (#408)
- Isolate filesystem access behind a sanctioned load_fixture helper (#407)

### Misc

- Various dependency and infrastructure updates

## [2.7.0] - 2026-08-26

### Added

- Identify withdrawn endpoints instead of a generic response error (#334)
- Add an inventory of the webservices the library depends on (#335)
- Add `icloud doctor` CLI command for read-only service diagnostics (#336)
- Make family device polling delay and max retries configurable (#326)
- Support minimal (paused) 2FA auth for Find My operations (#325)
- Expose `deleted` on RemindersList (#319)

### Fixed

- Resolve SonarCloud findings on main (#340, #344)
- Port Photos uploads to Apple's photosupload endpoint (#331)
- Request PCS access so encrypted fields decode for Reminders (#317)
- Raise PyiCloudPCSTimeoutException when PCS retries are exhausted (#343)
- Request PCS access so encrypted fields decode for Notes (#318)
- Report an advertised webservice with no url as unavailable (#337)
- Read the shared scope zone by zone in invites (#339)
- Refresh webservices after token authentication (#332)
- Parse Apple's date arrays into datetimes for Calendar (#320)
- Handle Apple authenticationType key in 409 2FA responses (#323)
- Handle nested JSON boot context and auth-state wipe during SRP 2FA (#322)
- Declare CRDT lengths in UTF-16 code units, not codepoints for Reminders (#304)
- Fix China mainland auth endpoints and 2FA fallback (#285)
- Adjust Dependabot pip updates to only widen requirements when needed (#291)

### Changed

- Refactor setup/startup scripts: set -e + pipefail, opt-in trace (#327)
- Strict-mode cleanup (mypy/pylint) with SonarQube noqa suppressions (#321)

### Dependencies

- Bump actions/checkout from 4 to 7 (#329)
- Bump actions/setup-node from 4 to 7 (#328)
- Bump actions/setup-python from 5 to 7 (#330)
- Bump github/codeql-action/analyze/init from 4.36.2 to 4.37.8 (#314, #315)
- Bump actions/setup-python from 6 to 7 (#299)
- Update setuptools requirement from <82.1,>=77.0 to >=77.0,<84.1 (#309)
- Update ruff requirement from >=0.15.15 to >=0.15.20 (#280, #287)
- Bump ghcr.io/devcontainers/features/docker-in-docker from 3.1.0 to 4.0.0 (#286)
- Bump actions/checkout from 6 to 7 (#284)

### Contributors

@FezVrasta, @hackermengzhi, @MrJarnould, @timlaing, @YoraiLevi, [@copilot-swe-agent[bot]], [@dependabot[bot]], Cherry, @claude, @Copilot

## [2.6.5] - 2026-06-09

### Features

- Add additional video media versions for thumbnails and medium images (#277)

### Dependencies

- Update srp requirement from >=1.0.21 to >=1.0.22 (#271)
- Update pydantic requirement from <3,>=2.12 to >=2.13.4,<3 (#270)
- Bump actions/github-script from 8 to 9 (#275)
- Bump github/codeql-action from 4.36.0 to 4.36.2 (#278)
- Update click requirement from >=8.1.8 to >=8.4.1 (#273)
- Update isort requirement from >=5.11.5 to >=8.0.1 (#272)
- Update ruff requirement from >=0.15.14 to >=0.15.15 (#274)

## [2.6.4] - 2026-05-19

### Fixed

- Fix invalid credentials handling in response from iCloud (#269)
- Fix Service Unavailable error when logging in (#266)
- Handle preexisting session state better (#265)

### Dependencies

- Bump github/codeql-action from 4.28.20 to 4.36.0 (#264)
- Update pydantic requirement from <3,>=2.11 to >=2.12,<3 (#261)
- Update tzlocal requirement from >=5.3.1 to >=5.3.2 (#262)
- Update ruff requirement from >=0.15.9 to >=0.15.14 (#263)
- Bump actions/setup-python from 5.6.0 to 6.0.0 (#260)
- Bump actions/github-script from 7.0.1 to 8.0.0 (#259)
- Bump github/codeql-action from 4.28.17 to 4.28.20 (#258)
- Bump actions/checkout from 5.0.0 to 6.0.0 (#257)

## [2.6.3] - 2026-05-16

### Fixed

- Fix for Chinese mainland auth (#254)

## [2.6.2] - 2026-05-15

### Fixed

- Add missing test dependency for China mainland server test (#253)

## [2.6.1] - 2026-05-14

### Fixed

- Fix China mainland server detection (#252)

## [2.6.0] - 2026-05-14

### Added

- Add China mainland server support (#248)

### Dependencies

- Bump srp from 1.0.21 to 1.0.22 (#250)
- Bump ruff from >=0.15.9 to >=0.15.10 (#249)

## [2.6.0b0] - 2026-05-13

_Pre-release for testing China mainland support_

## [2.5.0] - 2026-04-30

### Added

- Add support for trusted device authentication with code delivery (#244)

### Fixed

- Fix and improve handling of 2FA scenarios (#243)
- Fix calendar service timezone handling (#236)

### Dependencies

- Bump github/codeql-action from 4.28.5 to 4.28.17 (#242, #241, #238)
- Bump ruff from >=0.15.3 to >=0.15.9 (#240, #239, #237)
- Bump pydantic from >=2.10.6 to >=2.11,<3 (#235)
- Bump tzlocal from >=5.3 to >=5.3.1 (#234)
- Bump actions/checkout from 4.2.2 to 5.0.0 (#233)
- Bump pytest from >=8.3.4 to >=8.4.1 (#232)

## [2.4.1] - 2026-03-26

### Fixed

- Fix HSA2 authentication flow for 2FA (#229)
- Fix calendar event parsing for all-day events (#228)

### Dependencies

- Bump ruff from >=0.15.1 to >=0.15.3 (#227)
- Bump click from >=8.1.8 to >=8.2.0 (#226)

## [2.4.0] - 2026-03-05

### Added

- Add Notes service (#219)
- Add Reminders service (#218)

### Fixed

- Fix Find My authentication and location refresh (#221)
- Fix Drive file download for shared items (#220)

### Dependencies

- Bump ruff from >=0.11.5 to >=0.15.1 (#217, #216)
- Bump pytest-cov from >=6.1.1 to >=6.2.1 (#215)
- Bump pydantic from >=2.10.6 to >=2.11.7 (#214)

## [2.3.0] - 2026-02-22

### Added

- Add support for iCloud Keychain and authentication tokens (#209)

### Fixed

- Fix Photos album handling (#208)
- Fix Drive upload issues (#207)

## [2.2.0] - 2026-02-08

### Added

- Add Calendar service (#205)

### Fixed

- Improve error handling for authentication failures (#204)

## [2.1.0] - 2026-01-25

### Added

- Add Find My iPhone service (#201)
- Add support for 2FA via SMS and email (#200)

### Fixed

- Fix session persistence (#199)

## [2.0.2] - 2025-12-15

### Fixed

- Bug fixes and stability improvements

## [2.0.0b6] - 2025-11-20

### Changed

- Beta release for v2.0.0

## [v2.0.0b6] - earlier

## [V2.0.2] - earlier

[Unreleased]: https://github.com/timlaing/pyicloud/compare/2.7.0...HEAD
[2.7.0]: https://github.com/timlaing/pyicloud/compare/2.6.5...2.7.0
[2.6.5]: https://github.com/timlaing/pyicloud/compare/2.6.4...2.6.5
[2.6.4]: https://github.com/timlaing/pyicloud/compare/2.6.3...2.6.4
[2.6.3]: https://github.com/timlaing/pyicloud/compare/2.6.2...2.6.3
[2.6.2]: https://github.com/timlaing/pyicloud/compare/2.6.1...2.6.2
[2.6.1]: https://github.com/timlaing/pyicloud/compare/2.6.0...2.6.1
[2.6.0]: https://github.com/timlaing/pyicloud/compare/2.6.0b0...2.6.0
[2.6.0b0]: https://github.com/timlaing/pyicloud/compare/2.5.0...2.6.0b0
[2.5.0]: https://github.com/timlaing/pyicloud/compare/2.4.1...2.5.0
[2.4.1]: https://github.com/timlaing/pyicloud/compare/2.4.0...2.4.1
[2.4.0]: https://github.com/timlaing/pyicloud/compare/2.3.0...2.4.0
[2.3.0]: https://github.com/timlaing/pyicloud/compare/2.2.0...2.3.0
[2.2.0]: https://github.com/timlaing/pyicloud/compare/2.1.0...2.2.0
[2.1.0]: https://github.com/timlaing/pyicloud/compare/2.0.2...2.1.0
[2.0.2]: https://github.com/timlaing/pyicloud/compare/2.0.0b6...2.0.2
[2.0.0b6]: https://github.com/timlaing/pyicloud/compare/V2.0.2...2.0.0b6
[V2.0.2]: https://github.com/timlaing/pyicloud/compare/2.0.0b6...V2.0.2
