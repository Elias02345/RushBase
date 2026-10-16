export type Os = "windows" | "macos" | "linux";

/** The visitor's operating system, used to preselect the matching file path. */
export function detectOs(agent: string = navigator.userAgent): Os {
  if (/Windows/i.test(agent)) return "windows";
  if (/Mac|iPhone|iPad/i.test(agent)) return "macos";
  return "linux";
}

/** True on Apple platforms, where the command key replaces Ctrl in shortcuts. */
export function isApple(agent: string = navigator.userAgent): boolean {
  return /Mac|iPhone|iPad/i.test(agent);
}

/** Copy text to the clipboard; resolves false when the browser refuses. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      area.remove();
    }
  }
}

/** The worker CLI call that redeems an enrollment token (see `rushbase enroll --help`). */
export function enrollCommand(token: string): string {
  return `rushbase enroll --server ${window.location.origin} --token ${token} --name my-worker`;
}
