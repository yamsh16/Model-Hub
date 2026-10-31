import ApiStatus from "@/components/ApiStatus";

export default function Home() {
  return (
    <section className="bg-grey-bg">
      <div className="mx-auto max-w-[1200px] px-6 py-24">
        <p className="font-mono text-sm text-teal">AI / ML / LLM MODEL ANALYZER</p>
        <h1 className="mt-4 max-w-3xl text-5xl font-bold leading-tight">
          Attach your model. Know exactly where it stands.
        </h1>
        <p className="mt-6 max-w-xl text-lg text-muted">
          This is the new Next.js app. The pages move over from the static site on Days 23 and 24;
          until then the working app is still <code className="font-mono">frontend/</code>.
        </p>
        <div className="mt-8">
          <ApiStatus />
        </div>
      </div>
    </section>
  );
}
