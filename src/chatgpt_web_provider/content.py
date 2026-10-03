"""Parse text and inline image content without ever stringifying binary data."""
from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_IMAGES = 10
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}


@dataclass(frozen=True)
class ImageAttachment:
    name: str
    mime: str
    data: bytes

    def payload(self) -> dict:
        return {"name": self.name, "mimeType": self.mime, "buffer": self.data}


def decode_image(url: str) -> ImageAttachment:
    if not isinstance(url, str) or not url.startswith("data:"):
        raise ValueError("Images must use a base64 data URL in image_url.url; remote URLs and file paths are not supported.")
    header, separator, encoded = url.partition(",")
    mime = header[5:].split(";", 1)[0].lower()
    if not separator or not header.lower().endswith(";base64") or mime not in IMAGE_TYPES:
        raise ValueError("Supported image types: PNG, JPEG, WebP and GIF, supplied as base64 data URLs.")
    if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError("Each image must be at most 20 MiB.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid base64 image data.") from None
    signatures = {
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }
    if not data or len(data) > MAX_IMAGE_BYTES or not signatures[mime]:
        raise ValueError("Image bytes do not match the declared image type or exceed the size limit.")
    return ImageAttachment("image-" + hashlib.sha256(data).hexdigest()[:16] + IMAGE_TYPES[mime], mime, data)


def parse_content(content) -> tuple[str, list[ImageAttachment]]:
    if content is None:
        return "", []
    if isinstance(content, str):
        if content.startswith("data:image/"):
            raise ValueError("Place image data in an image_url content block, not in a text string.")
        return content, []
    blocks = content if isinstance(content, list) else [content]
    texts, images = [], []
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError("Each message content part must be a typed object.")
        kind = block.get("type")
        if kind in {"text", "input_text", "output_text"}:
            text = block.get("text")
            if not isinstance(text, str):
                raise ValueError("Text content parts require a string text field.")
            texts.append(text)
        elif kind in {"image_url", "input_image"}:
            image = block.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            images.append(decode_image(url))
        else:
            raise ValueError("Unsupported content part. This bridge supports text and inline image_url images; document and file_id uploads are not implemented.")
    if len(images) > MAX_IMAGES or sum(len(image.data) for image in images) > MAX_REQUEST_BYTES:
        raise ValueError("An image request supports at most 10 images and 32 MiB total.")
    return "\n".join(texts), images
