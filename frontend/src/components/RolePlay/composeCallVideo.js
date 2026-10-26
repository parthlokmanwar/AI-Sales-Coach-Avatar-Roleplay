/*
 * composeCallVideo — paint the avatar and the rep's camera into ONE video
 * track, so the downloaded recording shows both sides of the call.
 *
 * Recording the avatar's MediaStreamTrack directly (what we did first) captures
 * only the avatar; the rep's self-view is a separate track and never lands in
 * the file. A canvas is the standard way to merge them: draw both <video>
 * elements every frame, then captureStream() the canvas as a normal video track
 * that MediaRecorder accepts — on desktop AND mobile.
 *
 * Note this is only possible because we left the iframe behind. Drawing a
 * cross-origin video to a canvas taints it and captureStream() then throws;
 * these elements play same-origin MediaStreams, so the canvas stays clean.
 */

const BG = "#0a1628";

/** Fit `src` inside `dst` preserving aspect (letterbox), like object-fit: contain. */
function containRect(sw, sh, dw, dh) {
  if (!sw || !sh) return { x: 0, y: 0, w: dw, h: dh };
  const scale = Math.min(dw / sw, dh / sh);
  const w = sw * scale, h = sh * scale;
  return { x: (dw - w) / 2, y: (dh - h) / 2, w, h };
}

/** Crop `src` to fill `dst` preserving aspect, like object-fit: cover. */
function coverRect(sw, sh, dw, dh) {
  if (!sw || !sh) return { sx: 0, sy: 0, sw: 1, sh: 1 };
  const scale = Math.max(dw / sw, dh / sh);
  const w = dw / scale, h = dh / scale;
  return { sx: (sw - w) / 2, sy: (sh - h) / 2, sw: w, sh: h };
}

function roundedPath(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y,     x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x,     y + h, r);
  ctx.arcTo(x,     y + h, x,     y,     r);
  ctx.arcTo(x,     y,     x + w, y,     r);
  ctx.closePath();
}

/**
 * @param remoteEl <video> playing the avatar
 * @param localEl  <video> playing the rep's camera (may be absent/off)
 * @returns { track, stop, width, height } — or null if nothing can be drawn
 */
export function composeCallVideo(remoteEl, localEl, { fps = 30 } = {}) {
  if (!remoteEl || typeof document === "undefined") return null;

  // Match the avatar's aspect where known so the letterbox stays minimal.
  const rw = remoteEl.videoWidth || 1280;
  const rh = remoteEl.videoHeight || 720;
  const height = 720;
  const width = Math.round(height * Math.max(1, rw / Math.max(1, rh)));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(320, Math.min(1920, width % 2 ? width + 1 : width));
  canvas.height = height;

  const ctx = canvas.getContext("2d", { alpha: false });
  if (!ctx || !canvas.captureStream) return null;

  const hasFrames = (el) => el && el.videoWidth > 0 && el.videoHeight > 0 && !el.paused;

  let raf = 0;
  let stopped = false;
  const draw = () => {
    if (stopped) return;
    const W = canvas.width, H = canvas.height;
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, W, H);

    if (hasFrames(remoteEl)) {
      const r = containRect(remoteEl.videoWidth, remoteEl.videoHeight, W, H);
      ctx.drawImage(remoteEl, r.x, r.y, r.w, r.h);
    }

    // Rep's camera, picture-in-picture bottom-right — mirrors the on-screen
    // layout so the recording matches what they saw. Skipped when the camera
    // is off, rather than drawing a black box.
    if (hasFrames(localEl)) {
      const pw = Math.round(W * 0.22);
      const ph = Math.round(pw * 3 / 4);
      const px = W - pw - Math.round(W * 0.02);
      const py = H - ph - Math.round(W * 0.02);
      const c = coverRect(localEl.videoWidth, localEl.videoHeight, pw, ph);
      ctx.save();
      roundedPath(ctx, px, py, pw, ph, Math.round(pw * 0.06));
      ctx.clip();
      ctx.fillStyle = BG;
      ctx.fillRect(px, py, pw, ph);
      ctx.drawImage(localEl, c.sx, c.sy, c.sw, c.sh, px, py, pw, ph);
      ctx.restore();
      ctx.strokeStyle = "rgba(255,255,255,0.28)";
      ctx.lineWidth = 2;
      roundedPath(ctx, px, py, pw, ph, Math.round(pw * 0.06));
      ctx.stroke();
    }

    raf = requestAnimationFrame(draw);
  };
  draw();

  const stream = canvas.captureStream(fps);
  const track = stream.getVideoTracks()[0];
  if (!track) { stopped = true; cancelAnimationFrame(raf); return null; }

  return {
    track,
    width: canvas.width,
    height: canvas.height,
    stop() {
      stopped = true;
      cancelAnimationFrame(raf);
      try { track.stop(); } catch { /* noop */ }
    },
  };
}
