/**
 * What to call somebody in the header.
 *
 * The browser suite signs in as one account with one shape of name, so every case below except the
 * first is unreachable from it. They are the ordinary ones: a Google sign-in that filled in a name,
 * an email sign-up that filled in nothing, an account whose display name has not been set.
 */

import { describe, expect, it } from "vitest";

import { displayNameOf, initialsOf } from "@/lib/identity";

describe("displayNameOf", () => {
  it("prefers the display name a person chose over anything the provider guessed", () => {
    /* `/profile` writes `firstName` — a name somebody typed has to beat one Google supplied,
       or editing the field does nothing visible and the page looks broken. */
    expect(
      displayNameOf({
        firstName: "Ada",
        username: "ada_l",
        primaryEmailAddress: { emailAddress: "ada@example.com" },
      }),
    ).toBe("Ada");
  });

  it("joins the two name fields a social sign-in fills", () => {
    expect(displayNameOf({ firstName: "Ada", lastName: "Lovelace" })).toBe("Ada Lovelace");
  });

  it("falls back to a username when there is no name", () => {
    expect(displayNameOf({ username: "ada_l" })).toBe("ada_l");
  });

  it("falls back to the local part of the email, never the whole address", () => {
    /* The header is read over somebody's shoulder. The domain is also the same for everyone who
       signed up the same way, so it is the half that carries no information. */
    expect(displayNameOf({ primaryEmailAddress: { emailAddress: "ada@example.com" } })).toBe("ada");
  });

  it("never returns an empty string", () => {
    // A nameless button is an invisible control. Every one of these is an account that exists.
    expect(displayNameOf({})).toBe("Account");
    expect(displayNameOf(null)).toBe("Account");
    expect(displayNameOf({ firstName: "   ", username: "", primaryEmailAddress: null })).toBe(
      "Account",
    );
  });

  it("ignores an email with nothing before the @", () => {
    expect(displayNameOf({ primaryEmailAddress: { emailAddress: "@example.com" } })).toBe("Account");
  });
});

describe("initialsOf", () => {
  it("takes the first and last initial of a full name", () => {
    expect(initialsOf("Ada Lovelace")).toBe("AL");
    expect(initialsOf("Ada Byron King Lovelace")).toBe("AL");
  });

  it("takes two letters from a single name", () => {
    expect(initialsOf("ada")).toBe("AD");
  });

  it("survives a name that is one letter, or none", () => {
    // The fallback behind a fallback: it renders when Clerk's generated image fails to load.
    expect(initialsOf("a")).toBe("A");
    expect(initialsOf("   ")).toBe("?");
  });
});
