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
  | { id: number; kind: "tool"; callId: number };

export type WorkflowNode = "triage" | "transfer" | "schedule" | "end";
export type Vars = Record<string, string>;
