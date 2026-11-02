import Link from "next/link";
import ApiStatus from "@/components/ApiStatus";
import { buttonClass, Container } from "@/components/ui";

const STEPS = [
  { n: "01", title: "Attach", text: "Connect an OpenAI-compatible API endpoint and upload a test set." },
  { n: "02", title: "Analyze", text: "Get training progress, accuracy and a readiness score." },
  { n: "03", title: "Chat and improve", text: "Talk to the model and follow the advice on what to train next." },
];

export default function Home() {
  return (
    <>
      <section className="bg-grey-bg py-16 sm:py-24">
        <Container>
          <p className="mb-4 font-mono text-sm tracking-[0.12em] text-teal">AI / ML / LLM MODEL ANALYZER</p>
          <h1 className="max-w-3xl text-4xl font-bold leading-tight sm:text-6xl">
            Attach your model. Know exactly where it stands.
          </h1>
          <p className="mt-6 max-w-xl text-lg text-muted">
            Bring any trained model. Get a clear report on how much it has trained, how accurate it is, and how much
            more training it needs. Then talk to it right here.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/connect" className={buttonClass.teal}>
              Attach a model
            </Link>
            <Link href="/report" className={buttonClass.outline}>
              See the report
            </Link>
          </div>
          <div className="mt-10">
            <ApiStatus />
          </div>
        </Container>
      </section>

      <section id="how-it-works" className="scroll-mt-16 bg-ink py-16 text-white">
        <Container>
          <h2 className="mb-9 text-3xl font-bold">How it works</h2>
          <div className="grid gap-10 sm:grid-cols-3">
            {STEPS.map((s) => (
              <div key={s.n}>
                <p className="mb-1.5 font-mono text-[#5fd3cb]">{s.n}</p>
                <h3 className="mb-2 text-xl font-semibold">{s.title}</h3>
                <p className="text-[#c9d0da]">{s.text}</p>
              </div>
            ))}
          </div>
        </Container>
      </section>
    </>
  );
}
