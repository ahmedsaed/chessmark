/**
 * What to call the person in the header.
 *
 * In `lib/` rather than in the component for the reason everything else here is: a component is
 * covered by the browser pass and nothing else, and the browser pass signs in as one account with
 * one shape of name. The interesting cases are the accounts that have *no* name — a Google sign-in
 * with only an email, a fresh email sign-up that never visited `/profile` — and those are reachable
 * from a unit test and from almost nowhere else.
 */

/** The fields of Clerk's user that bear on a name. Narrowed so this stays testable without Clerk. */
export interface Named {
  firstName?: string | null;
  lastName?: string | null;
  username?: string | null;
  primaryEmailAddress?: { emailAddress?: string | null } | null;
}

/**
 * The best name this account has, falling back until something is printable.
 *
 * `firstName` first because that is the field `/profile` writes — the display name a person chose
 * has to beat the one the provider guessed. Then the parts a social sign-in fills in, then the
 * local part of the email, and only then a constant.
 *
 * Never returns an empty string. A header that renders a nameless button is a header with an
 * invisible control in it, and the fallback costs one branch.
 */
export function displayNameOf(user: Named | null | undefined): string {
  if (!user) return "Account";

  const full = [user.firstName, user.lastName].filter(Boolean).join(" ").trim();
  if (full) return full;
  if (user.username?.trim()) return user.username.trim();

  const email = user.primaryEmailAddress?.emailAddress?.trim();
  if (email) {
    /* The local part, not the whole address. A header is not the place to publish somebody's email
       to whoever is looking over their shoulder, and `ahmed@example.com` is 17 characters of which
       11 are the same for everyone who signed up the same way. */
    const local = email.split("@")[0];
    if (local) return local;
  }

  return "Account";
}

/**
 * One or two letters for an avatar with no picture.
 *
 * Clerk serves a generated image for accounts with no upload, so this is the fallback *behind* a
 * fallback — it renders when the image itself fails to load, which is a blocked CDN or an offline
 * browser rather than a missing photo.
 */
export function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}
