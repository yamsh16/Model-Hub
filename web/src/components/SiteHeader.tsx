import Link from "next/link";

// Same links as the static site's header. Sign-in controls arrive with the page migration.
const LINKS = [
  { href: "/#how-it-works", label: "How it works" },
  { href: "/#report", label: "Model report" },
  { href: "/#chat", label: "Chat" },
];

export default function SiteHeader() {
  return (
    <header className="sticky top-0 z-10 border-b border-line bg-white/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1200px] items-center justify-between px-6 py-4">
        <Link href="/" className="flex items-center gap-2 text-lg font-semibold">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="12" cy="12" r="10" />
            <circle cx="12" cy="12" r="3" />
            <path d="M12 2v7M12 15v7M2 12h7M15 12h7" />
          </svg>
          Model Hub
        </Link>
        <nav className="flex items-center gap-6 text-sm">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} className="text-ink hover:text-teal">
              {l.label}
            </Link>
          ))}
          <Link href="/#attach" className="rounded-md bg-ink px-4 py-2 font-medium text-white hover:opacity-90">
            Attach a model
          </Link>
        </nav>
      </div>
    </header>
  );
}
