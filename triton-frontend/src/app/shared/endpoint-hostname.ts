/** Validate a hostname without accepting URLs, ports or paths. */
export function isEndpointHostname(value: string): boolean {
  const host = value.trim().toLowerCase().replace(/\.$/, "");
  return (
    !!host &&
    host.length <= 253 &&
    host.split(".").every((label) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))
  );
}
