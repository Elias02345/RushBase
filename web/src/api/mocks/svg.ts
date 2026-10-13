/** Procedural placeholder imagery for the mock layer: calm landscapes seeded by the asset id. */

function hash(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function scene(seed: number, progress: number, width: number, height: number): string {
  const hue = seed % 360;
  const sky = `hsl(${hue} 55% ${62 + (seed % 12)}%)`;
  const sky2 = `hsl(${(hue + 40) % 360} 60% ${78 - (seed % 8)}%)`;
  const hills = `hsl(${(hue + 160) % 360} 32% ${30 + (seed % 10)}%)`;
  const hills2 = `hsl(${(hue + 190) % 360} 30% ${20 + (seed % 8)}%)`;
  const sunX = width * (0.15 + 0.7 * ((progress + (seed % 7) / 14) % 1));
  const sunY = height * (0.28 + 0.12 * Math.sin(progress * Math.PI));
  const ridge = (offset: number, amp: number, base: number) => {
    const points: string[] = [`0,${height}`];
    for (let x = 0; x <= width; x += width / 8) {
      const y = base + Math.sin((x / width) * 5 + offset + (seed % 11)) * amp;
      points.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    points.push(`${width},${height}`);
    return points.join(" ");
  };
  return `
    <defs><linearGradient id="g${seed}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${sky}"/><stop offset="1" stop-color="${sky2}"/>
    </linearGradient></defs>
    <rect width="${width}" height="${height}" fill="url(#g${seed})"/>
    <circle cx="${sunX.toFixed(1)}" cy="${sunY.toFixed(1)}" r="${height * 0.09}" fill="hsl(${(hue + 20) % 360} 90% 92%)" opacity="0.9"/>
    <polygon points="${ridge(0.4 + progress, height * 0.05, height * 0.66)}" fill="${hills}"/>
    <polygon points="${ridge(2.2 + progress * 2, height * 0.06, height * 0.8)}" fill="${hills2}"/>`;
}

function wrap(width: number, height: number, body: string): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">${body}</svg>`;
}

/** 16:9 (or custom ratio) poster frame. */
export function posterSvg(id: string, width = 640, height = 360): string {
  return wrap(width, height, scene(hash(id), 0.35, width, height));
}

/** Contact sheet of `cols` x `rows` frames; the scene drifts frame by frame so scrubbing is visible. */
export function spriteSvg(id: string, cols: number, rows: number, count: number): string {
  const frameW = 320;
  const frameH = 180;
  const seed = hash(id);
  const frames: string[] = [];
  for (let index = 0; index < count; index++) {
    const x = (index % cols) * frameW;
    const y = Math.floor(index / cols) * frameH;
    frames.push(
      `<svg x="${x}" y="${y}" width="${frameW}" height="${frameH}" viewBox="0 0 ${frameW} ${frameH}">${scene(seed, index / count, frameW, frameH)}</svg>`,
    );
  }
  return wrap(cols * frameW, rows * frameH, frames.join(""));
}

/** One keyframe thumbnail of a shot. */
export function keyframeSvg(id: string, index: number): string {
  return wrap(320, 180, scene(hash(id), (index % 10) / 10, 320, 180));
}
