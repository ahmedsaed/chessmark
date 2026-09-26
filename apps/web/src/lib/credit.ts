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
