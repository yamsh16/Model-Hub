"use client";

import { useRef, useState } from "react";
import { errorMessage } from "@/lib/format";
import { useAuth } from "./AuthProvider";
import { Button, Field, inputClass, Status, type StatusMessage } from "./ui";

/** Header sign-in / sign-out controls, plus the sign-in dialog they open. */
export default function AuthControls() {
  const { user, ready, signIn, register, signOut } = useAuth();
  const dialog = useRef<HTMLDialogElement>(null);
  const [mode, setMode] = useState<"signin" | "register">("signin");
  const [status, setStatus] = useState<StatusMessage>(null);
  const [busy, setBusy] = useState(false);
  const isRegister = mode === "register";

  function open() {
    setMode("signin");
    setStatus(null);
    dialog.current?.showModal();
  }

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setStatus(null);
    try {
      const action = isRegister ? register : signIn;
      await action(String(form.get("email")), String(form.get("password")));
      dialog.current?.close();
    } catch (err) {
      setStatus({ text: errorMessage(err), kind: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex items-center gap-2.5">
      {user ? (
        <>
          <span className="font-mono text-[0.85rem]">{user.email}</span>
          <Button variant="outline" className="!px-3.5 !py-2" onClick={signOut}>
            Sign out
          </Button>
        </>
      ) : (
        ready && (
          <Button variant="outline" className="!px-3.5 !py-2" onClick={open}>
            Sign in
          </Button>
        )
      )}

      <dialog
        ref={dialog}
        aria-labelledby="auth-title"
        className="m-auto w-[min(420px,92vw)] rounded-[18px] border border-line p-7 backdrop:bg-ink/50"
      >
        {/* key: switching sign-in / create-account starts with empty fields */}
        <form key={mode} onSubmit={submit}>
          <h2 id="auth-title" className="mb-4 text-2xl font-bold">
            {isRegister ? "Create an account" : "Sign in"}
          </h2>
          <Field label="Email" htmlFor="auth-email">
            <input id="auth-email" name="email" type="text" autoComplete="email" required className={inputClass} />
          </Field>
          <Field label="Password" hint={isRegister ? "(at least 10 characters)" : undefined} htmlFor="auth-password">
            <input
              id="auth-password"
              name="password"
              type="password"
              autoComplete={isRegister ? "new-password" : "current-password"}
              required
              className={inputClass}
            />
          </Field>
          <Status message={status} />
          <div className="mt-4 flex flex-wrap gap-2">
            <Button type="submit" disabled={busy}>
              {isRegister ? "Create account" : "Sign in"}
            </Button>
            <Button variant="outline" onClick={() => setMode(isRegister ? "signin" : "register")}>
              {isRegister ? "I have an account" : "Create an account"}
            </Button>
            <Button variant="outline" onClick={() => dialog.current?.close()}>
              Cancel
            </Button>
          </div>
        </form>
      </dialog>
    </span>
  );
}
