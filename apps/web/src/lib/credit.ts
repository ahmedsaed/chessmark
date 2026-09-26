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

/** A limit ready for the API: a positive amount, or `null` for none. */
export function limitFrom(text: string): string | null {
  const value = Number(text);
  return text.trim() !== "" && Number.isFinite(value) && value > 0 ? value.toFixed(2) : null;
}

/**
 * The game's cost as the page states it, and what to say when it moved (ADR-0054).
 *
 * While it plays, the cost is what each turn recorded. Once it has been reconciled — it ended, or
 * its owner or its credit is holding it — it is what OpenRouter billed, which is what its owner is
 * charged. The two differ only by requests a failure of ours lost from the record, and when they do
 * the page says so rather than letting a number change without a word.
 */
export function gameCost(game: {
  total_cost_usd: string;
  billed_usd?: string | null;
  billed_requests?: number | null;
  unrecorded_requests?: number | null;
}): { usd: string; note: string | null } {
  if (game.billed_usd === null || game.billed_usd === undefined) {
    return { usd: game.total_cost_usd, note: null };
  }
  const billed = Number(game.billed_usd);
  const recorded = Number(game.total_cost_usd);
  if (Math.abs(billed - recorded) < 0.00000001) return { usd: game.billed_usd, note: null };

  const lost = game.unrecorded_requests ?? 0;
  const requests = game.billed_requests ?? 0;
  const difference = formatBalance(Math.abs(billed - recorded));
  const why =
    billed > recorded
      ? `${difference} of it is ${lost} request${lost === 1 ? "" : "s"} that a failure on our side lost from the game's record. They were billed all the same, so they are included.`
      : `That is ${difference} less than the game recorded as it played; the difference was refunded.`;
  return {
    usd: game.billed_usd,
    note: `OpenRouter billed ${formatBalance(billed)} for ${requests} request${requests === 1 ? "" : "s"}. ${why}`,
  };
}

/** A game's or a seat's cost, to the precision a single game needs: a tenth of a cent, or six
 *  places for the calls that cost less than that. */
export function usd(value: string): string {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  if (amount === 0) return "$0.000";
  return amount < 0.001 ? `$${amount.toFixed(6)}` : `$${amount.toFixed(3)}`;
}
