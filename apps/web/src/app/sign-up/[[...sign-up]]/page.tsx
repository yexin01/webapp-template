import { SignUp } from "@clerk/nextjs";

export const metadata = { title: "Sign up" };

export default function SignUpPage() {
  return (
    <div className="auth-page">
      <SignUp />
    </div>
  );
}
