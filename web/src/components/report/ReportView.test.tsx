import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AuthProvider from "../AuthProvider";
import WorkspaceProvider from "../WorkspaceProvider";
import { WORKSPACE_KEY } from "@/lib/storage";
import ReportView from "./ReportView";

const readiness = {
  score: 72,
  verdict: "Promising, a few gaps",
  coverage: 0.6,
  components: [{ name: "accuracy", label: "Accuracy", value: 0.9, weight: 0.4, detail: "90% on 20 rows" }],
  advice: [{ priority: 1, text: "Collect more examples of class b." }],
  estimate: null,
};

function renderReport() {
  return render(
    <AuthProvider>
      <WorkspaceProvider>
        <ReportView />
      </WorkspaceProvider>
    </AuthProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

describe("ReportView", () => {
  it("shows a sample report when nothing is loaded, without calling the backend", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderReport();
    expect(screen.getByText("SAMPLE REPORT")).toBeTruthy();
    expect(screen.getByText("– / 100")).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the saved evaluation and the server's readiness score", async () => {
    const evaluation = {
      n: 20, classes: ["a", "b"], accuracy: 0.9, f1_macro: 0.88, f1_weighted: 0.9, roc_auc: null,
      per_class: [
        { label: "a", precision: 1, recall: 0.8, f1: 0.89, support: 10 },
        { label: "b", precision: 0.8, recall: 1, f1: 0.89, support: 10 },
      ],
      confusion_matrix: [[8, 2], [0, 10]], total_rows: 20, sampled: false, unparsed: 0, target: "label",
    };
    sessionStorage.setItem(
      WORKSPACE_KEY,
      JSON.stringify({ testset: { id: "t1", filename: "t.csv", rows: 20, columns: [], target: "label", classes: {} }, evaluation }),
    );
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(readiness), { status: 200 })));

    renderReport();

    expect(await screen.findByText("90.0%")).toBeTruthy();
    expect(await screen.findByText("72 / 100")).toBeTruthy();
    expect(screen.getByText("Collect more examples of class b.")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("YOUR REPORT")).toBeTruthy());
    expect(screen.getByRole("table", { name: "Confusion matrix" })).toBeTruthy();
  });
});
