import { auth, currentUser } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";

/**
 * Defense-in-depth single-user gate. Clerk's dashboard allowlist is the
 * primary control; this is a second check in case the allowlist is
 * misconfigured. Every authenticated server route should call this.
 */
export async function requireAllowedUser() {
  const { userId } = await auth();
  if (!userId) redirect("/sign-in");

  const allowedEmail = process.env.ALLOWED_EMAIL;
  if (!allowedEmail) {
    throw new Error(
      "ALLOWED_EMAIL is not set. Configure it in Vercel and .env.local " +
        "(see SETUP.md).",
    );
  }

  const user = await currentUser();
  const email = user?.emailAddresses?.find(
    (e) => e.id === user?.primaryEmailAddressId,
  )?.emailAddress;

  if (!email || email.toLowerCase() !== allowedEmail.toLowerCase()) {
    redirect("/forbidden");
  }

  return { userId, email };
}
