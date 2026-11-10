export type TransferRule = { match: string; destination: string; announce: string; label: string };
export type Calendar = { id: string; name: string; description: string; timezone: string };
export type CatalogItem = { id: string; name: string; price: string; description: string; emoji: string };

export type Tenant = {
  id: string;
  business_name: string;
  kind: string;
  hue: number;
  emoji: string;
  transfer_rules: TransferRule[];
  calendars: Calendar[];
  catalog: CatalogItem[];
  try_saying: string[];
  open: boolean;
  local_time: string;
};

export type ToolName = "take_message" | "transfer_call" | "check_availability" | "book_appointment" | "show_item";
export type Params = Record<string, string>;

/** One invocation of a client tool. `result` is missing while it is running. */
export type ToolCall = { id: number; name: ToolName; params: Params; result?: string; ok?: boolean; ms?: number };

export type FeedItem =
  | { id: number; kind: "agent" | "user" | "sys"; text: string; live?: boolean }
  | { id: number; kind: "tool"; callId: number }
  | { id: number; kind: "step"; stepId: number };

/** The agent's workflow as it is in agents/receptionist/receptionist.json. */
export type WorkflowNode = { id: string; type: string; label: string; x: number; y: number; edge_order: string[]; tools: string[] };
export type WorkflowEdge = { id: string; source: string; target: string; gate: string | null; when: string | null; back: string | null };
export type Workflow = { nodes: WorkflowNode[]; edges: WorkflowEdge[] };

/** One move between nodes. `back` means the edge was followed against its direction. */
export type Step = { id: number; from: string; to: string; edge: string; back: boolean };
export type Vars = Record<string, string>;
