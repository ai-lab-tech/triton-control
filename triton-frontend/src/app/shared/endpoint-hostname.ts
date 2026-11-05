/** User input is always one DNS label; the administrator supplies the domain. */
export function isEndpointHostname(value: string): boolean {
  return /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(
    value.trim().toLowerCase().replace(/\.$/, ""),
  );
}

export function resolvedEndpointHostname(value: string, baseDomain: string): string {
  const host = value.trim().toLowerCase().replace(/\.$/, "");
  return !host || !baseDomain ? host : host + "." + baseDomain;
}
