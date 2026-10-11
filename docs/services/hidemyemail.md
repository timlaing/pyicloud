# Hide My Email

[← Back to the documentation index](../README.md)

You can access the iCloud Hide My Email service through the `hidemyemail` property

To generate a new email alias use the `generate` method.

```python
# Generate a new email alias
new_email = api.hidemyemail.generate()
if new_email is None:
    raise RuntimeError("Failed to generate a new email alias")
print(f"Generated new email: {new_email}")
```

To reserve the generated email with a custom label

```python
reserved = api.hidemyemail.reserve(new_email, "Shopping")
if not reserved:
    raise RuntimeError("Failed to reserve the generated email")
print(f"Reserved email - response: {reserved}")
```

To get the anonymous_id (unique identifier) from the reservation.

```python
anonymous_id = reserved.get("anonymousId")
if anonymous_id is None:
    raise RuntimeError("Reservation did not return an anonymousId")
print(anonymous_id)
```

To list the current aliases

```python
# Print details of each alias
for alias in api.hidemyemail:
    print(f"- {alias.get('hme')}: {alias.get('label')} ({alias.get('anonymousId')})")
```

Additional detail usage

```python
# Get detailed information about a specific alias
alias_details = api.hidemyemail[anonymous_id]
print(f"Alias details: {alias_details}")

# Update the alias metadata (label and note)
updated = api.hidemyemail.update_metadata(
    anonymous_id, "Online Shopping", "Used for e-commerce websites"
)
print(f"Updated alias: {updated}")

# Deactivate an alias (stops email forwarding but keeps the alias for future reactivation)
deactivated = api.hidemyemail.deactivate(anonymous_id)
print(f"Deactivated alias: {deactivated}")

# Reactivate a previously deactivated alias (resumes email forwarding)
reactivated = api.hidemyemail.reactivate(anonymous_id)
print(f"Reactivated alias: {reactivated}")

# Delete the alias when no longer needed
deleted = api.hidemyemail.delete(anonymous_id)
print(f"Deleted alias: {deleted}")
```
