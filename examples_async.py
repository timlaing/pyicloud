#!/usr/bin/env python3
"""
Example script demonstrating both synchronous and asynchronous PyiCloud usage.

This script shows how to use both the traditional sync API and the new async API
for common operations.
"""

import asyncio
import sys


def sync_example():
    """Example using synchronous PyiCloudService."""
    print("=" * 60)
    print("SYNCHRONOUS EXAMPLE")
    print("=" * 60)
    
    from pyicloud import PyiCloudService
    
    # This is the traditional way - simple and straightforward
    try:
        api = PyiCloudService('user@example.com', 'password')
        
        print(f"✓ Logged in as: {api.account_name}")
        print(f"  Requires 2FA: {api.requires_2fa}")
        print(f"  Is trusted session: {api.is_trusted_session}")
        
        # Note: This is a mock example - in real usage you'd interact with
        # actual iCloud services here
        
    except Exception as e:
        print(f"✗ Error: {e}")
    
    print()


async def async_example():
    """Example using asynchronous AsyncPyiCloudService."""
    print("=" * 60)
    print("ASYNCHRONOUS EXAMPLE")
    print("=" * 60)
    
    from pyicloud import AsyncPyiCloudService
    
    # Using context manager (recommended)
    try:
        async with await AsyncPyiCloudService.create(
            'user@example.com',
            'password'
        ) as api:
            print(f"✓ Logged in as: {api.account_name}")
            print(f"  Requires 2FA: {api.requires_2fa}")
            print(f"  Is trusted session: {api.is_trusted_session}")
            
            # The session is automatically closed when exiting the context
            
    except Exception as e:
        print(f"✗ Error: {e}")
    
    print()


async def async_multiple_accounts():
    """Example showing concurrent operations with multiple accounts."""
    print("=" * 60)
    print("ASYNC CONCURRENT EXAMPLE - Multiple Accounts")
    print("=" * 60)
    
    from pyicloud import AsyncPyiCloudService
    
    async def check_account(email, password):
        """Check a single account."""
        try:
            async with await AsyncPyiCloudService.create(email, password) as api:
                return {
                    'email': api.account_name,
                    'status': 'success',
                    'requires_2fa': api.requires_2fa,
                    'is_trusted': api.is_trusted_session,
                }
        except Exception as e:
            return {
                'email': email,
                'status': 'error',
                'error': str(e),
            }
    
    # These accounts would be checked concurrently (not sequentially)
    accounts = [
        ('user1@example.com', 'password1'),
        ('user2@example.com', 'password2'),
        ('user3@example.com', 'password3'),
    ]
    
    print(f"Checking {len(accounts)} accounts concurrently...")
    
    # This runs all account checks at the same time!
    results = await asyncio.gather(*[
        check_account(email, password)
        for email, password in accounts
    ])
    
    print("\nResults:")
    for result in results:
        if result['status'] == 'success':
            print(f"  ✓ {result['email']}: 2FA={result['requires_2fa']}, Trusted={result['is_trusted']}")
        else:
            print(f"  ✗ {result['email']}: {result['error']}")
    
    print()


def main():
    """Run all examples."""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║  PyiCloud Examples - Sync and Async Usage               ║")
    print("╚" + "=" * 58 + "╝")
    print()
    
    # Note: These examples use mock credentials and will fail to authenticate
    # In real usage, you would:
    # 1. Use actual iCloud credentials
    # 2. Handle 2FA/2SA challenges
    # 3. Interact with actual iCloud services
    
    print("Note: These examples use mock credentials for demonstration.")
    print("In real usage, replace with actual credentials.\n")
    
    # Run synchronous example
    # sync_example()
    
    # Run asynchronous examples
    # asyncio.run(async_example())
    # asyncio.run(async_multiple_accounts())
    
    # For now, just show the structure
    print("Examples are defined but commented out to avoid authentication failures.")
    print("Uncomment the lines above to run with real credentials.")
    print()
    
    # Show usage patterns
    print("=" * 60)
    print("USAGE PATTERNS")
    print("=" * 60)
    print()
    
    print("Synchronous (traditional):")
    print("  from pyicloud import PyiCloudService")
    print("  api = PyiCloudService('user@example.com', 'password')")
    print("  print(api.account_name)")
    print()
    
    print("Asynchronous (new):")
    print("  import asyncio")
    print("  from pyicloud import AsyncPyiCloudService")
    print("  ")
    print("  async def main():")
    print("      async with await AsyncPyiCloudService.create(")
    print("          'user@example.com', 'password'")
    print("      ) as api:")
    print("          print(api.account_name)")
    print("  ")
    print("  asyncio.run(main())")
    print()
    
    print("For more examples, see:")
    print("  • ASYNC_API.md - Comprehensive async guide")
    print("  • README.md - General usage documentation")
    print()


if __name__ == '__main__':
    main()
