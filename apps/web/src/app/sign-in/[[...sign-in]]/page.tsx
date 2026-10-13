import { SignIn } from "@clerk/nextjs";

export const metadata = { title: "Sign in" };

export default function SignInPage() {
  return (
    <div className="auth-page">
      <SignIn />
    </div>
  );
}
