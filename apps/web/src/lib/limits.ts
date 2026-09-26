/** The ply cap a game may be given, as the API accepts it (`CreateGameRequest.max_plies`). */
export const MIN_PLIES = 2;
export const MAX_PLIES = 1000;
export const DEFAULT_PLIES = 300;

/** A ply cap ready for the API: the typed number held to the accepted range, or the default. */
export function pliesFrom(text: string): number {
  const value = Math.round(Number(text));
  if (text.trim() === "" || !Number.isFinite(value)) return DEFAULT_PLIES;
  return Math.min(MAX_PLIES, Math.max(MIN_PLIES, value));
}
