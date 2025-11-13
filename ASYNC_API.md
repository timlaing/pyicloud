# Async API Guide

PyiCloud now supports asynchronous operations using Python's `async`/`await` syntax. This guide shows you how to use the async API.

## Installation

The async functionality requires `httpx`, which is included in the standard dependencies:

```bash
pip install pyicloud
```

## Basic Usage

### Using the Factory Method

The recommended way to create an async PyiCloud service is using the `create()` class method:

```python
import asyncio
from pyicloud import AsyncPyiCloudService

async def main():
    # Create and authenticate
    api = await AsyncPyiCloudService.create('jappleseed@apple.com', 'password')
    
    # Use the API
    print(f"Logged in as: {api.account_name}")
    print(f"Requires 2FA: {api.requires_2fa}")
    
    # Don't forget to close the session
    await api.close()

# Run the async function
asyncio.run(main())
```

### Using Context Manager (Recommended)

The preferred way is to use the async context manager, which automatically handles cleanup:

```python
import asyncio
from pyicloud import AsyncPyiCloudService

async def main():
    async with await AsyncPyiCloudService.create(
        'jappleseed@apple.com', 
        'password'
    ) as api:
        print(f"Logged in as: {api.account_name}")
        # API will be automatically closed when exiting the context

asyncio.run(main())
```

## Authentication

### Two-Factor Authentication (2FA)

If you have 2FA enabled:

```python
import asyncio
from pyicloud import AsyncPyiCloudService

async def main():
    async with await AsyncPyiCloudService.create(
        'jappleseed@apple.com',
        'password'
    ) as api:
        if api.requires_2fa:
            print("Two-factor authentication required.")
            code = input("Enter the code you received: ")
            result = await api.validate_2fa_code(code)
            print(f"Code validation result: {result}")
        
        if not api.is_trusted_session:
            print("Session is not trusted. Trusting session...")
            await api.trust_session()

asyncio.run(main())
```

### Two-Step Authentication (2SA)

For two-step authentication:

```python
import asyncio
from pyicloud import AsyncPyiCloudService

async def main():
    async with await AsyncPyiCloudService.create(
        'jappleseed@apple.com',
        'password'
    ) as api:
        if api.requires_2sa:
            print("Two-step authentication required.")
            devices = await api.get_trusted_devices()
            
            print("Trusted devices:")
            for i, device in enumerate(devices):
                print(f"  {i}: {device.get('deviceName', 'Unknown')}")
            
            device_index = int(input("Select a device: "))
            device = devices[device_index]
            
            if not await api.send_verification_code(device):
                print("Failed to send verification code")
                return
            
            code = input("Enter verification code: ")
            if not await api.validate_verification_code(device, code):
                print("Failed to verify code")
                return
            
            print("Verification successful!")

asyncio.run(main())
```

## Advanced Usage

### China Mainland

If your Apple ID is registered in China mainland:

```python
async with await AsyncPyiCloudService.create(
    'jappleseed@apple.com',
    'password',
    china_mainland=True
) as api:
    # Use the API
    pass
```

### Custom Cookie Directory

Store session data in a custom directory:

```python
async with await AsyncPyiCloudService.create(
    'jappleseed@apple.com',
    'password',
    cookie_directory='/path/to/cookies'
) as api:
    # Use the API
    pass
```

### Force Reauthentication

Force a fresh authentication:

```python
async with await AsyncPyiCloudService.create(
    'jappleseed@apple.com',
    'password'
) as api:
    await api.authenticate(force_refresh=True)
```

## Backward Compatibility

The original synchronous API (`PyiCloudService`) remains fully functional and is still the default import. Existing code will continue to work without any changes:

```python
# This still works exactly as before
from pyicloud import PyiCloudService

api = PyiCloudService('jappleseed@apple.com', 'password')
```

## Performance Benefits

The async API provides several advantages:

1. **Better Concurrency**: Handle multiple iCloud accounts or operations simultaneously
2. **Non-blocking I/O**: Your application can perform other tasks while waiting for network responses
3. **Resource Efficiency**: Lower memory footprint when handling multiple concurrent requests

## Example: Multiple Accounts

Here's an example of working with multiple iCloud accounts concurrently:

```python
import asyncio
from pyicloud import AsyncPyiCloudService

async def check_account(email, password):
    async with await AsyncPyiCloudService.create(email, password) as api:
        return {
            'email': api.account_name,
            'requires_2fa': api.requires_2fa,
            'is_trusted': api.is_trusted_session
        }

async def main():
    accounts = [
        ('user1@example.com', 'password1'),
        ('user2@example.com', 'password2'),
        ('user3@example.com', 'password3'),
    ]
    
    # Check all accounts concurrently
    results = await asyncio.gather(*[
        check_account(email, password) 
        for email, password in accounts
    ])
    
    for result in results:
        print(result)

asyncio.run(main())
```

## Migration Guide

### From Sync to Async

Here's how to migrate existing synchronous code to async:

**Before (Sync):**
```python
from pyicloud import PyiCloudService

api = PyiCloudService('user@example.com', 'password')
devices = api.trusted_devices
api.send_verification_code(devices[0])
```

**After (Async):**
```python
import asyncio
from pyicloud import AsyncPyiCloudService

async def main():
    async with await AsyncPyiCloudService.create(
        'user@example.com',
        'password'
    ) as api:
        devices = await api.get_trusted_devices()
        await api.send_verification_code(devices[0])

asyncio.run(main())
```

Key differences:
1. Use `AsyncPyiCloudService` instead of `PyiCloudService`
2. Create instances with `await AsyncPyiCloudService.create()` instead of direct instantiation
3. Add `await` before all I/O operations (methods that make network requests)
4. Wrap your code in an `async def` function
5. Run with `asyncio.run()`
6. Use `async with` context manager for automatic cleanup

## When to Use Async

Use the async API when:
- You need to handle multiple iCloud accounts simultaneously
- Your application is already using `asyncio`
- You want non-blocking I/O operations
- You're building a web server or high-concurrency application

Continue using the sync API when:
- You have simple, sequential scripts
- You're working with a single account
- Your application doesn't use `asyncio`
- You prefer simpler code structure

## Error Handling

Error handling works the same way in both sync and async APIs:

```python
import asyncio
from pyicloud import AsyncPyiCloudService
from pyicloud.exceptions import (
    PyiCloudFailedLoginException,
    PyiCloud2FARequiredException,
)

async def main():
    try:
        async with await AsyncPyiCloudService.create(
            'user@example.com',
            'wrong_password'
        ) as api:
            print("Logged in successfully")
    except PyiCloudFailedLoginException:
        print("Failed to log in - check your credentials")
    except PyiCloud2FARequiredException:
        print("2FA is required")

asyncio.run(main())
```

## Notes

- The async session uses `httpx` for HTTP requests instead of `requests`
- Session cookies and data are compatible between sync and async APIs
- You cannot mix sync and async code - either use `PyiCloudService` or `AsyncPyiCloudService` consistently
- Remember to always close async sessions (use context manager for automatic cleanup)
