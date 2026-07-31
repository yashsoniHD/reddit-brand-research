import { FlaskConical, Info, TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * One icon-led tinted banner for the whole app.
 *
 * There were three of these, built independently and drifting: the report's
 * local info/warning pair, a hand-inlined copy of its info branch for the
 * sample banner, and the error banner on its own metrics (`gap-3 px-4 py-3.5`,
 * a `mt-0.5` icon, `text-sm`). Same shape, three sets of numbers, so two
 * banners stacked on one page never lined up. The metrics below are the single
 * set; a tone picks the icon and the tint and nothing else.
 */
const TONES = {
  info: {
    Icon: Info,
    plate: "border-[var(--brand-line)] bg-[var(--brand-soft)]",
    icon: "text-[var(--brand-ink)]",
  },
  /** The sample banner is an info note wearing a different icon, not its own thing. */
  sample: {
    Icon: FlaskConical,
    plate: "border-[var(--brand-line)] bg-[var(--brand-soft)]",
    icon: "text-[var(--brand-ink)]",
  },
  warning: {
    Icon: TriangleAlert,
    plate: "border-destructive/30 bg-destructive/8",
    icon: "text-destructive",
  },
  /** Same paint as warning: the difference an error carries is `role="alert"`,
   *  which is the caller's to pass, not a second red. */
  error: {
    Icon: TriangleAlert,
    plate: "border-destructive/30 bg-destructive/8",
    icon: "text-destructive",
  },
} as const;

export type CalloutTone = keyof typeof TONES;

export function Callout({
  tone,
  role,
  className,
  children,
}: {
  tone: CalloutTone;
  /** `role="alert"` for the error case. Left to the caller because an alert
   *  interrupts a screen reader, and only the caller knows whether this banner
   *  appeared in response to something the user just did. */
  role?: React.AriaRole;
  className?: string;
  children: React.ReactNode;
}) {
  const { Icon, plate, icon } = TONES[tone];

  return (
    <div
      role={role}
      className={cn(
        "t-small flex items-start gap-2.5 rounded-xl border px-4 py-3 text-foreground",
        plate,
        className,
      )}
    >
      {/* An icon as well as a tint, so the tone survives greyscale. */}
      <Icon aria-hidden="true" strokeWidth={2} className={cn("mt-px size-4 shrink-0", icon)} />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
