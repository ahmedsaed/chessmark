/**
 * A model's name, as short as a card can say it and still mean the same model.
 *
 * The registry's display names carry two things a card has no room for: the vendor, as a
 * `Vendor: ` prefix, and the price tier, as a ` (free)` suffix. Production's pool is almost
 * entirely free models, so `NVIDIA: Nemotron 3 Ultra (free)` spent fifteen of a row's thirty
 * characters saying what every other row also said, and the archive's card cut every game to
 * `nemotron-3-ultra-550b-a55b:...` — which named neither opponent. What is left is the part that
 * tells one model from another.
 *
 * A name with no prefix or suffix comes back as it was, so a person's name is untouched.
 */
export function shortName(name: string): string {
  const short = name
    .replace(/^[^:]{1,40}:\s+/, "")
    .replace(/\s*\((free|beta|preview)\)\s*$/i, "")
    .replace(/\s{2,}/g, " ")
    .trim();
  return short || name.trim();
}
