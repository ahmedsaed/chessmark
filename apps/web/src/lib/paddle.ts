/**
 * Paddle's browser configuration, read once and refused when it is half there (ADR-0055).
 *
 * **No token means selling is off** — `/credit` says so and loads no Paddle at all. **A token without
 * its environment is an error, not a default.** The token is `test_…` for Paddle's sandbox and
 * `live_…` for production, and Paddle.js pointed at the wrong one fails at checkout in front of a
 * buyer; guessing "sandbox" would ship a store that takes no money, guessing "production" one whose
 * test purchases are real. So the build fails and says which variable is missing.
 */

export type PaddleEnvironment = "sandbox" | "production";

export interface PaddleConfig {
  token: string;
  environment: PaddleEnvironment;
}

export function paddleConfigFrom(
  token: string | undefined,
  environment: string | undefined,
): PaddleConfig | null {
  if (!token) return null;
  if (environment !== "sandbox" && environment !== "production") {
    throw new Error(
      `NEXT_PUBLIC_PADDLE_ENV must be "sandbox" or "production" when NEXT_PUBLIC_PADDLE_CLIENT_TOKEN ` +
        `is set (it is ${environment ? `"${environment}"` : "unset"}). Refusing to guess which ` +
        "Paddle account to send buyers to.",
    );
  }
  const expected = environment === "sandbox" ? "test_" : "live_";
  if (!token.startsWith(expected)) {
    throw new Error(
      `NEXT_PUBLIC_PADDLE_CLIENT_TOKEN is not a ${environment} token (those start "${expected}").`,
    );
  }
  return { token, environment };
}

/* Read as literals so Next inlines them into the client bundle; `process.env[name]` would not be. */
export const paddleConfig = paddleConfigFrom(
  process.env.NEXT_PUBLIC_PADDLE_CLIENT_TOKEN,
  process.env.NEXT_PUBLIC_PADDLE_ENV,
);
