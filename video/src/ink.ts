// Where the text of a captured screen is. A page card is mostly white, so its dark pixels (text, buttons) tell where to look;
// the crop frames them with a margin and the picture itself is never changed.

import type { Region } from "./data.ts";

const STEP = 4;
const DARK = 150;
const PAD = 56;

/** The padded bounding box of the dark pixels of an image (browser canvas), in the image's own pixels. */
export function inkRegion(bitmap: ImageBitmap): Region {
  const w = Math.ceil(bitmap.width / STEP);
  const h = Math.ceil(bitmap.height / STEP);
  const canvas = new OffscreenCanvas(w, h);
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (ctx === null) return { x: 0, y: 0, w: bitmap.width, h: bitmap.height };
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, w, h);
  ctx.drawImage(bitmap, 0, 0, w, h);
  const { data } = ctx.getImageData(0, 0, w, h);
  let x0 = w;
  let y0 = h;
  let x1 = -1;
  let y1 = -1;
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const i = (y * w + x) * 4;
      const lum = 0.299 * (data[i] ?? 255) + 0.587 * (data[i + 1] ?? 255) + 0.114 * (data[i + 2] ?? 255);
      if (lum < DARK) {
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
    }
  }
  if (x1 < 0) return { x: 0, y: 0, w: bitmap.width, h: bitmap.height };
  const x = Math.max(0, x0 * STEP - PAD);
  const y = Math.max(0, y0 * STEP - PAD);
  const right = Math.min(bitmap.width, (x1 + 1) * STEP + PAD);
  const bottom = Math.min(bitmap.height, (y1 + 1) * STEP + PAD);
  return { x, y, w: right - x, h: bottom - y };
}
