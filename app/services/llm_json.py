"""Parse JSON do LLM trả về một cách "khoan dung" (dùng chung cho Cảnh báo Cổ phiếu & Crypto).

Vì sao cần: model có thể bọc JSON trong ```json fence, thêm chữ giải thích, hoặc
**dừng giữa chừng** khi chạm trần `max_tokens` → JSON bị cụt (thiếu `]`/`}`).
Trường hợp bị cụt trước đây làm `json.loads` ném lỗi kiểu
`Expecting ',' delimiter: line N column M` tại ĐÚNG cuối chuỗi và toàn bộ lần quét thất bại.

Module này: cắt phần vỏ → thử parse → sửa lỗi phổ biến (dấu phẩy thừa) → nếu vẫn lỗi thì
tìm "điểm cắt an toàn" gần nhất (kết thúc một phần tử hoàn chỉnh) rồi đóng lại các
`{`/`[` còn mở, nhờ đó vẫn giữ được các tín hiệu đã sinh xong thay vì mất hết.
"""
from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
# Bỏ mảnh dở cuối cùng: `"key":` chưa có value, rồi tới dấu `,` / `:` treo lơ lửng.
_DANGLING_KEY_RE = re.compile(r'"[^"\\]*"\s*:\s*$')
_DANGLING_SEP_RE = re.compile(r"[,:]\s*$")
# String đứng trơ sau `{` hoặc `,` (model dừng ngay sau tên key, chưa kịp ghi value).
_DANGLING_BARE_STR_RE = re.compile(r"([,{])\s*\"[^\"\\\\]*\"\s*$")
_MAX_REPAIR_ATTEMPTS = 40


def extract_json_object(text: str) -> str:
    """Bỏ ``` fence + chữ thừa quanh JSON, trả về chuỗi từ `{` đầu tới `}` cuối."""
    text = (text or "").strip()
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _scan(text: str) -> tuple[list[str], list[int], int]:
    """Quét text (có thể chưa hợp lệ).

    Trả về (các ký tự đóng cần thêm, các điểm cắt an toàn, vị trí `"` mở string còn dở).
    "Điểm cắt an toàn" = vị trí NGAY SAU khi một string/object/array vừa kết thúc, tức chỗ
    có thể cắt bỏ phần dở phía sau mà vẫn ghép được JSON hợp lệ.
    """
    stack: list[str] = []
    cuts: list[int] = []
    in_str = False
    esc = False
    str_start = -1
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
                cuts.append(i + 1)
            continue
        if ch == '"':
            in_str = True
            str_start = i
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack:
                stack.pop()
            cuts.append(i + 1)
    return list(reversed(stack)), cuts, (str_start if in_str else -1)


def _close(prefix: str) -> str:
    """Đóng các `{`/`[` còn mở ở cuối prefix."""
    closers, _, _ = _scan(prefix)
    return prefix + "".join(closers)


def _strip_dangling(prefix: str) -> str:
    """Bỏ mảnh dở ở cuối (vd: `{"a":[{"t":` → `{"a":[{`)."""
    out = prefix
    for _ in range(3):
        new = _DANGLING_KEY_RE.sub("", out)
        new = _DANGLING_BARE_STR_RE.sub(r"\1", new)
        new = _DANGLING_SEP_RE.sub("", new)
        if new == out:
            break
        out = new
    return out


def _candidates(text: str, str_start: int):
    """Sinh dần các ứng viên parse được, từ "ít mất mát" tới "mất nhiều hơn"."""
    _, cuts, _ = _scan(text)
    prefixes: list[str] = [text]
    if str_start >= 0:
        prefixes.append(text[:str_start])
    prefixes.extend(text[:cut] for cut in reversed(cuts[-_MAX_REPAIR_ATTEMPTS:]))

    seen: set[str] = set()
    for prefix in prefixes:
        for candidate in (_close(prefix), _close(_strip_dangling(prefix))):
            if candidate and candidate not in seen:
                seen.add(candidate)
                yield candidate


def parse_llm_json(text: str) -> dict:
    """Parse JSON từ LLM; tự sửa dấu phẩy thừa và JSON bị cắt cụt.

    Raise ValueError nếu không cứu được — thông báo ngắn gọn để hiển thị cho người dùng.
    """
    raw = extract_json_object(text)

    for candidate in (raw, _TRAILING_COMMA_RE.sub(r"\1", raw)):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value

    _, _, str_start = _scan(raw)
    for candidate in _candidates(raw, str_start):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value

    raise ValueError("AI trả về JSON không hợp lệ và không thể khôi phục.")
