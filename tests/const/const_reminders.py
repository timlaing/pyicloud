"""Sanitized device-authored Reminders CRDT test constants."""

# These documents were captured from a throwaway reminder titled "CRDT test",
# then sanitized before committing: the original device's replica UUID was
# replaced with DEVICE_REPLICA_UUID and the payload was rewrapped with gzip
# using a fixed timestamp. They contain no account, list, or record identifier.
DEVICE_REPLICA_UUID = bytes.fromhex("00112233445566778899aabbccddeeff")

# Device-authored title: "CRDT test". One visible run, no tombstones.
DEVICE_TITLE_BASELINE = (
    "H4sIAAAAAAACE+NgEMrnYBBgkMoU4nQOcglRKEktLpES4GIBCQKFwbQGI1iEESjCKQWm"
    "NZikxLg4gHL/gYAfqA7OVpLhkuISYBBUMnYJTSvvmLlq95m77/4LMXFwAjGjFpAGAB0m"
    "X8FzAAAA"
)

# Device-authored edit: "CRDT test" -> "CRDT test 2". The former run is
# tombstoned with the device's deletion counter, and the visible run starts at
# character clock 9 in the same replica.
DEVICE_TITLE_EDIT = (
    "H4sIAAAAAAACE+NgEGpl5GAQYJCqF+J2DnIJUShJLS5RMJIS4GIBCQMlwLQGI1iEUYB"
    "TgFsKRDNoMEkJgUUYBDjBIhwKjBrMUmJcHED1/4GAH6gXzlaS4ZLiEmAQVDJ2CU0r75i"
    "5aveZu+/+CzFxiAAxpxYTBzcApf81P4oAAAA="
)
