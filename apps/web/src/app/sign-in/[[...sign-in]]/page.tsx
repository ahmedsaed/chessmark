import { Suspense } from "react";

import { AuthForm } from "@/components/AuthForm";

/* `useSearchParams` needs a Suspense boundary to prerender — without one Next refuses the build
   with "missing-suspense-with-csr-bailout", and the form reads `?redirect=`. */
export default function SignInPage() {
  return (
    <Suspense>
      <AuthForm mode="sign-in" />
    </Suspense>
  );
}
