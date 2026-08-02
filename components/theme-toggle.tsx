"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import {
  activeTheme,
  applyTheme,
  storeTheme,
  storedTheme,
  systemTheme,
  type Theme,
} from "@/lib/theme";

/**
 * Light or dark, said in the header.
 *
 * Two states rather than three. A "system" option is the more complete answer
 * and it is the wrong one for a control this small: it needs a third icon
 * nobody recognises, and it spends a header slot explaining a default that is
 * already in force for everyone who has never touched this. Following the
 * operating system is what the page does until this is pressed, and pressing
 * it is the reader saying they want something else.
 *
 * Which half is lit is painted by CSS off `data-theme` (see `.theme-choice` in
 * globals.css), not by the state below. The state exists for `aria-pressed`,
 * which is the one thing a selector cannot set.
 */

const CHOICES = [
  { value: "light", label: "Light theme", Icon: Sun },
  { value: "dark", label: "Dark theme", Icon: Moon },
] as const satisfies readonly { value: Theme; label: string; Icon: typeof Sun }[];

export function ThemeToggle() {
  /**
   * Seeded light and corrected on mount, because the server has no way to know
   * and guessing on it would be a hydration mismatch. Only `aria-pressed` is
   * wrong in that first frame, and only until hydration, which is the same
   * moment the button becomes pressable at all. The visible state is never
   * wrong, because it is not coming from here.
   */
  const [theme, setTheme] = useState<Theme>("light");

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    setTheme(activeTheme());
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  /**
   * Keep following the operating system, but only while the reader has not
   * disagreed with it.
   *
   * Without this, a machine that switches to dark at sunset would leave this
   * page light until it is reloaded. With it, an explicit choice is still
   * final: `storedTheme()` is re-read inside the handler rather than captured,
   * so a choice made after this effect ran is seen by it.
   */
  useEffect(() => {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const follow = () => {
      if (storedTheme() !== null) return;
      const next = systemTheme();
      applyTheme(next);
      setTheme(next);
    };
    query.addEventListener("change", follow);
    return () => query.removeEventListener("change", follow);
  }, []);

  const choose = (next: Theme) => {
    applyTheme(next);
    storeTheme(next);
    setTheme(next);
  };

  return (
    /* A group rather than a radiogroup. Radio semantics would promise arrow-key
       navigation between the two and a single tab stop, which is a keyboard
       contract this does not implement; two plain toggle buttons promise
       exactly what they do. */
    <div
      role="group"
      aria-label="Theme"
      className="flex items-center gap-0.5 rounded-full border bg-card p-0.5"
    >
      {CHOICES.map(({ value, label, Icon }) => (
        <button
          key={value}
          type="button"
          // Read by the `.theme-choice` selectors in globals.css to decide
          // which half is lit, against `data-theme` on <html>.
          data-theme-value={value}
          aria-pressed={theme === value}
          aria-label={label}
          onClick={() => choose(value)}
          className="theme-choice"
        >
          <Icon aria-hidden="true" strokeWidth={2} className="size-3.5" />
        </button>
      ))}
    </div>
  );
}
