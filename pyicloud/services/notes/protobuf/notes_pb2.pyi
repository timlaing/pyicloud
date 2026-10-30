from collections.abc import Iterable as _Iterable
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper

DESCRIPTOR: _descriptor.FileDescriptor

class Highlight(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    HIGHLIGHT_UNKNOWN: _ClassVar[Highlight]
    HIGHLIGHT_PURPLE: _ClassVar[Highlight]
    HIGHLIGHT_PINK: _ClassVar[Highlight]
    HIGHLIGHT_ORANGE: _ClassVar[Highlight]
    HIGHLIGHT_MINT: _ClassVar[Highlight]
    HIGHLIGHT_BLUE: _ClassVar[Highlight]

class StyleType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STYLE_TYPE_TITLE: _ClassVar[StyleType]
    STYLE_TYPE_HEADING: _ClassVar[StyleType]
    STYLE_TYPE_SUBHEADING: _ClassVar[StyleType]
    STYLE_TYPE_MONOSPACED: _ClassVar[StyleType]
    STYLE_TYPE_BULLET_LIST_ITEM: _ClassVar[StyleType]
    STYLE_TYPE_DASHED_LIST_ITEM: _ClassVar[StyleType]
    STYLE_TYPE_NUMBERED_LIST_ITEM: _ClassVar[StyleType]
    STYLE_TYPE_CHECKLIST_ITEM: _ClassVar[StyleType]

class WritingDirection(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    WRITING_DIRECTION_DEFAULT: _ClassVar[WritingDirection]
    WRITING_DIRECTION_LTR: _ClassVar[WritingDirection]
    WRITING_DIRECTION_RTL: _ClassVar[WritingDirection]

class Alignment(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ALIGNMENT_DEFAULT: _ClassVar[Alignment]
    ALIGNMENT_CENTER: _ClassVar[Alignment]
    ALIGNMENT_RIGHT: _ClassVar[Alignment]
    ALIGNMENT_JUSTIFY: _ClassVar[Alignment]

class FontWeight(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    FONT_WEIGHT_UNKNOWN: _ClassVar[FontWeight]
    FONT_WEIGHT_BOLD: _ClassVar[FontWeight]
    FONT_WEIGHT_ITALIC: _ClassVar[FontWeight]
    FONT_WEIGHT_BOLD_ITALIC: _ClassVar[FontWeight]

HIGHLIGHT_UNKNOWN: Highlight
HIGHLIGHT_PURPLE: Highlight
HIGHLIGHT_PINK: Highlight
HIGHLIGHT_ORANGE: Highlight
HIGHLIGHT_MINT: Highlight
HIGHLIGHT_BLUE: Highlight
STYLE_TYPE_TITLE: StyleType
STYLE_TYPE_HEADING: StyleType
STYLE_TYPE_SUBHEADING: StyleType
STYLE_TYPE_MONOSPACED: StyleType
STYLE_TYPE_BULLET_LIST_ITEM: StyleType
STYLE_TYPE_DASHED_LIST_ITEM: StyleType
STYLE_TYPE_NUMBERED_LIST_ITEM: StyleType
STYLE_TYPE_CHECKLIST_ITEM: StyleType
WRITING_DIRECTION_DEFAULT: WritingDirection
WRITING_DIRECTION_LTR: WritingDirection
WRITING_DIRECTION_RTL: WritingDirection
ALIGNMENT_DEFAULT: Alignment
ALIGNMENT_CENTER: Alignment
ALIGNMENT_RIGHT: Alignment
ALIGNMENT_JUSTIFY: Alignment
FONT_WEIGHT_UNKNOWN: FontWeight
FONT_WEIGHT_BOLD: FontWeight
FONT_WEIGHT_ITALIC: FontWeight
FONT_WEIGHT_BOLD_ITALIC: FontWeight

class Color(_message.Message):
    __slots__ = ("alpha", "blue", "green", "red")
    RED_FIELD_NUMBER: _ClassVar[int]
    GREEN_FIELD_NUMBER: _ClassVar[int]
    BLUE_FIELD_NUMBER: _ClassVar[int]
    ALPHA_FIELD_NUMBER: _ClassVar[int]
    red: float
    green: float
    blue: float
    alpha: float
    def __init__(
        self,
        red: float | None = ...,
        green: float | None = ...,
        blue: float | None = ...,
        alpha: float | None = ...,
    ) -> None: ...

class AttachmentInfo(_message.Message):
    __slots__ = ("attachment_identifier", "type_uti")
    ATTACHMENT_IDENTIFIER_FIELD_NUMBER: _ClassVar[int]
    TYPE_UTI_FIELD_NUMBER: _ClassVar[int]
    attachment_identifier: str
    type_uti: str
    def __init__(
        self,
        attachment_identifier: str | None = ...,
        type_uti: str | None = ...,
    ) -> None: ...

class Font(_message.Message):
    __slots__ = ("font_hints", "font_name", "point_size")
    FONT_NAME_FIELD_NUMBER: _ClassVar[int]
    POINT_SIZE_FIELD_NUMBER: _ClassVar[int]
    FONT_HINTS_FIELD_NUMBER: _ClassVar[int]
    font_name: str
    point_size: float
    font_hints: int
    def __init__(
        self,
        font_name: str | None = ...,
        point_size: float | None = ...,
        font_hints: int | None = ...,
    ) -> None: ...

class ParagraphStyle(_message.Message):
    __slots__ = (
        "alignment",
        "block_quote",
        "checklist",
        "indent_amount",
        "paragraph_uuid",
        "starting_list_item_number",
        "style_type",
        "writing_direction_paragraph",
    )
    STYLE_TYPE_FIELD_NUMBER: _ClassVar[int]
    ALIGNMENT_FIELD_NUMBER: _ClassVar[int]
    WRITING_DIRECTION_PARAGRAPH_FIELD_NUMBER: _ClassVar[int]
    INDENT_AMOUNT_FIELD_NUMBER: _ClassVar[int]
    CHECKLIST_FIELD_NUMBER: _ClassVar[int]
    STARTING_LIST_ITEM_NUMBER_FIELD_NUMBER: _ClassVar[int]
    BLOCK_QUOTE_FIELD_NUMBER: _ClassVar[int]
    PARAGRAPH_UUID_FIELD_NUMBER: _ClassVar[int]
    style_type: StyleType
    alignment: Alignment
    writing_direction_paragraph: WritingDirection
    indent_amount: int
    checklist: Checklist
    starting_list_item_number: int
    block_quote: int
    paragraph_uuid: bytes
    def __init__(
        self,
        style_type: StyleType | str | None = ...,
        alignment: Alignment | str | None = ...,
        writing_direction_paragraph: WritingDirection | str | None = ...,
        indent_amount: int | None = ...,
        checklist: Checklist | _Mapping | None = ...,
        starting_list_item_number: int | None = ...,
        block_quote: int | None = ...,
        paragraph_uuid: bytes | None = ...,
    ) -> None: ...

class Checklist(_message.Message):
    __slots__ = ("done", "uuid")
    UUID_FIELD_NUMBER: _ClassVar[int]
    DONE_FIELD_NUMBER: _ClassVar[int]
    uuid: bytes
    done: int
    def __init__(self, uuid: bytes | None = ..., done: int | None = ...) -> None: ...

class DictionaryElement(_message.Message):
    __slots__ = ("key", "value")
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    key: ObjectID
    value: ObjectID
    def __init__(
        self,
        key: ObjectID | _Mapping | None = ...,
        value: ObjectID | _Mapping | None = ...,
    ) -> None: ...

class Dictionary(_message.Message):
    __slots__ = ("element",)
    ELEMENT_FIELD_NUMBER: _ClassVar[int]
    element: _containers.RepeatedCompositeFieldContainer[DictionaryElement]
    def __init__(
        self, element: _Iterable[DictionaryElement | _Mapping] | None = ...
    ) -> None: ...

class ObjectID(_message.Message):
    __slots__ = ("object_index", "string_value", "unsigned_integer_value")
    UNSIGNED_INTEGER_VALUE_FIELD_NUMBER: _ClassVar[int]
    STRING_VALUE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_INDEX_FIELD_NUMBER: _ClassVar[int]
    unsigned_integer_value: int
    string_value: str
    object_index: int
    def __init__(
        self,
        unsigned_integer_value: int | None = ...,
        string_value: str | None = ...,
        object_index: int | None = ...,
    ) -> None: ...

class RegisterLatest(_message.Message):
    __slots__ = ("contents",)
    CONTENTS_FIELD_NUMBER: _ClassVar[int]
    contents: ObjectID
    def __init__(self, contents: ObjectID | _Mapping | None = ...) -> None: ...

class MapItem(_message.Message):
    __slots__ = ("key", "value")
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    key: int
    value: ObjectID
    def __init__(
        self,
        key: int | None = ...,
        value: ObjectID | _Mapping | None = ...,
    ) -> None: ...

class AttributeRun(_message.Message):
    __slots__ = (
        "attachment_info",
        "color",
        "emphasis_style",
        "font",
        "font_weight",
        "highlight_color",
        "length",
        "link",
        "paragraph_style",
        "strikethrough",
        "superscript",
        "timestamp",
        "underlined",
        "writing_direction_selection",
    )
    LENGTH_FIELD_NUMBER: _ClassVar[int]
    PARAGRAPH_STYLE_FIELD_NUMBER: _ClassVar[int]
    FONT_FIELD_NUMBER: _ClassVar[int]
    FONT_WEIGHT_FIELD_NUMBER: _ClassVar[int]
    UNDERLINED_FIELD_NUMBER: _ClassVar[int]
    STRIKETHROUGH_FIELD_NUMBER: _ClassVar[int]
    SUPERSCRIPT_FIELD_NUMBER: _ClassVar[int]
    LINK_FIELD_NUMBER: _ClassVar[int]
    COLOR_FIELD_NUMBER: _ClassVar[int]
    WRITING_DIRECTION_SELECTION_FIELD_NUMBER: _ClassVar[int]
    ATTACHMENT_INFO_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    EMPHASIS_STYLE_FIELD_NUMBER: _ClassVar[int]
    HIGHLIGHT_COLOR_FIELD_NUMBER: _ClassVar[int]
    length: int
    paragraph_style: ParagraphStyle
    font: Font
    font_weight: FontWeight
    underlined: int
    strikethrough: int
    superscript: int
    link: str
    color: Color
    writing_direction_selection: WritingDirection
    attachment_info: AttachmentInfo
    timestamp: int
    emphasis_style: int
    highlight_color: Highlight
    def __init__(
        self,
        length: int | None = ...,
        paragraph_style: ParagraphStyle | _Mapping | None = ...,
        font: Font | _Mapping | None = ...,
        font_weight: FontWeight | str | None = ...,
        underlined: int | None = ...,
        strikethrough: int | None = ...,
        superscript: int | None = ...,
        link: str | None = ...,
        color: Color | _Mapping | None = ...,
        writing_direction_selection: WritingDirection | str | None = ...,
        attachment_info: AttachmentInfo | _Mapping | None = ...,
        timestamp: int | None = ...,
        emphasis_style: int | None = ...,
        highlight_color: Highlight | str | None = ...,
    ) -> None: ...

class NoteStoreProto(_message.Message):
    __slots__ = ("document",)
    DOCUMENT_FIELD_NUMBER: _ClassVar[int]
    document: Document
    def __init__(self, document: Document | _Mapping | None = ...) -> None: ...

class Document(_message.Message):
    __slots__ = ("note", "version")
    VERSION_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    version: int
    note: Note
    def __init__(
        self,
        version: int | None = ...,
        note: Note | _Mapping | None = ...,
    ) -> None: ...

class Note(_message.Message):
    __slots__ = ("attribute_run", "note_text")
    NOTE_TEXT_FIELD_NUMBER: _ClassVar[int]
    ATTRIBUTE_RUN_FIELD_NUMBER: _ClassVar[int]
    note_text: str
    attribute_run: _containers.RepeatedCompositeFieldContainer[AttributeRun]
    def __init__(
        self,
        note_text: str | None = ...,
        attribute_run: _Iterable[AttributeRun | _Mapping] | None = ...,
    ) -> None: ...

class MergableDataProto(_message.Message):
    __slots__ = ("mergable_data_object",)
    MERGABLE_DATA_OBJECT_FIELD_NUMBER: _ClassVar[int]
    mergable_data_object: MergableDataObject
    def __init__(
        self,
        mergable_data_object: MergableDataObject | _Mapping | None = ...,
    ) -> None: ...

class MergableDataObject(_message.Message):
    __slots__ = ("mergeable_data_object_data", "version")
    VERSION_FIELD_NUMBER: _ClassVar[int]
    MERGEABLE_DATA_OBJECT_DATA_FIELD_NUMBER: _ClassVar[int]
    version: int
    mergeable_data_object_data: MergeableDataObjectData
    def __init__(
        self,
        version: int | None = ...,
        mergeable_data_object_data: MergeableDataObjectData | _Mapping
        | None = ...,
    ) -> None: ...

class MergeableDataObjectData(_message.Message):
    __slots__ = (
        "mergeable_data_object_entry",
        "mergeable_data_object_key_item",
        "mergeable_data_object_type_item",
        "mergeable_data_object_uuid_item",
    )
    MERGEABLE_DATA_OBJECT_ENTRY_FIELD_NUMBER: _ClassVar[int]
    MERGEABLE_DATA_OBJECT_KEY_ITEM_FIELD_NUMBER: _ClassVar[int]
    MERGEABLE_DATA_OBJECT_TYPE_ITEM_FIELD_NUMBER: _ClassVar[int]
    MERGEABLE_DATA_OBJECT_UUID_ITEM_FIELD_NUMBER: _ClassVar[int]
    mergeable_data_object_entry: _containers.RepeatedCompositeFieldContainer[
        MergeableDataObjectRow
    ]
    mergeable_data_object_key_item: _containers.RepeatedScalarFieldContainer[str]
    mergeable_data_object_type_item: _containers.RepeatedScalarFieldContainer[str]
    mergeable_data_object_uuid_item: _containers.RepeatedScalarFieldContainer[bytes]
    def __init__(
        self,
        mergeable_data_object_entry: _Iterable[MergeableDataObjectRow | _Mapping]
        | None = ...,
        mergeable_data_object_key_item: _Iterable[str] | None = ...,
        mergeable_data_object_type_item: _Iterable[str] | None = ...,
        mergeable_data_object_uuid_item: _Iterable[bytes] | None = ...,
    ) -> None: ...

class MergeableDataObjectRow(_message.Message):
    __slots__ = (
        "custom_map",
        "dictionary",
        "list",
        "note",
        "ordered_set",
        "register_latest",
        "unknown_message",
    )
    REGISTER_LATEST_FIELD_NUMBER: _ClassVar[int]
    LIST_FIELD_NUMBER: _ClassVar[int]
    DICTIONARY_FIELD_NUMBER: _ClassVar[int]
    UNKNOWN_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    CUSTOM_MAP_FIELD_NUMBER: _ClassVar[int]
    ORDERED_SET_FIELD_NUMBER: _ClassVar[int]
    register_latest: RegisterLatest
    list: List
    dictionary: Dictionary
    unknown_message: UnknownMergeableDataObjectEntryMessage
    note: Note
    custom_map: MergeableDataObjectMap
    ordered_set: OrderedSet
    def __init__(
        self,
        register_latest: RegisterLatest | _Mapping | None = ...,
        list: List | _Mapping | None = ...,
        dictionary: Dictionary | _Mapping | None = ...,
        unknown_message: UnknownMergeableDataObjectEntryMessage | _Mapping
        | None = ...,
        note: Note | _Mapping | None = ...,
        custom_map: MergeableDataObjectMap | _Mapping | None = ...,
        ordered_set: OrderedSet | _Mapping | None = ...,
    ) -> None: ...

class UnknownMergeableDataObjectEntryMessage(_message.Message):
    __slots__ = ("unknown_entry",)
    UNKNOWN_ENTRY_FIELD_NUMBER: _ClassVar[int]
    unknown_entry: UnknownMergeableDataObjectEntryMessageEntry
    def __init__(
        self,
        unknown_entry: UnknownMergeableDataObjectEntryMessageEntry | _Mapping
        | None = ...,
    ) -> None: ...

class UnknownMergeableDataObjectEntryMessageEntry(_message.Message):
    __slots__ = ("unknown_int1", "unknown_int2")
    UNKNOWN_INT1_FIELD_NUMBER: _ClassVar[int]
    UNKNOWN_INT2_FIELD_NUMBER: _ClassVar[int]
    unknown_int1: int
    unknown_int2: int
    def __init__(
        self, unknown_int1: int | None = ..., unknown_int2: int | None = ...
    ) -> None: ...

class MergeableDataObjectMap(_message.Message):
    __slots__ = ("map_entry", "type")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    MAP_ENTRY_FIELD_NUMBER: _ClassVar[int]
    type: int
    map_entry: _containers.RepeatedCompositeFieldContainer[MapItem]
    def __init__(
        self,
        type: int | None = ...,
        map_entry: _Iterable[MapItem | _Mapping] | None = ...,
    ) -> None: ...

class OrderedSet(_message.Message):
    __slots__ = ("elements", "ordering")
    ORDERING_FIELD_NUMBER: _ClassVar[int]
    ELEMENTS_FIELD_NUMBER: _ClassVar[int]
    ordering: OrderedSetOrdering
    elements: Dictionary
    def __init__(
        self,
        ordering: OrderedSetOrdering | _Mapping | None = ...,
        elements: Dictionary | _Mapping | None = ...,
    ) -> None: ...

class OrderedSetOrdering(_message.Message):
    __slots__ = ("array", "contents")
    ARRAY_FIELD_NUMBER: _ClassVar[int]
    CONTENTS_FIELD_NUMBER: _ClassVar[int]
    array: OrderedSetOrderingArray
    contents: Dictionary
    def __init__(
        self,
        array: OrderedSetOrderingArray | _Mapping | None = ...,
        contents: Dictionary | _Mapping | None = ...,
    ) -> None: ...

class OrderedSetOrderingArray(_message.Message):
    __slots__ = ("attachment", "contents")
    CONTENTS_FIELD_NUMBER: _ClassVar[int]
    ATTACHMENT_FIELD_NUMBER: _ClassVar[int]
    contents: Note
    attachment: _containers.RepeatedCompositeFieldContainer[
        OrderedSetOrderingArrayAttachment
    ]
    def __init__(
        self,
        contents: Note | _Mapping | None = ...,
        attachment: _Iterable[OrderedSetOrderingArrayAttachment | _Mapping]
        | None = ...,
    ) -> None: ...

class OrderedSetOrderingArrayAttachment(_message.Message):
    __slots__ = ("index", "uuid")
    INDEX_FIELD_NUMBER: _ClassVar[int]
    UUID_FIELD_NUMBER: _ClassVar[int]
    index: int
    uuid: bytes
    def __init__(self, index: int | None = ..., uuid: bytes | None = ...) -> None: ...

class List(_message.Message):
    __slots__ = ("list_entry",)
    LIST_ENTRY_FIELD_NUMBER: _ClassVar[int]
    list_entry: _containers.RepeatedCompositeFieldContainer[ListItem]
    def __init__(
        self, list_entry: _Iterable[ListItem | _Mapping] | None = ...
    ) -> None: ...

class ListItem(_message.Message):
    __slots__ = ("additional_details", "details", "id")
    ID_FIELD_NUMBER: _ClassVar[int]
    DETAILS_FIELD_NUMBER: _ClassVar[int]
    ADDITIONAL_DETAILS_FIELD_NUMBER: _ClassVar[int]
    id: ObjectID
    details: ListEntryDetails
    additional_details: ListEntryDetails
    def __init__(
        self,
        id: ObjectID | _Mapping | None = ...,
        details: ListEntryDetails | _Mapping | None = ...,
        additional_details: ListEntryDetails | _Mapping | None = ...,
    ) -> None: ...

class ListEntryDetails(_message.Message):
    __slots__ = ("id", "list_entry_details_key")
    LIST_ENTRY_DETAILS_KEY_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    list_entry_details_key: ListEntryDetailsKey
    id: ObjectID
    def __init__(
        self,
        list_entry_details_key: ListEntryDetailsKey | _Mapping | None = ...,
        id: ObjectID | _Mapping | None = ...,
    ) -> None: ...

class ListEntryDetailsKey(_message.Message):
    __slots__ = ("list_entry_details_key", "list_entry_details_type_index")
    LIST_ENTRY_DETAILS_TYPE_INDEX_FIELD_NUMBER: _ClassVar[int]
    LIST_ENTRY_DETAILS_KEY_FIELD_NUMBER: _ClassVar[int]
    list_entry_details_type_index: int
    list_entry_details_key: int
    def __init__(
        self,
        list_entry_details_type_index: int | None = ...,
        list_entry_details_key: int | None = ...,
    ) -> None: ...
