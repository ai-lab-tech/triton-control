/** User input is always one DNS label; the administrator supplies the domain. */
export function isEndpointHostname(value: string): boolean {
  return /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(
    value.trim().toLowerCase().replace(/\.$/, ""),
  );
}

export function resolvedEndpointHostname(
  value: string,
  baseDomain: string,
  existingHost?: string | null,
): string {
  const host = value.trim().toLowerCase().replace(/\.$/, "");
  if (existingHost && host === existingHost.split(".")[0]) return existingHost;
  return !host || !baseDomain ? host : host + "." + baseDomain;
}
