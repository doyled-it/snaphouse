// The volume slider's orientation rules, pure (no DOM). A horizontal track reads
// the pointer's x (left is 0); a vertical track reads its y (the top is 100, the
// bottom 0), as the desktop mixer faders do.

import { keyStep, valueFromPointer, valueFromPointerVertical } from "./volume-controller.js";

export const HORIZONTAL = "horizontal";
export const VERTICAL = "vertical";

// `point` is { x, y } in client pixels; `rect` is the track's bounding box.
export function pointerValue(orientation, point, rect) {
  if (orientation === VERTICAL) {
    return valueFromPointerVertical(point.y, rect.top, rect.height);
  }
  return valueFromPointer(point.x, rect.left, rect.width);
}

// Up/Down and Page Up/Down step the same way in either orientation.
export function keyStepFor(key, _orientation) {
  return keyStep(key);
}
