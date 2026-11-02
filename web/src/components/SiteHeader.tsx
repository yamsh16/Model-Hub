import Link from "next/link";
import AuthControls from "./AuthControls";
import { buttonClass, Container } from "./ui";

const LINKS = [
  { href: "/#how-it-works", label: "How it works" },
  { href: "/report", label: "Model report" },
  { href: "/chat", label: "Chat" },
];

export default function SiteHeader() {
  return (
    <header className="sticky top-0 z-10 border-b border-line bg-white/90 backdrop-blur">
      <Container className="flex items-center justify-between py-4">
        <Link href="/" className="flex items-center gap-2 text-lg font-bold">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true" className="text-teal">
            <circle cx="12" cy="12" r="10" />
            <circle cx="12" cy="12" r="3" />
            <path d="M12 2v7M12 15v7M2 12h7M15 12h7" />
          </svg>
          Model Hub
        </Link>
        <nav className="flex items-center gap-6 text-sm">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} className="hidden text-ink hover:text-teal sm:inline">
              {l.label}
            </Link>
          ))}
          <Link href="/connect" className={`${buttonClass.dark} !px-4 !py-2 text-sm`}>
            Attach a model
          </Link>
          <AuthControls />
        </nav>
      </Container>
    </header>
  );
}
