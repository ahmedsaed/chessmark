import type { Metadata } from "next";

import { ProfileView } from "@/components/ProfileView";
import { apiUrl } from "@/lib/api";
import { pageMetadata } from "@/lib/site";

/**
 * The account page — and the replacement for Clerk's `<UserButton />`.
 *
 * `UserButton` was the last prebuilt Clerk component in the global header, and a prebuilt
 * component anywhere is what forces the `@clerk/ui` bundle onto **every** route: 285 KiB, on
 * `/about` and `/leaderboard` as much as here. `prefetchUI={false}` is documented as being *"for
 * custom UIs using Control Components"* and throws without lazy fallback if any prebuilt component
 * renders, so the saving is not a flag — it is the reward for owning these screens.
 *
 * It is also a better page than the modal it replaces. `UserButton` knew nothing about credits,
 * spend or games, which are the three things a person actually comes here to check.
 */
export const metadata: Metadata = pageMetadata({
  title: "Profile",
  description: "Your account, your allowance, and what you have spent today.",
  path: "/profile",
});

export default function ProfilePage() {
  return <ProfileView apiUrl={apiUrl} />;
}
