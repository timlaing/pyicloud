# Account

[← Back to the documentation index](../index.md)

You can access information about your iCloud account using the `account` property:

```pycon
>>> api.account
{devices: 5, family: 3, storage: 8990635296 bytes free}
```

## Summary Plan

you can access information about your iCloud account\'s summary plan using the `account.summary_plan` property:

```pycon
>>> api.account.summary_plan
{'featureKey': 'cloud.storage', 'summary': {'includedInPlan': True, 'limit': 50, 'limitUnits': 'GIB'}, 'includedWithAccountPurchasedPlan': {'includedInPlan': True, 'limit': 50, 'limitUnits': 'GIB'}, 'includedWithAppleOnePlan': {'includedInPlan': False}, 'includedWithSharedPlan': {'includedInPlan': False}, 'includedWithCompedPlan': {'includedInPlan': False}, 'includedWithManagedPlan': {'includedInPlan': False}}
```

## Storage

You can get the storage information of your iCloud account using the `account.storage` property:

```pycon
>>> api.account.storage
{usage: 85.12% used of 53687091200 bytes, usages_by_media: {'photos': <AccountStorageUsageForMedia: {key: photos, usage: 41785285900 bytes}>, 'backup': <AccountStorageUsageForMedia: {key: backup, usage: 27250085 bytes}>, 'docs': <AccountStorageUsageForMedia: {key: docs, usage: 3810332430 bytes}>, 'mail': <AccountStorageUsageForMedia: {key: mail, usage: 26208942 bytes}>, 'messages': <AccountStorageUsageForMedia: {key: messages, usage: 1379351 bytes}>}}
```

You even can generate a pie chart:

```python
......
storage = api.account.storage
y = []
colors = []
labels = []
for usage in storage.usages_by_media.values():
    y.append(usage.usage_in_bytes)
    colors.append(f"#{usage.color}")
    labels.append(usage.label)

plt.pie(y,
        labels=labels,
        colors=colors,
        )
plt.title("Storage Pie Test")
plt.show()
```
