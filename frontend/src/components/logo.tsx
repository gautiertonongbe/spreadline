import { clsx } from "clsx";

/**
 * The mark.
 *
 * Two corners facing away from each other with a gold bar in the space between.
 * It borrows the bid and ask bracket from a trading screen, which is the nearest
 * respectable relative this product has: a price quoted on one side, a price
 * quoted on the other, and the only part worth anything sitting in the middle.
 *
 * Only the bar is gold, and that is the rule the whole interface follows. The
 * money is not in either price, it is in the gap between them, which is why gold
 * is never spent on a result anywhere else.
 *
 * Mitred joins and butt terminals, not round: this is a product that refuses to
 * round a number, gates a score to zero and says so when it cannot measure
 * something, and a softened mark would be the first thing that lies.
 *
 * **The two gaps are load-bearing.** This is a five-stroke mark where the others
 * considered had three, and the whole thing holds together on the clear space
 * between each bracket arm and the bar. Each gap is 2.6 units of a 32 unit
 * grid, which is about 1.3px at a 16px favicon: enough to survive rounding onto
 * a pixel. Lengthening an arm to "tidy up" the spacing closes that gap and the
 * mark fuses into a blot at small sizes. If it ever needs to sit below 16px,
 * draw a reduced version with the arms shortened further rather than scaling
 * this one down.
 */
export function LogoMark({
  size = 28,
  className,
  mono = false,
}: {
  size?: number;
  className?: string;
  /** Draw the whole mark in the current text colour, for tight or one-ink use. */
  mono?: boolean;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      role="img"
      aria-label="Spreadline"
      className={className}
    >
      <path
        d="M6 17 V8 H12"
        stroke="currentColor"
        strokeWidth="2.8"
        strokeLinejoin="miter"
      />
      <path
        d="M26 15 V24 H20"
        stroke="currentColor"
        strokeWidth="2.8"
        strokeLinejoin="miter"
      />
      <path
        d="M16 11.5 V20.5"
        stroke={mono ? "currentColor" : "rgb(var(--accent))"}
        strokeWidth="2.8"
        opacity={mono ? 0.55 : 1}
      />
    </svg>
  );
}

/** The mark with the name set beside it. */
export function Logo({ className }: { className?: string }) {
  return (
    <span className={clsx("flex items-center gap-2.5", className)}>
      <LogoMark size={26} className="shrink-0 text-primary" />
      <span className="min-w-0">
        <span className="display block text-[1.3125rem] font-medium leading-none tracking-[-0.015em] text-primary">
          Spreadline
        </span>
      </span>
    </span>
  );
}
