import type { ButtonHTMLAttributes, ReactNode } from "react";

// Small building blocks shared by every page, so the look is set in one place.

const BUTTON_BASE =
  "inline-flex items-center justify-center rounded-lg border border-transparent px-5 py-3 font-semibold transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";

export const buttonClass = {
  dark: `${BUTTON_BASE} bg-ink text-white`,
  teal: `${BUTTON_BASE} bg-teal text-white`,
  outline: `${BUTTON_BASE} border-ink bg-transparent text-ink`,
};

export function Button({
  variant = "dark",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof buttonClass }) {
  return <button type="button" className={`${buttonClass[variant]} ${className}`} {...props} />;
}

export const inputClass =
  "w-full min-w-0 rounded-lg border border-[#b8bfc9] bg-white px-3.5 py-3 text-base text-ink focus:outline-2 focus:outline-offset-1 focus:outline-teal";

export function Field({ label, hint, htmlFor, children }: { label: string; hint?: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className="mt-3.5 first:mt-0">
      <label htmlFor={htmlFor} className="mb-2 block text-[0.95rem] font-semibold">
        {label} {hint && <span className="font-normal text-muted">{hint}</span>}
      </label>
      {children}
    </div>
  );
}

export type StatusMessage = { text: string; kind?: "error" | "success" } | null;

/** One line of feedback under a form. */
export function Status({ message }: { message: StatusMessage }) {
  if (!message?.text) return null;
  const color = message.kind === "error" ? "text-[#b3261e]" : message.kind === "success" ? "text-teal" : "";
  return (
    <p role={message.kind === "error" ? "alert" : "status"} className={`mt-3.5 text-sm ${color}`}>
      {message.text}
    </p>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-[18px] bg-white p-7 shadow-[0_4px_24px_rgba(15,27,43,0.06)] ${className}`}>{children}</div>;
}

export function Panel({ title, children, className = "" }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-md border border-line p-6 ${className}`}>
      {title && <h3 className="mb-4 text-[1.05rem] font-semibold">{title}</h3>}
      {children}
    </div>
  );
}

export function Container({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`mx-auto max-w-[1200px] px-6 ${className}`}>{children}</div>;
}

/** A table of text cells; the first row is the header. Cells are plain text because class
 *  labels come from the user's data. */
export function DataTable({ header, rows, label }: { header: string[]; rows: (string | number)[][]; label: string }) {
  const cell = "border border-black/10 px-2.5 py-1.5 text-right first:text-left first:font-semibold";
  return (
    <div className="overflow-x-auto">
      <table aria-label={label} className="w-full border-collapse text-[0.9rem]">
        <thead>
          <tr>
            {header.map((h, i) => (
              <th key={i} className={cell}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((c, j) => (
                <td key={j} className={cell}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
