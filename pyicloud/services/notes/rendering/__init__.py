"""Rendering support for Apple Notes (proto3), transport-agnostic.

Contains:
- renderer_iface: the minimal datasource Protocol and AttachmentRef value type
- renderer: pure HTML renderer for note content (fragment + page)
- ck_datasource: CloudKit-backed in-memory datasource for attachments
"""
