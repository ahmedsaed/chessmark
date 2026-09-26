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
  /* **Cents only when the balance is whole cents.** Credit is spent at what each turn cost, and a
     decision model's turn costs five thousandths of a cent: a game of them took $1.00 to $0.9962
     and the header, rounding to cents, went on saying $1.00 — the one number that should move
     while you watch, standing still. */
  const wholeCents = Math.abs(size * 100 - Math.round(size * 100)) < 1e-9;
  const text = `$${size.toFixed(wholeCents ? 2 : 4)}`;
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
  // As typed, not rounded to cents: a limit under a cent rounded to "0.00", which the API refuses
  // as a limit that would end the game before it starts.
  const trimmed = text.trim();
  const value = Number(trimmed);
  return trimmed !== "" && Number.isFinite(value) && value > 0 ? trimmed : null;
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
  const difference = usd(String(Math.abs(billed - recorded)));
  const why =
    billed > recorded
      ? `${difference} of it is ${lost} request${lost === 1 ? "" : "s"} that a failure on our side lost from the game's record. They were billed all the same, so they are included.`
      : `That is ${difference} less than the game recorded as it played; the difference was refunded.`;
  return {
    usd: game.billed_usd,
    note: `OpenRouter billed ${usd(game.billed_usd)} for ${requests} request${requests === 1 ? "" : "s"}. ${why}`,
  };
}

/**
 * A game's or a seat's cost, precise enough that the numbers on one page add up.
 *
 * It was three places above a tenth of a cent, and a decision game's seats read "$0.002" and
 * "$0.001" over a total of "$0.004" — three true roundings that did not sum, on a page whose cost
 * is now what somebody is charged. Six places below a cent, four below a dollar, cents above.
 */
export function usd(value: string): string {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  if (amount === 0) return "$0.00";
  if (amount < 0.01) return `$${amount.toFixed(6)}`;
  return amount < 1 ? `$${amount.toFixed(4)}` : `$${amount.toFixed(2)}`;
}

/** What a game you paid for cost: the billed figure once reconciled, the running one before. */
export function costOf(game: { total_cost_usd: string; billed_usd?: string | null }): string {
  return game.billed_usd ?? game.total_cost_usd;
}

/** The total of games you paid for, each at `costOf`. */
export function spentOn(games: { total_cost_usd: string; billed_usd?: string | null }[]): number {
  return games.reduce((total, game) => total + Number(costOf(game)), 0);
}
