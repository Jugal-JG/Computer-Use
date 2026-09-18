"""Pixel-only perception. No browser or target application dependencies."""

import io
import os
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image

from .contracts import Target

candidate = os.getenv("TESSERACT_CMD") or r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if Path(candidate).exists():
    pytesseract.pytesseract.tesseract_cmd = candidate


class Unresolved(Exception):
    """A safe, caller-visible reason why perception refused to choose."""

    def __init__(self, code, detail=None):
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


def normalize(s):
    return re.sub(r"[^a-z0-9*]+", " ", s.lower()).strip()


@dataclass
class Box:
    text: str
    x: int
    y: int
    w: int
    h: int
    confidence: float = 100

    @property
    def center(self):
        return (self.x + self.w / 2, self.y + self.h / 2)


class View:
    def __init__(self, png: bytes):
        self.png = png
        self.image = Image.open(io.BytesIO(png)).convert("RGB")
        self.width, self.height = self.image.size
        data = pytesseract.image_to_data(
            self.image, config="--psm 11", output_type=pytesseract.Output.DICT
        )
        self.words = []
        for i, t in enumerate(data["text"]):
            if t.strip() and float(data["conf"][i]) >= 45:
                self.words.append(
                    Box(
                        t,
                        data["left"][i],
                        data["top"][i],
                        data["width"][i],
                        data["height"][i],
                        float(data["conf"][i]),
                    )
                )
        # Sparse OCR can miss white text on dark controls. Detect dark rectangular
        # regions from pixels, invert each crop, and OCR at double resolution.
        gray = cv2.cvtColor(np.array(self.image), cv2.COLOR_RGB2GRAY)
        # Foreground-layer detection is scale independent. A dimmed page with a
        # substantial enclosed panel is a modal; unexplained heavy dimming is an
        # occlusion and is also unsafe to act through.
        dark_ratio = float(np.mean(gray < 110))
        # A full-width dark masthead is ordinary page chrome. On short
        # viewports its area alone must not turn a white form into a modal.
        dark_rows = np.mean(gray < 110, axis=1)
        band = 0
        while band < self.height and dark_rows[band] > 0.85:
            band += 1
        if 0 < band < self.height * 0.5 and np.mean(gray[band:] > 175) > 0.65:
            dark_ratio = float(np.mean(gray[band:] < 110))
        bright, _ = cv2.findContours(
            cv2.inRange(gray, 175, 255), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        panels = []
        image_area = self.width * self.height
        for contour in bright:
            x, y, w, h = cv2.boundingRect(contour)
            area_ratio = (w * h) / image_area
            contour_area = cv2.contourArea(contour)
            centered = (
                0.08 < (x + w / 2) / self.width < 0.92 and 0.06 < (y + h / 2) / self.height < 0.94
            )
            if 0.02 <= area_ratio <= 0.94 and centered and contour_area / (w * h) > 0.65:
                panels.append(Box("foreground", x, y, w, h, 100))
        self.foreground = (
            max(panels, key=lambda b: b.w * b.h) if dark_ratio > 0.30 and panels else None
        )
        self.modal = self.foreground is not None
        self.occluded = dark_ratio > 0.38 and self.foreground is None
        self.base_words = tuple(self.words)
        self.dark_ocr_done = False
        self.gray = gray

    def _augment_dark_controls(self):
        if self.dark_ocr_done:
            return
        self.dark_ocr_done = True
        gray = self.gray
        contours, _ = cv2.findContours(
            cv2.inRange(gray, 0, 140), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
        )
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            # A button may become narrow/tall when a responsive layout wraps.
            # Keep only broad, screen-relative shape limits; there is no fixed
            # pixel button size here.
            width_ratio, height_ratio = w / self.width, h / self.height
            if not (0.025 <= width_ratio <= 0.98 and 0.018 <= height_ratio <= 0.30):
                continue
            crop = gray[y + 3 : y + h - 3, x + 3 : x + w - 3]
            if not crop.size or np.mean(crop) > 170:
                continue
            prepared = cv2.resize(255 - crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            d = pytesseract.image_to_data(
                prepared, config="--psm 7", output_type=pytesseract.Output.DICT
            )
            for i, t in enumerate(d["text"]):
                if not t.strip() or float(d["conf"][i]) < 60:
                    continue
                box = Box(
                    t,
                    x + 3 + d["left"][i] // 2,
                    y + 3 + d["top"][i] // 2,
                    d["width"][i] // 2,
                    d["height"][i] // 2,
                    float(d["conf"][i]),
                )
                if not any(
                    normalize(z.text) == normalize(t)
                    and abs(z.x - box.x) < 8
                    and abs(z.y - box.y) < 8
                    for z in self.words
                ):
                    self.words.append(box)

        # Some controls have no separable contour (for example a flat dark
        # button merged with its container).  A full inverted sparse pass is
        # slower, so it is deliberately deferred until a text target is being
        # resolved, never performed for ordinary checkpoint reads.
        inverted = cv2.resize(255 - gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        data = pytesseract.image_to_data(
            inverted, config="--psm 11", output_type=pytesseract.Output.DICT
        )
        for i, t in enumerate(data["text"]):
            if not t.strip() or float(data["conf"][i]) < 60:
                continue
            box = Box(
                t,
                data["left"][i] // 2,
                data["top"][i] // 2,
                max(1, data["width"][i] // 2),
                max(1, data["height"][i] // 2),
                float(data["conf"][i]),
            )
            if not any(
                normalize(z.text) == normalize(t) and abs(z.x - box.x) < 8 and abs(z.y - box.y) < 8
                for z in self.words
            ):
                self.words.append(box)

    def _pixel_region(self, region):
        region = region or (0.0, 0.0, 1.0, 1.0)
        return (
            region[0] * self.width,
            region[1] * self.height,
            region[2] * self.width,
            region[3] * self.height,
        )

    def _in_foreground(self, box):
        if not self.foreground:
            return True
        f = self.foreground
        return f.x <= box.center[0] <= f.x + f.w and f.y <= box.center[1] <= f.y + f.h

    def find(self, text, region=None, threshold=70):
        wanted = normalize(text).split()
        region = self._pixel_region(region)
        words = [
            w
            for w in self.words
            if region[0] <= w.center[0] <= region[2]
            and region[1] <= w.center[1] <= region[3]
            and self._in_foreground(w)
        ]
        matches = []
        # OCR reading order can split rows across columns. Match spatially, not by global string.
        for first in words:
            if not wanted or normalize(first.text) != wanted[0]:
                continue
            chain = [first]
            for token in wanted[1:]:
                prev = chain[-1]
                candidates = [
                    w
                    for w in words
                    if normalize(w.text) == token
                    and 0 <= w.x - (prev.x + prev.w) < 48
                    and abs(w.center[1] - prev.center[1]) < 12
                ]
                if not candidates:
                    break
                chain.append(min(candidates, key=lambda w: w.x))
            if len(chain) == len(wanted) and min(w.confidence for w in chain) >= threshold:
                x = min(w.x for w in chain)
                y = min(w.y for w in chain)
                matches.append(
                    Box(
                        text,
                        x,
                        y,
                        max(w.x + w.w for w in chain) - x,
                        max(w.y + w.h for w in chain) - y,
                        min(w.confidence for w in chain),
                    )
                )
        return matches

    def has(self, text):
        return bool(self.find(text))

    def _geometry_score(self, box, hint):
        if not hint:
            return 0.0
        hx, hy, hw, hh = hint
        cx, cy = box.center[0] / self.width, box.center[1] / self.height
        expected_x, expected_y = hx + hw / 2, hy + hh / 2
        distance = ((cx - expected_x) ** 2 + (cy - expected_y) ** 2) ** 0.5
        return max(0.0, 1.0 - distance / 0.35)

    def _relationship_score(self, box, anchor, relationships, gap_scale=0.6):
        if not relationships:
            return 0.0
        dx = (box.center[0] - anchor.center[0]) / self.width
        dy = (box.center[1] - anchor.center[1]) / self.height
        scores = []
        if "left_of" in relationships:
            scores.append(self._relationship_score(anchor, box, ("right_of",), gap_scale))
        if "above" in relationships:
            scores.append(self._relationship_score(anchor, box, ("below",), gap_scale))
        if "right_of" in relationships and box.x >= anchor.x + anchor.w - 3:
            scores.append(max(0.0, 1.0 - abs(dy) / 0.10 - max(0.0, dx - 0.45)))
        if "below" in relationships and box.y >= anchor.y + anchor.h - 3:
            horizontal_gap = 0.0
            if anchor.center[0] < box.x:
                horizontal_gap = (box.x - anchor.center[0]) / self.width
            elif anchor.center[0] > box.x + box.w:
                horizontal_gap = (anchor.center[0] - box.x - box.w) / self.width
            vertical_gap = max(0, box.y - (anchor.y + anchor.h)) / self.height
            below_score = max(0.0, 1.0 - horizontal_gap / 0.25 - vertical_gap / gap_scale)
            scores.append(below_score)
        if "same_row" in relationships:
            scores.append(max(0.0, 1.0 - abs(dy) / 0.08))
        if "same_column" in relationships:
            scores.append(max(0.0, 1.0 - abs(dx) / 0.12))
        if "inside_container" in relationships:
            scores.append(0.5)
        return max(scores, default=0.0)

    def _choose(self, candidates, target, anchor=None):
        if not candidates:
            raise Unresolved("TARGET_NOT_FOUND")
        ranked = []
        for box, visual_score in candidates:
            score = 0.65 * visual_score + 0.20 * self._geometry_score(box, target.geometry_hint)
            if anchor:
                score += 0.15 * self._relationship_score(
                    box, anchor, target.relationships, 0.15 if target.kind == "field" else 0.6
                )
            if target.kind == "field" and target.geometry_hint is None:
                score /= 0.8
            ranked.append((score, box))
        ranked.sort(key=lambda item: item[0], reverse=True)
        best = ranked[0]
        if best[0] < target.min_confidence:
            raise Unresolved("TARGET_LOW_CONFIDENCE", round(best[0], 3))
        if len(ranked) > 1 and best[0] - ranked[1][0] < target.winner_margin:
            raise Unresolved("TARGET_AMBIGUOUS", len(candidates))
        return best[1]

    def _field_rectangles(self):
        if hasattr(self, "field_rectangles"):
            return self.field_rectangles
        gray = cv2.cvtColor(np.array(self.image), cv2.COLOR_RGB2GRAY)
        masks = [cv2.Canny(gray, 35, 120), cv2.inRange(gray, 0, 185)]
        rects = []
        for mask in masks:
            contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                wr, hr = w / self.width, h / self.height
                if not (0.04 <= wr <= 0.90 and 0.018 <= hr <= 0.45 and w / max(h, 1) >= 1.35):
                    continue
                interior = gray[y + 4 : y + h - 4, x + 4 : x + w - 4]
                # This resolver supports outlined, light writing areas. Filled
                # dark buttons are not fields, even when a proposed relationship
                # places one near a label. Unsupported themes fail closed.
                if not interior.size or np.mean(interior > 190) < 0.75:
                    continue
                box = Box("field", x, y, w, h)
                if not self._in_foreground(box):
                    continue
                duplicate = None
                for index, old in enumerate(rects):
                    ix1, iy1 = max(x, old.x), max(y, old.y)
                    ix2, iy2 = min(x + w, old.x + old.w), min(y + h, old.y + old.h)
                    intersection = max(0, ix2 - ix1) * max(0, iy2 - iy1)
                    union = w * h + old.w * old.h - intersection
                    if (
                        abs(x - old.x) < 7
                        and abs(y - old.y) < 7
                        or union
                        and intersection / union > 0.55
                    ):
                        duplicate = index
                        break
                if duplicate is not None:
                    old = rects[duplicate]
                    if w / h > old.w / old.h:
                        rects[duplicate] = box
                    continue
                rects.append(box)
        self.field_rectangles = rects
        return rects

    def resolve(self, target: Target):
        matches = self.find(target.text, target.region)
        if (target.kind == "text" or not matches) and not getattr(self, "dark_ocr_done", True):
            self._augment_dark_controls()
            matches = self.find(target.text, target.region)
        if not matches:
            raise Unresolved("TARGET_NOT_FOUND")
        if target.kind == "text":
            relationship_anchor = None
            if target.anchor_text:
                anchors = self.find(target.anchor_text)
                if len(anchors) != 1:
                    raise Unresolved("TARGET_AMBIGUOUS" if anchors else "TARGET_NOT_FOUND")
                relationship_anchor = anchors[0]
                matches = [
                    box
                    for box in matches
                    if self._relationship_score(box, relationship_anchor, target.relationships) > 0
                ]
            return self._choose(
                [(box, box.confidence / 100) for box in matches], target, relationship_anchor
            )

        if len(matches) != 1:
            raise Unresolved("TARGET_AMBIGUOUS", len(matches))
        anchor = matches[0]
        if target.kind == "value":
            return self._resolve_value(target, anchor)
        # Responsive forms commonly move a label from beside its field to above
        # it. Treat the learned direction as a hint, not a fixed desktop layout.
        # Only current, closely adjacent writing areas qualify for this pairing.
        if set(target.relationships) & {"right_of", "below", "same_row"}:
            target = target.model_copy(
                update={
                    "relationships": tuple(
                        dict.fromkeys((*target.relationships, "right_of", "below"))
                    ),
                    "geometry_hint": None,
                }
            )
        candidates = []
        for box in self._field_rectangles():
            # Labels and enclosing panels can also produce contours. A writing
            # area must not overlap its own label.
            if min(box.x + box.w, anchor.x + anchor.w) > max(box.x, anchor.x) and min(
                box.y + box.h, anchor.y + anchor.h
            ) > max(box.y, anchor.y):
                continue
            relation = self._relationship_score(box, anchor, target.relationships, 0.15)
            if relation <= 0:
                continue
            if set(target.relationships) <= {"right_of", "below", "same_row"}:
                beside = (
                    box.x >= anchor.x + anchor.w - 3
                    and abs(box.center[1] - anchor.center[1]) <= max(anchor.h, box.h) / 2
                )
                underneath = (
                    box.y >= anchor.y + anchor.h - 3
                    and box.y - anchor.y - anchor.h <= max(24, anchor.h * 1.5)
                    and box.x - anchor.h <= anchor.center[0] <= box.x + box.w + anchor.h
                )
                if not (beside or underneath):
                    continue
            crop = np.array(self.image)[box.y : box.y + box.h, box.x : box.x + box.w]
            uniformity = 1.0 - min(1.0, float(np.std(crop)) / 100) if crop.size else 0.0
            visual = min(1.0, 0.85 * relation + 0.15 * uniformity)
            candidates.append((box, visual))

        # Never turn stored geometry into a clickable field without pixel evidence.
        return self._choose(candidates, target, anchor)

    def _resolve_value(self, target, anchor):
        """Find a neighbouring text region without persisting its contents."""
        words = [
            w
            for w in self.words
            if self._in_foreground(w)
            and not (
                min(w.x + w.w, anchor.x + anchor.w) > max(w.x, anchor.x)
                and min(w.y + w.h, anchor.y + anchor.h) > max(w.y, anchor.y)
            )
        ]
        lines = []
        for word in sorted(words, key=lambda w: (w.center[1], w.x)):
            row = next((r for r in lines if abs(r[0].center[1] - word.center[1]) < 10), None)
            if row is None:
                lines.append([word])
            else:
                row.append(word)
        regions = []
        for row in lines:
            groups = []
            for word in sorted(row, key=lambda w: w.x):
                if groups and word.x - (groups[-1][-1].x + groups[-1][-1].w) < 40:
                    groups[-1].append(word)
                else:
                    groups.append([word])
            for group in groups:
                x, y = min(w.x for w in group), min(w.y for w in group)
                box = Box(
                    "value",
                    x,
                    y,
                    max(w.x + w.w for w in group) - x,
                    max(w.y + w.h for w in group) - y,
                    min(w.confidence for w in group),
                )
                regions.append(box)
        # Sparse OCR sometimes misses an entire masked account. Locate text ink
        # independently of recognition so unreadable content can still be cropped.
        gray = cv2.cvtColor(np.array(self.image), cv2.COLOR_RGB2GRAY)
        ink = cv2.inRange(gray, 0, 160)
        for kernel in ((max(35, self.width // 15), 1), (1, max(30, self.height // 15))):
            lines_mask = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones(kernel[::-1], np.uint8))
            ink = cv2.subtract(ink, lines_mask)
        joined = cv2.dilate(ink, np.ones((3, 11), np.uint8))
        contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            box = Box("value", max(0, x + 5), max(0, y + 1), max(1, w - 10), max(1, h - 2))
            if not (7 <= h <= max(40, anchor.h * 2) and w >= 12 and self._in_foreground(box)):
                continue
            if min(box.x + box.w, anchor.x + anchor.w) > max(box.x, anchor.x) and min(
                box.y + box.h, anchor.y + anchor.h
            ) > max(box.y, anchor.y):
                continue
            if any(
                abs(box.center[1] - r.center[1]) < 10
                and min(box.x + box.w, r.x + r.w) > max(box.x, r.x)
                for r in regions
            ):
                continue
            regions.append(box)
        candidates = []
        for box in regions:
            relation = self._relationship_score(box, anchor, target.relationships, 0.15)
            if relation <= 0:
                continue
            dx = max(0, box.x - anchor.x - anchor.w, anchor.x - box.x - box.w)
            dy = max(0, box.y - anchor.y - anchor.h, anchor.y - box.y - box.h)
            proximity = max(0.0, 1 - dx / self.width - dy / self.height)
            candidates.append((box, 0.8 * relation + 0.2 * proximity))
        return self._choose(candidates, target, anchor)

    def read_box(self, box, *, inset=0, masked=False):
        """OCR one already-resolved box, cached only within this screenshot."""
        if not hasattr(self, "read_values"):
            self.read_values = {}
        key = (box.x, box.y, box.w, box.h, inset, masked)
        if key in self.read_values:
            return self.read_values[key]
        left, top = max(0, box.x + inset), max(0, box.y + inset)
        right = min(self.width, box.x + box.w - inset)
        bottom = min(self.height, box.y + box.h - inset)
        if right <= left or bottom <= top:
            raise Unresolved("VALUE_REGION_INVALID")
        crop = self.image.crop((left, top, right, bottom))
        config = "--psm 7" + (" -c tessedit_char_whitelist=*0123456789" if masked else "")
        for scale in (1, 2, 3) if masked else (2,):
            image = crop.resize((crop.width * scale, crop.height * scale))
            value = pytesseract.image_to_string(image, config=config).strip()
            if masked:
                value = value.replace(" ", "")
                if not re.fullmatch(r"\*{2,6}\d{4}", value):
                    continue
            self.read_values[key] = value
            return value
        raise Unresolved("OUTPUT_UNREADABLE")

    def read_value(self, target, *, masked=False):
        box = self.resolve(target)
        return self.read_box(box, inset=5 if target.kind == "field" else -4, masked=masked)

    def read_field(self, target):
        # Re-resolve on this screenshot: scrolling and responsive layout changes
        # invalidate coordinates from the preceding action.
        return self.read_value(target)
