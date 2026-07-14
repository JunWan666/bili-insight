from __future__ import annotations

import xml.etree.ElementTree as ET

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

MAX_DANMAKU_BYTES = 8 * 1024 * 1024
MAX_DANMAKU_ENTRIES = 100_000
MAX_XML_ELEMENTS = 120_000
MAX_ATTRIBUTES_PER_ELEMENT = 16
MAX_ATTRIBUTE_NAME_LENGTH = 128
MAX_ATTRIBUTE_VALUE_LENGTH = 2_048
MAX_TEXT_LENGTH = 20_000


class DanmakuValidationError(ValueError):
    """The danmaku document is malformed or exceeds a bounded safety limit."""


def validate_danmaku_xml(payload: bytes) -> None:
    if not payload or len(payload) > MAX_DANMAKU_BYTES:
        raise DanmakuValidationError("Danmaku XML size is outside the safe range")
    lowered = payload.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise DanmakuValidationError("Danmaku XML declarations are not allowed")
    try:
        root = DefusedET.fromstring(
            payload,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except (ET.ParseError, DefusedXmlException, ValueError) as exc:
        raise DanmakuValidationError("Danmaku XML is malformed") from exc
    if root.tag != "i":
        raise DanmakuValidationError("Danmaku XML has an unexpected root element")

    element_count = 0
    danmaku_count = 0
    for element in root.iter():
        element_count += 1
        if element_count > MAX_XML_ELEMENTS:
            raise DanmakuValidationError("Danmaku XML contains too many elements")
        if len(element.attrib) > MAX_ATTRIBUTES_PER_ELEMENT:
            raise DanmakuValidationError("Danmaku XML contains too many attributes")
        for name, value in element.attrib.items():
            if len(name) > MAX_ATTRIBUTE_NAME_LENGTH or len(value) > MAX_ATTRIBUTE_VALUE_LENGTH:
                raise DanmakuValidationError("Danmaku XML attribute exceeds the safe limit")
        if len(element.text or "") > MAX_TEXT_LENGTH or len(element.tail or "") > MAX_TEXT_LENGTH:
            raise DanmakuValidationError("Danmaku XML text exceeds the safe limit")
        if element.tag == "d":
            danmaku_count += 1
            if danmaku_count > MAX_DANMAKU_ENTRIES:
                raise DanmakuValidationError("Danmaku XML contains too many entries")
            if "p" not in element.attrib:
                raise DanmakuValidationError("Danmaku XML entry is missing its parameters")
