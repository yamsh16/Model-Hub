import type { Metadata } from "next";
import ConnectView from "@/components/connect/ConnectView";
import { Container } from "@/components/ui";

export const metadata: Metadata = { title: "Attach a model · Model Hub" };

export default function ConnectPage() {
  return (
    <section className="bg-grey-bg py-12">
      <Container>
        <p className="mb-3 font-mono text-sm tracking-[0.12em] text-teal">ATTACH</p>
        <h1 className="mb-8 max-w-3xl text-3xl font-bold leading-tight sm:text-5xl">Bring your model and a test set.</h1>
        <ConnectView />
      </Container>
    </section>
  );
}
