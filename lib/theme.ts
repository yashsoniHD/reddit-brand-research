/**
 * The reader's light/dark choice.
 *
 * The app used to follow the operating system and offer no way to disagree
 * with it, which is fine until the one machine you read this on is set to a
 * theme you do not want this page in. So there are now two sources: the
 * operating system, which is still the default, and an explicit choice, which
 * wins when there is one.
 *
 * The resolved answer is written to `data-theme` on <html>, and every token in
 * globals.css hangs off that attribute rather than off a media query. Doing it
 * with an attribute is what makes the choice expressible at all: a media query
 * cannot be overridden from JavaScript, so as long as the palette was defined
 * inside `@media (prefers-color-scheme: dark)` there was nothing for a toggle
 * to change.
 */

export type Theme = "light" | "dark";

/**
 * Where the explicit choice is kept.
 *
 * Namespaced like the history key in lib/storage.ts, and deliberately NOT
 * per-user: the theme is a property of this browser rather than of whoever is
 * signed in, and signing out should not throw it away.
 */
export const THEME_STORAGE_KEY = "reddit-content-ideas:theme";

/**
 * The page background for each theme, for the `theme-color` meta tag.
 *
 * These MUST track `--background` in globals.css. A meta tag cannot read a CSS
 * variable, so this is the one place the two values are duplicated by hand;
 * it is the same duplication `viewport.themeColor` used to carry in
 * app/layout.tsx, moved here because the tag is now written by script.
 */
export const THEME_BACKGROUNDS: Record<Theme, string> = {
  light: "#f7f8fa",
  dark: "#0b0b0c",
};

function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark";
}

/** What the operating system asks for. Falls back to light where it cannot be asked. */
export function systemTheme(): Theme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/**
 * The explicit choice, or null when the reader has never made one.
 *
 * null is a real third state rather than a missing value: it is what keeps the
 * page following the operating system live, and it is the difference between
 * "they want light" and "their machine is light today".
 */
export function storedTheme(): Theme | null {
  if (typeof window === "undefined") return null;
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(stored) ? stored : null;
  } catch {
    // Storage can throw outright rather than return null (Safari private
    // browsing, a blocked third-party context). Unreadable reads as unset.
    return null;
  }
}

/** What the page is actually painted in right now, read back off <html>. */
export function activeTheme(): Theme {
  if (typeof document === "undefined") return "light";
  const attribute = document.documentElement.dataset.theme;
  return isTheme(attribute) ? attribute : systemTheme();
}

/**
 * Paint a theme.
 *
 * Both halves matter: the attribute repaints the page, and the meta tag
 * repaints the browser's own chrome around it. Skipping the second leaves a
 * phone with a black address bar above a white page, which is the seam this
 * value was introduced to close in the first place.
 */
export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", THEME_BACKGROUNDS[theme]);
}

/** Remember a choice. A failed write costs persistence, never the repaint. */
export function storeTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* See storedTheme: an unwritable store is not worth failing the toggle for. */
  }
}

/**
 * The script that resolves the theme before the first paint.
 *
 * Inlined into <head> by app/layout.tsx, ahead of every stylesheet and every
 * byte of <body>, because the alternative is the flash: the document defaults
 * to light, so a reader on dark would watch a white page repaint underneath
 * them on every single navigation. This has to be blocking and inline. A
 * module, a deferred script or a React effect all run too late by definition.
 *
 * It is also where the `theme-color` meta tag is created, rather than in
 * `viewport.themeColor`. Next's metadata API writes one tag per media query,
 * and a media query is exactly what an explicit choice has to be able to
 * overrule, so a reader who picks light on a dark machine would keep the dark
 * address bar. One tag, written to whatever was actually resolved, cannot
 * disagree with the page.
 *
 * Written by hand rather than compiled from the helpers above: it runs before
 * any bundle has loaded, so it can only use what the browser already has.
 */
export const THEME_BOOT_SCRIPT = `(function(){var t="light";try{var s=localStorage.getItem(${JSON.stringify(
  THEME_STORAGE_KEY,
)});t=(s==="light"||s==="dark")?s:(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light")}catch(e){try{t=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}catch(e2){}}document.documentElement.dataset.theme=t;var m=document.createElement("meta");m.name="theme-color";m.content=t==="dark"?${JSON.stringify(
  THEME_BACKGROUNDS.dark,
)}:${JSON.stringify(THEME_BACKGROUNDS.light)};document.head.appendChild(m)})()`;
