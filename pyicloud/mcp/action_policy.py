"""Fixed action risk policy; callers cannot downgrade risk through payload fields."""

STANDARD_WRITE_ACTIONS = (
    "reminders.create",
    "reminders.update",
    "reminders.delete",
    "drive.rename",
    "drive.trash_file",
    "drive.move_file",
    "calendar.create_event",
    "mail.create_draft",
    "mail.edit_draft",
    "mail.create_folder",
    "mail.rename_folder",
    "photos.set_favorite",
    "photos.rename_album",
    "photos.create_album",
    "photos.add_to_album",
)

# No verified Calendar event recovery contract: classify deletion conservatively.
SUPPORTED_HIGH_RISK_ACTIONS = ("calendar.delete_event",)


def is_high_risk(action: str) -> bool:
    """New/unknown actions fail closed as high risk until independently classified."""
    return action not in STANDARD_WRITE_ACTIONS
