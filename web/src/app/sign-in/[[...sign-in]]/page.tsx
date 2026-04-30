import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
  return (
    <main className="flex min-h-svh items-center justify-center p-6">
      <SignIn
        appearance={{
          elements: {
            footer: { display: "none" },
          },
        }}
      />
    </main>
  );
}
