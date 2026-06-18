// Pull a vivid accent color from album art and set it as --accent, matching the
// original app's behavior. Falls back silently if the canvas is tainted.
export function applyAccentFromUrl(url) {
  if (!url) return;
  const img = new Image();
  img.crossOrigin = "anonymous";
  img.onload = () => {
    try {
      const n = 16;
      const c = document.createElement("canvas");
      c.width = n;
      c.height = n;
      const ctx = c.getContext("2d");
      ctx.drawImage(img, 0, 0, n, n);
      const data = ctx.getImageData(0, 0, n, n).data;
      let best = null;
      let bestScore = -1;
      for (let i = 0; i < data.length; i += 4) {
        const r = data[i];
        const g = data[i + 1];
        const b = data[i + 2];
        const max = Math.max(r, g, b);
        const min = Math.min(r, g, b);
        const sat = max === 0 ? 0 : (max - min) / max;
        const score = sat * (max / 255);
        if (max > 40 && score > bestScore) {
          bestScore = score;
          best = [r, g, b];
        }
      }
      if (best) {
        document.documentElement.style.setProperty(
          "--accent",
          `rgb(${best[0]},${best[1]},${best[2]})`
        );
      }
    } catch {
      /* tainted canvas — keep default accent */
    }
  };
  img.src = url;
}
