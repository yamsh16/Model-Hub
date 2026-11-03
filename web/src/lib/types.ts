// Shapes of the backend's JSON replies (see the pydantic models in ../../../backend/*.py).

export type User = { id: string; email: string };
export type AuthReply = { access_token: string; token_type: string; user: User };

export type Endpoint = { id: string; name: string; url: string; model: string; latency_ms: number };

export type ColumnInfo = { name: string; unique: number; missing: number };
export type TestSet = {
  id: string;
  filename: string;
  rows: number;
  columns: ColumnInfo[];
  target: string | null;
  classes: Record<string, number> | null;
};
export type Leakage = {
  train_rows: number;
  test_rows: number;
  overlap_rows: number;
  overlap_pct: number;
  duplicate_test_rows: number;
  warning: string | null;
};

export type ClassMetrics = { label: string; precision: number; recall: number; f1: number; support: number };
export type Evaluation = {
  n: number;
  classes: string[];
  accuracy: number;
  f1_macro: number;
  f1_weighted: number;
  roc_auc: number | null;
  per_class: ClassMetrics[];
  confusion_matrix: number[][];
  total_rows: number;
  sampled: boolean;
  unparsed: number;
  target: string;
};

export type TrainingLog = {
  id: string;
  filename: string;
  source: "csv" | "mlflow";
  x_name: string;
  x: number[];
  series: Record<string, (number | null)[]>;
  train_loss: string | null;
  val_loss: string | null;
};

export type Estimate = {
  series: string;
  x_name: string;
  model: "power" | "exponential" | null;
  n_points: number;
  last_x: number;
  r2: number | null;
  status: "already_flat" | "estimate" | "not_reached" | "rising" | "unreliable";
  remaining: number | null;
  remaining_low: number | null;
  remaining_high: number | null;
  high_unbounded: boolean;
  assumptions: string[];
  criterion: string;
  warnings: string[];
};

export type Readiness = {
  score: number | null;
  verdict: string;
  coverage: number;
  components: { name: string; label: string; value: number; weight: number; detail: string }[];
  advice: { priority: number; text: string }[];
  estimate: Estimate | null;
};

export type ChatMessage = { role: "user" | "assistant"; content: string };

export type Weights = {
  id: string;
  name: string;
  filename: string;
  format: "safetensors" | "gguf";
  size_bytes: number;
  sha256: string;
  tensor_count: number;
  parameter_count: number;
  dtypes: Record<string, number>;
  architecture: string | null;
  metadata: Record<string, string>;
  warnings: string[];
};
