/**
 * Every outbound link in this app points at a URL that came from the bundle
 * (archive verified server side). Centralised so the rel and target rules
 * cannot be forgotten on one of them.
 */
export function ExternalLink({
  href,
  children,
  className,
}: {
  href: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={
        className ??
        "text-[var(--brand-ink)] underline decoration-[var(--brand)]/35 underline-offset-2 hover:decoration-[var(--brand)]"
      }
    >
      {children}
    </a>
  );
}
