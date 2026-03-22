"""
Export/render configuration for Apple Notes HTML output.

Centralizes behavior flags so callers can tune defaults without touching
core logic. All fields are optional at call sites; None means "use current
module defaults and/or environment fallbacks".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Tuple


@dataclass(frozen=True, slots=True)
class ExportConfig:
    # Logging/debug
    debug: bool = False

    # Export policy
    export_mode: Literal["archival", "lightweight"] = "archival"
    assets_dir: Optional[str] = None
    full_page: Optional[bool] = None

    # Image fidelity: when a Media record is present for image attachments,
    # prefer it over preview images. Keep this True for best quality.
    prefer_media_for_images: bool = True

    # Predicate for recognizing image UTIs beyond just the "public.image" prefix.
    # If empty, a reasonable built-in set is used.
    image_uti_prefixes: Tuple[str, ...] = ("public.image",)
    image_uti_exacts: Tuple[str, ...] = (
        "public.jpeg",
        "public.jpg",
        "public.png",
        "public.heic",
        "public.heif",
        "public.tiff",
        "public.gif",
        "public.bmp",
        "public.webp",
        # Apple Notes sketches use this UTI; treat as image-like for downloads
        "com.apple.paper",
    )

    # Appearance hint for preview selection: "light" or "dark".
    preview_appearance: str = "light"

    # Default <object> height for embedded PDFs
    pdf_object_height: int = 600

    # Link behavior
    link_target_blank: bool = True
    link_rel: str = "noopener noreferrer"
    referrer_policy: str = "no-referrer"

    def is_image_uti(self, uti: Optional[str]) -> bool:
        if not uti:
            return False
        u = uti.casefold()

        prefixes = self.image_uti_prefixes or ("public.image",)
        for prefix in prefixes:
            if not isinstance(prefix, str):
                raise TypeError("image_uti_prefixes must contain only strings")
            if u.startswith(prefix.casefold()):
                return True

        exacts = self.image_uti_exacts or ()
        normalized_exacts = []
        for exact in exacts:
            if not isinstance(exact, str):
                raise TypeError("image_uti_exacts must contain only strings")
            normalized_exacts.append(exact.casefold())

        return u in tuple(normalized_exacts)
