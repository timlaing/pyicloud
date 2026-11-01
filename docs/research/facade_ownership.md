# Facade and shared-state ownership inventory

This inventory precedes the focused #479 extractions and uses the released public
baseline established by #478. It is not a wholesale layout migration.

| Area   | Public definitions/re-exports                                                                                                                                              | State owner                                                                                                                                      | Extraction boundary                                                                                                                                                        |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Core   | `PyiCloudService`, `TrustedPhoneNumber`, `PhoneNumberVerification` stay in `pyicloud.base`; root `PyiCloudService` and `AppleDevice` remain re-exports of the same classes | Core owns endpoints, authentication orchestration, the shared `PyiCloudSession`, request parameters, bridge/2FA state and lazy service instances | Stateless authentication header snapshots and verification-response classification in `_auth_protocol`; persistence remains on the existing session/cookie implementations |
| Photos | `PhotosService`, library/album/container and asset classes remain in `pyicloud.services.photos.service`, with package re-exports and defining-module metadata retained     | Service/library/album objects retain session references, discovered libraries, album caches, sync state, resources and upload-hydration budgets  | Query/filter construction and serialization belong to `queries`; existing `mappers` and `materialize` own decoding/resources; no public class relocation                   |
| Mail   | `MailService` stays in `pyicloud.services.mail.service`; public models and exception re-exports retain their paths                                                         | The facade retains session/params, account and mailbox identity checks, request order, response closing and iterator timing                      | Query preparation and whole-page pagination validation in `_pagination`; existing draft, folder, sender and sync helpers remain distinct                                   |

`BaseService` keeps references to the supplied mutable session and parameter
dictionary; this work does not copy their ownership or create a second transport.
Headers/query snapshots must not mutate caller inputs. Mail whole-page validation
must precede yielding any page record; lazy iterators must not prefetch. Photos
query owner, projection and continuation fields must survive serialization.
Authentication trust must precede token validation and preserve transport errors.

The intended typed consumer surface is the documented root/service facades and
their documented return models/exceptions, not every annotation-bearing private
helper. Generated protobuf modules and existing Notes `.pyi` package data stay
unchanged. Publishing `py.typed`, migrating public models or enforcing a new static
consumer contract requires a separate decision and downstream consumer checks;
none is included in these runtime refactors.

Owning behavior tests were added and run before extraction, then rerun after it.
Keep the released signature/identity manifest and installed-package smokes as
prerequisites. Existing meaningful `test_cov_*` edge assertions remain unless the
owning behavior is deliberately consolidated; no coverage exclusions are added.
