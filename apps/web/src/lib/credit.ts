/**
 * A credit balance, as a person reads it (ADR-0052).
 *
 * Dollars, because that is what a balance is now: each turn is charged what it actually cost, and
 * a unit in between would only hide the number. Cents, as money is usually written — except for a
 * balance under a cent either side of zero, which rounds to `$0.00` and would read as empty while
 * it is not. A game can take a balance a turn's cost below zero, and that is said as it is.
 */
export function formatBalance(usd: string | number): string {
  const value = Number(usd);
  if (!Number.isFinite(value)) return "—";

  const size = Math.abs(value);
  const digits = size > 0 && size < 0.01 ? 4 : 2;
  const text = `$${size.toFixed(digits)}`;
  return value < 0 ? `−${text}` : text;
}

/** Whether this balance can start or continue a paid game: anything above zero. */
export function canPay(usd: string | number): boolean {
  return Number(usd) > 0;
}

/**
 * The signal that a balance has probably moved, so the header should read it again.
 *
 * **Not a meter.** Credit is spent turn by turn (ADR-0052), and polling `/me` from every open page
 * would ask a question whose answer changes only when a game the reader pays for plays a model
 * turn. So the page that can see that happen says so, and nothing else asks: a window event
 * rather than shared state, because the header lives in the root layout and the game in the page
 * under it, and neither should have to know the other exists.
 */
export const BALANCE_MAY_HAVE_CHANGED = "chessmark:balance-may-have-changed";

export function announceSpend(): void {
  window.dispatchEvent(new Event(BALANCE_MAY_HAVE_CHANGED));
}

/**
 * Whether the move that just landed cost the viewer anything: a new ply, in a game they pay for,
 * made by a model rather than by them. Their own move is free, so it asks nothing.
 */
export function modelMoveCharged(options: {
  pays: boolean;
  before: number;
  after: number;
  mover: "white" | "black" | null;
  seat: "white" | "black" | undefined;
}): boolean {
  const { pays, before, after, mover, seat } = options;
  return pays && after > before && mover !== null && mover !== seat;
}
