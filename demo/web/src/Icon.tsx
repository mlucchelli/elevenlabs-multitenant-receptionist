const PATHS = {
  phone: "M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2Z",
  mic: "M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm6-3a6 6 0 0 1-12 0M12 18v3",
  chat: "M4 5h16v11H9l-5 4V5Z",
  send: "M5 12h14M13 6l6 6-6 6",
  inbox: "M4 13l2.5-8h11L20 13v6H4v-6Zm0 0h5a3 3 0 0 0 6 0h5",
  calendar: "M5 6h14v14H5V6Zm0 4h14M9 3v4m6-4v4",
  route: "M6 6h5a3 3 0 0 1 3 3v6a3 3 0 0 0 3 3h1M6 6a2 2 0 1 0 0 .01M18 18a2 2 0 1 0 0 .01",
  screen: "M4 5h16v11H4V5Zm5 15h6m-3-4v4",
  bolt: "M14 3l-2 7h6l-8 11 2-8H6l8-10Z",
  braces: "M9 4c-2 0-3 1-3 3v2c0 1.5-1 3-2 3 1 0 2 1.5 2 3v2c0 2 1 3 3 3m6-16c2 0 3 1 3 3v2c0 1.5 1 3 2 3-1 0-2 1.5-2 3v2c0 2-1 3-3 3",
  check: "M5 12.5l4.5 4.5L19 7.5",
  lock: "M7 11V8a5 5 0 0 1 10 0v3m-11 0h12v9H6v-9Z",
  down: "M6 9l6 6 6-6",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}
